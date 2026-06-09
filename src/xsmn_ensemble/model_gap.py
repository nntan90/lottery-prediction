"""
model_gap.py — Model 2: Gap/Overdue Scoring (Rule-based)
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Mục tiêu: Phát hiện số có độ trễ (gap) cao bất thường — số "overdue"
sắp đến chu kỳ xuất hiện trở lại (mean reversion theory).

Scoring formula:
  Score = w1 * gap_zscore_clamped    (0.35)   ← z-score dương = đang quá trễ
        + w2 * consecutive_miss_norm  (0.25)   ← miss liên tiếp cao = áp lực nổ
        + w3 * gap_ratio              (0.20)   ← gap_current / avg_gap (>1 = overdue)
        + w4 * inverse_recent_freq    (0.20)   ← freq_7 thấp = đang "nén"

Khác với model_freq_gap.py (v3.1): model này KHÔNG sử dụng frequency scoring,
chỉ focus thuần gap/overdue signal.
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional

from src.xsmn_ensemble.data_utils import _load_tails_by_draws


def predict_gap(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 250,
    top_n: int = 5,
    region: str = "XSMN",
) -> Dict:
    """
    Model 2: Pure Gap/Overdue scoring.

    Chỉ dựa trên gap analysis, KHÔNG xét frequency.
    Phát hiện số có độ trễ bất thường cao, đang "overdue" theo chu kỳ.

    Args:
        db: LotteryDB instance
        province: slug tỉnh (e.g. 'tp-hcm'), None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ quay lookback
        top_n: số cặp top-N output
        region: 'XSMN' hoặc 'XSMB'

    Returns:
        {
            'model_name': 'gap_overdue',
            'province': province,
            'top_pairs': [(pair, score), ...],
            'n_draws_used': int,
            'status': 'success' | 'error',
            'error_message': str | None,
            'execution_time_ms': int,
        }
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 10:
            return {
                "model_name": "gap_overdue",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "status": "error",
                "error_message": f"Không đủ lịch sử: {n} kỳ (cần ≥ 10)",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        scores = np.zeros(100, dtype=float)

        for pair in range(100):
            appeared = history["tail_set"].apply(lambda s, p=pair: p in s).to_numpy()

            # ── Gap since last ──
            appeared_positions = np.where(appeared)[0]
            if len(appeared_positions) > 0:
                gap_since_last = n - 1 - appeared_positions[-1]
            else:
                gap_since_last = n

            # ── Gap statistics ──
            if len(appeared_positions) >= 2:
                gaps = np.diff(appeared_positions)
                avg_gap = float(gaps.mean())
                std_gap = float(gaps.std()) if len(gaps) > 1 else 1e-6
            elif len(appeared_positions) == 1:
                avg_gap = float(gap_since_last)
                std_gap = 1e-6
            else:
                avg_gap = float(n) if n > 0 else 100.0
                std_gap = 1e-6

            # ── Gap z-score (chỉ lấy phần dương = overdue) ──
            gap_zscore = (gap_since_last - avg_gap) / (std_gap + 1e-6)
            gap_zscore_clamped = max(gap_zscore, 0)  # chỉ quan tâm overdue
            # Normalize z-score về [0,1]: z>4 coi là cực overdue
            gap_zscore_norm = min(gap_zscore_clamped / 4.0, 1.0)

            # ── Consecutive miss: số kỳ liên tiếp KHÔNG xuất hiện ──
            consecutive_miss = 0
            for ap in reversed(appeared):
                if not ap:
                    consecutive_miss += 1
                else:
                    break
            consecutive_miss_norm = min(consecutive_miss / max(n, 1), 1.0)

            # ── Gap ratio: gap hiện tại / gap trung bình ──
            # > 1.0 = đang vượt chu kỳ trung bình (overdue)
            gap_ratio = gap_since_last / (avg_gap + 1e-6)
            # Normalize về [0,1]: ratio>3 coi là extreme overdue
            gap_ratio_norm = min(max(gap_ratio - 1.0, 0) / 2.0, 1.0)

            # ── Inverse recent frequency: freq_7 thấp = đang "nén" ──
            freq_7 = appeared[-min(n, 7):].sum() / max(min(n, 7), 1)
            inverse_recent_freq = 1.0 - freq_7  # cao khi freq thấp

            # ── Composite Score (tất cả features đã ở [0,1]) ──
            scores[pair] = (
                gap_zscore_norm       * 0.35 +  # tín hiệu chính: z-score overdue
                consecutive_miss_norm * 0.25 +  # áp lực nổ tích lũy
                gap_ratio_norm        * 0.20 +  # vượt chu kỳ trung bình
                inverse_recent_freq   * 0.20    # đang "nén" ngắn hạn
            )

        # Min-max normalize to [0, 1]
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-8:
            scores = (scores - s_min) / (s_max - s_min)

        # Top N
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
        return {
            "model_name": "gap_overdue",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
