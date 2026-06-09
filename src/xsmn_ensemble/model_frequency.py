"""
model_frequency.py — Model 1: Frequency / Hot-Cool Scoring (Rule-based)
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Mục tiêu: Phát hiện số NÓNG (hot) và số LẠNH (cool) dựa trên tần suất
xuất hiện ngắn hạn (7 kỳ), trung hạn (30 kỳ), và dài hạn (100 kỳ).

Scoring formula:
  Score = w1 * freq_7_norm          (0.30)   ← nóng/lạnh cực ngắn
        + w2 * freq_30_norm         (0.25)   ← xu hướng trung hạn
        + w3 * freq_100_norm        (0.15)   ← nền tảng dài hạn
        + w4 * is_hot_streak        (0.15)   ← 3 kỳ liên tiếp xuất hiện
        + w5 * momentum             (0.15)   ← freq_7 - freq_30 (tăng tốc)

Khác với model_freq_gap.py (v3.1): model này KHÔNG sử dụng gap/overdue,
chỉ focus thuần frequency signal.
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional

from src.xsmn_ensemble.data_utils import _load_tails_by_draws


def predict_frequency(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 250,
    top_n: int = 5,
    region: str = "XSMN",
) -> Dict:
    """
    Model 1: Pure Frequency/Hot-Cool scoring.

    Chỉ dựa trên tần suất xuất hiện, KHÔNG xét gap/overdue.
    Phát hiện số đang nóng (tần suất tăng) và số đang nguội.

    Args:
        db: LotteryDB instance
        province: slug tỉnh (e.g. 'tp-hcm'), None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ quay lookback
        top_n: số cặp top-N output
        region: 'XSMN' hoặc 'XSMB'
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 10:
            return {
                "model_name": "frequency",
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

            # ── Frequency features ──
            freq_7  = appeared[-min(n, 7):].sum()  / max(min(n, 7), 1)
            freq_30 = appeared[-min(n, 30):].sum() / max(min(n, 30), 1)
            freq_100 = appeared[-min(n, 100):].sum() / max(min(n, 100), 1)

            # ── Hot streak: xuất hiện trong 2 kỳ gần nhất liên tiếp ──
            is_hot_streak = float(appeared[-2:].all()) if n >= 2 else 0.0

            # ── Momentum: freq ngắn hạn đang TĂNG so với trung hạn ──
            momentum = freq_7 - freq_30
            momentum_norm = min(max((momentum + 1.0) / 2.0, 0.0), 1.0)

            # ── Composite Score ──
            scores[pair] = (
                freq_7            * 0.30 +
                freq_30           * 0.25 +
                freq_100          * 0.15 +
                is_hot_streak     * 0.15 +
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
        return {
            "model_name": "frequency",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
