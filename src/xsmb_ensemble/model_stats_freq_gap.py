"""
model_stats_freq_gap.py — Model H: Descriptive Frequency/Gap Statistics (XSMB)

Ranks pairs from simple descriptive statistics over multiple draw windows.
This model intentionally stays transparent: it counts pair, tail, and digit-sum
frequency, then blends those baselines with gap and short-term trend signals.
"""

import time
from datetime import date
from typing import Dict, Optional

import numpy as np

from src.xsmb_ensemble.data_utils import _load_tails_by_draws, compute_pair_appeared_matrix


def predict_stats_freq_gap(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 2,
    region: str = "XSMB",
) -> Dict:
    """
    Model H: descriptive frequency/gap baseline.

    Score components:
      - pair frequency anomaly over 7/30/60/N draws
      - tail frequency anomaly for final digit 0-9
      - digit-sum anomaly for sums 0-18
      - gap sweet spot and trend acceleration
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)
        if n < 30:
            return _error_result("stats_freq_gap", province, n, f"Không đủ lịch sử: {n} kỳ (cần ≥ 30)", start_ms)

        appeared = compute_pair_appeared_matrix(history)
        scores = np.zeros(100, dtype=float)

        windows = [7, 30, 60, n]
        pair_freq = {w: appeared[-min(n, w):].mean(axis=0) for w in windows}

        tail_freq = {}
        sum_freq = {}
        for w in windows:
            recent = appeared[-min(n, w):]
            tail_counts = np.zeros(10, dtype=float)
            sum_counts = np.zeros(19, dtype=float)
            for pair in range(100):
                count = recent[:, pair].sum()
                tail_counts[pair % 10] += count
                sum_counts[(pair // 10) + (pair % 10)] += count
            tail_freq[w] = _safe_minmax(tail_counts)
            sum_freq[w] = _safe_minmax(sum_counts)

        for pair in range(100):
            col = appeared[:, pair]
            digit_sum = (pair // 10) + (pair % 10)
            tail = pair % 10

            freq_score = (
                pair_freq[7][pair] * 0.30 +
                pair_freq[30][pair] * 0.25 +
                pair_freq[60][pair] * 0.20 +
                pair_freq[n][pair] * 0.10
            )
            group_score = (
                tail_freq[30][tail] * 0.07 +
                tail_freq[60][tail] * 0.05 +
                sum_freq[30][digit_sum] * 0.02 +
                sum_freq[60][digit_sum] * 0.01
            )

            gap = _gap_since_last(col)
            if 5 <= gap <= 14:
                gap_score = 1.0
            elif 15 <= gap <= 28:
                gap_score = 0.7
            elif gap < 3:
                gap_score = 0.2
            else:
                gap_score = 0.45

            trend = pair_freq[7][pair] - pair_freq[30][pair]
            trend_score = min(max((trend + 0.4) / 0.8, 0.0), 1.0)

            scores[pair] = freq_score + group_score + gap_score * 0.12 + trend_score * 0.08

        scores = _safe_minmax(scores)
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "stats_freq_gap",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
    except Exception as e:
        return _error_result("stats_freq_gap", province, 0, str(e), start_ms)


def _gap_since_last(col: np.ndarray) -> int:
    positions = np.where(col > 0)[0]
    if len(positions) == 0:
        return len(col)
    return len(col) - 1 - int(positions[-1])


def _safe_minmax(values: np.ndarray) -> np.ndarray:
    v_min, v_max = float(values.min()), float(values.max())
    if v_max - v_min < 1e-10:
        return np.zeros_like(values, dtype=float)
    return (values - v_min) / (v_max - v_min)


def _error_result(model_name: str, province: Optional[str], n_draws: int, error_msg: str, start_ms: float) -> Dict:
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
