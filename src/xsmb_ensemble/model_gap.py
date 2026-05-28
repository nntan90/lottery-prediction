"""
model_gap.py — Model B: Weekday-Specific Gap/Overdue Scoring (XSMB v4)

Tối ưu cho XSMB daily data:
  - Weekday-specific gap stats: avg_gap tính trên cùng weekday
    (Pair X trung bình 8 kỳ thứ 2 mới nổ vs 5 kỳ thứ 6)
  - Gap percentile: gap hiện tại so với historical distribution
    (95th percentile gap = extreme overdue)
  - Cluster gap detection: decade nhóm (00-09) cùng nén → signal mạnh
  - Expected value: E(gap) adjusted by recent trend

Scoring formula (XSMB v4):
  Score = 0.25 × gap_zscore_norm          ← z-score overdue
        + 0.20 × weekday_gap_ratio_norm   ← gap vs avg gap cùng thứ
        + 0.15 × gap_percentile_norm      ← percentile trong lịch sử
        + 0.15 × consecutive_miss_norm    ← chuỗi vắng mặt liên tiếp
        + 0.10 × cluster_gap_norm         ← decade cùng nén
        + 0.15 × inverse_recent_freq      ← freq_7 thấp = đang nén
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional

from src.xsmb_ensemble.data_utils import (
    _load_tails_by_draws,
    _load_tails_by_weekday,
    compute_pair_appeared_matrix,
)


def predict_gap(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model B: Weekday-specific gap/overdue scoring cho XSMB.

    Phát hiện số có gap cao bất thường, đặc biệt khi tính theo
    weekday-specific statistics. XSMB xổ daily nên weekday context
    rất quan trọng cho pattern analysis.

    Args:
        db: LotteryDB instance
        province: None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ quay lookback
        top_n: số cặp top-N output
        region: always 'XSMB'

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()

    try:
        # Load chung
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 15:
            return _error_result("gap_overdue", province, n,
                                 f"Không đủ lịch sử: {n} kỳ (cần ≥ 15)", start_ms)

        appeared = compute_pair_appeared_matrix(history)  # (n, 100)

        # Load weekday history
        weekday = target_date.weekday() if target_date else 0
        wd_history = _load_tails_by_weekday(db, weekday, region, province, 60, before_date=target_date)
        n_wd = len(wd_history)
        wd_appeared = compute_pair_appeared_matrix(wd_history) if n_wd >= 5 else None

        scores = np.zeros(100, dtype=float)

        # ── Pre-compute: gap_since_last cho all pairs ──
        all_gaps = np.zeros(100, dtype=float)
        all_avg_gaps = np.zeros(100, dtype=float)
        all_std_gaps = np.zeros(100, dtype=float)

        for pair in range(100):
            col = appeared[:, pair]
            positions = np.where(col > 0)[0]

            # Gap since last
            if len(positions) > 0:
                all_gaps[pair] = n - 1 - positions[-1]
            else:
                all_gaps[pair] = n

            # Gap statistics (all kỳ)
            if len(positions) >= 2:
                gaps = np.diff(positions)
                all_avg_gaps[pair] = gaps.mean()
                all_std_gaps[pair] = gaps.std() if len(gaps) > 1 else 1e-6
            elif len(positions) == 1:
                all_avg_gaps[pair] = float(all_gaps[pair])
                all_std_gaps[pair] = 1e-6
            else:
                all_avg_gaps[pair] = float(n) if n > 0 else 100.0
                all_std_gaps[pair] = 1e-6

        # ── Compute gap percentile: mỗi pair, gap hiện tại đứng ở percentile nào ──
        # so với tất cả historical gaps của pair đó
        gap_percentiles = np.zeros(100, dtype=float)
        for pair in range(100):
            col = appeared[:, pair]
            positions = np.where(col > 0)[0]
            if len(positions) >= 3:
                hist_gaps = np.diff(positions)
                current_gap = all_gaps[pair]
                gap_percentiles[pair] = np.mean(hist_gaps <= current_gap)
            else:
                gap_percentiles[pair] = 0.5  # neutral

        # ── Compute cluster gap: decade nhóm nén ──
        decade_avg_gap = np.zeros(10, dtype=float)
        for d in range(10):
            decade_pairs = range(d * 10, d * 10 + 10)
            decade_avg_gap[d] = np.mean([all_gaps[p] for p in decade_pairs])

        # Normalize decade gap
        dg_min, dg_max = decade_avg_gap.min(), decade_avg_gap.max()
        if dg_max - dg_min > 1e-8:
            decade_gap_norm = (decade_avg_gap - dg_min) / (dg_max - dg_min)
        else:
            decade_gap_norm = np.full(10, 0.5)

        for pair in range(100):
            col = appeared[:, pair]
            gap = all_gaps[pair]
            avg_gap = all_avg_gaps[pair]
            std_gap = all_std_gaps[pair]

            # ── Gap z-score (only positive = overdue) ──
            gap_zscore = (gap - avg_gap) / (std_gap + 1e-6)
            gap_zscore_clamped = max(gap_zscore, 0)
            gap_zscore_norm = min(gap_zscore_clamped / 4.0, 1.0)

            # ── Weekday-specific gap ratio ──
            weekday_gap_ratio_norm = 0.5  # default neutral
            if wd_appeared is not None:
                wd_col = wd_appeared[:, pair]
                wd_positions = np.where(wd_col > 0)[0]
                if len(wd_positions) >= 2:
                    wd_gaps = np.diff(wd_positions)
                    wd_avg_gap = wd_gaps.mean()
                    # Ratio: gap hiện tại (tính theo weekday) / avg weekday gap
                    wd_current_gap = n_wd - 1 - wd_positions[-1] if len(wd_positions) > 0 else n_wd
                    wd_ratio = wd_current_gap / (wd_avg_gap + 1e-6)
                    weekday_gap_ratio_norm = min(max(wd_ratio - 1.0, 0) / 2.0, 1.0)

            # ── Gap percentile ──
            gap_pct_norm = gap_percentiles[pair]

            # ── Consecutive miss ──
            consecutive_miss = 0
            for ap in reversed(col):
                if ap < 0.5:
                    consecutive_miss += 1
                else:
                    break
            consecutive_miss_norm = min(consecutive_miss / max(n, 1), 1.0)

            # ── Cluster gap (decade đang nén) ──
            cluster_gap_norm = decade_gap_norm[pair // 10]

            # ── Inverse recent frequency ──
            freq_7 = col[-min(n, 7):].mean() if n >= 7 else col.mean()
            inverse_recent_freq = 1.0 - freq_7

            # ── Fresh Compression bonus (v4.1) ──
            # Pair đang nén: gap trung bình + z-score vừa phải = sweetspot
            # Ưu tiên pair "building pressure" thay vì pair đang nổ liên tiếp
            fresh_compression = 0.0
            if 7 <= gap <= 15 and 0.5 <= gap_zscore <= 2.0:
                fresh_compression = 0.3   # nhẹ nhàng
            elif 15 < gap <= 25 and gap_zscore > 1.5:
                fresh_compression = 0.5   # stronger signal

            # ── Composite Score (v4.1: +fresh_compression) ──
            scores[pair] = (
                gap_zscore_norm         * 0.22 +   # ↓ from 0.25
                weekday_gap_ratio_norm  * 0.18 +   # ↓ from 0.20
                gap_pct_norm            * 0.13 +   # ↓ from 0.15
                consecutive_miss_norm   * 0.12 +   # ↓ from 0.15
                cluster_gap_norm        * 0.10 +   # unchanged
                inverse_recent_freq     * 0.15 +   # unchanged
                fresh_compression       * 0.10     # NEW v4.1
            )

        # Min-max normalize to [0, 1]
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-8:
            scores = (scores - s_min) / (s_max - s_min)

        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "gap_overdue",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("gap_overdue", province, 0, str(e), start_ms)


def _error_result(model_name: str, province: Optional[str],
                  n_draws: int, error_msg: str, start_ms: float) -> Dict:
    """Helper tạo error result dict."""
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
