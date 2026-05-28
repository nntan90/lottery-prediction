"""
model_frequency.py — Model A: Multi-Window Frequency Analysis (XSMB v4)

Tối ưu cho XSMB daily data (1,095 kỳ/năm):
  - Multi-window: freq_3 / freq_7 / freq_14 / freq_30 / freq_60
  - Weekday-segmented frequency: freq cùng thứ
  - Acceleration detection: d(freq)/dt qua 3 windows
  - Hot-streak enhanced: 2/3 + 3/3 patterns

Scoring formula (XSMB v4):
  Score = 0.20 × freq_7_norm
        + 0.15 × freq_14_norm
        + 0.10 × freq_30_norm
        + 0.15 × weekday_freq_norm     ← same-weekday frequency
        + 0.15 × acceleration_norm     ← d(freq)/dt
        + 0.10 × hot_streak_score      ← 2/3 + 3/3 patterns
        + 0.15 × momentum_norm         ← freq_7 - freq_30
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


def predict_frequency(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model A: Multi-window frequency scoring cho XSMB.

    Tận dụng data dày đặc (daily) để phân tích frequency ở nhiều scale:
    ultra-short (3 kỳ), short (7), medium (14/30), long (60).
    Thêm weekday-specific frequency + acceleration detection.

    Args:
        db: LotteryDB instance
        province: None cho XSMB (quay chung toàn quốc)
        target_date: ngày cần dự đoán
        n_draws: số kỳ quay lookback tối đa
        top_n: số cặp top-N output
        region: always 'XSMB' for this module

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()

    try:
        # Load main history
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 15:
            return _error_result("frequency", province, n,
                                 f"Không đủ lịch sử: {n} kỳ (cần ≥ 15)", start_ms)

        appeared = compute_pair_appeared_matrix(history)  # (n, 100)

        # Load weekday-specific history
        weekday = target_date.weekday() if target_date else 0
        wd_history = _load_tails_by_weekday(db, weekday, region, province, 60, before_date=target_date)
        n_wd = len(wd_history)

        wd_appeared = None
        if n_wd >= 5:
            wd_appeared = compute_pair_appeared_matrix(wd_history)

        scores = np.zeros(100, dtype=float)

        for pair in range(100):
            col = appeared[:, pair]

            # ── Multi-window frequency ──
            freq_3  = col[-min(n, 3):].mean()   if n >= 3  else 0.0
            freq_7  = col[-min(n, 7):].mean()   if n >= 7  else col.mean()
            freq_14 = col[-min(n, 14):].mean()  if n >= 14 else col.mean()
            freq_30 = col[-min(n, 30):].mean()  if n >= 30 else col.mean()
            freq_60 = col[-min(n, 60):].mean()  if n >= 60 else col.mean()

            # ── Weekday-specific frequency ──
            if wd_appeared is not None:
                wd_col = wd_appeared[:, pair]
                weekday_freq = wd_col[-min(n_wd, 30):].mean()
            else:
                weekday_freq = freq_30  # fallback

            # ── Acceleration: d(freq)/dt qua 3 windows ──
            # Positive = tần suất đang tăng nhanh (số nóng lên)
            # Tính bằng chênh lệch giữa các windows liên tiếp
            accel_short = freq_7 - freq_14   # tăng tốc ngắn hạn
            accel_med   = freq_14 - freq_30  # tăng tốc trung hạn
            # Combined acceleration: weighted sum
            acceleration = 0.6 * accel_short + 0.4 * accel_med
            # Normalize [-1, +1] → [0, 1]
            acceleration_norm = min(max((acceleration + 0.5) / 1.0, 0.0), 1.0)

            # ── Hot-streak scoring (v4.1: cooling-aware) ──
            # 3/3: xuất hiện tất cả 3 kỳ gần nhất → quá nóng, có thể cooling
            # 2/3: warm signal, nhưng không extreme
            # v4.1: giảm score cho 3/3 để tránh echo vào consensus
            hot_streak = 0.0
            if n >= 3:
                last3 = col[-3:]
                hits_in_3 = last3.sum()
                if hits_in_3 >= 3:
                    hot_streak = 0.3   # ↓ from 1.0 — quá nóng = likely cooling
                elif hits_in_3 >= 2:
                    hot_streak = 0.5   # warm signal
                elif last3[-1] == 1:
                    hot_streak = 0.3   # Xuất hiện kỳ mới nhất

                # v4.1: 7-day cooling check — extreme hot → regression to mean
                if n >= 7:
                    last7 = col[-7:]
                    hits_in_7 = last7.sum()
                    if hits_in_7 >= 5:
                        hot_streak *= 0.3  # Extreme hot → dampen heavily
                    elif hits_in_7 >= 4:
                        hot_streak *= 0.5  # Very hot → moderate dampen

            # ── Momentum: freq ngắn hạn - freq trung hạn ──
            momentum = freq_7 - freq_30
            momentum_norm = min(max((momentum + 0.5) / 1.0, 0.0), 1.0)

            # ── Composite Score ──
            scores[pair] = (
                freq_7            * 0.20 +
                freq_14           * 0.15 +
                freq_30           * 0.10 +
                weekday_freq      * 0.15 +
                acceleration_norm * 0.15 +
                hot_streak        * 0.10 +
                momentum_norm     * 0.15
            )

        # Min-max normalize to [0, 1]
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-8:
            scores = (scores - s_min) / (s_max - s_min)

        # Top N
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "frequency",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("frequency", province, 0, str(e), start_ms)


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
