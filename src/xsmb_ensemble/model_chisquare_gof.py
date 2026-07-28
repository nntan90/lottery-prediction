"""
model_chisquare_gof.py — Model I: Chi-square Goodness-of-fit (XSMB)

Tests whether pair groups deviate from an expected uniform distribution, then
ranks pairs that sit inside statistically unusual decile/tail/sum clusters.
"""

import math
import time
from datetime import date
from typing import Dict, Optional

import numpy as np

from src.xsmb_ensemble.data_utils import _load_tails_by_draws, compute_pair_appeared_matrix


SUM_GROUP_CARDINALITIES = np.array(
    [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
    dtype=float,
)


def predict_chisquare_gof(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 2,
    region: str = "XSMB",
) -> Dict:
    """
    Model I: chi-square goodness-of-fit anomaly scorer.

    It evaluates uniformity over deciles, final tails, and digit sums across
    30/60/N-draw windows. Pairs in groups with stronger positive residuals get
    higher scores, with a mild pair-level frequency/gap stabilizer.
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)
        if n < 30:
            return _error_result("chisquare_gof", province, n, f"Không đủ lịch sử: {n} kỳ (cần ≥ 30)", start_ms)

        appeared = compute_pair_appeared_matrix(history)
        scores = np.zeros(100, dtype=float)

        windows = [30, 60, n]
        window_weight = {30: 0.45, 60: 0.35, n: 0.20}

        group_scores: dict[int, dict[str, np.ndarray]] = {}
        p_strengths: dict[int, dict[str, float]] = {}
        for w in windows:
            recent = appeared[-min(n, w):]
            decile_counts, tail_counts, sum_counts = _group_counts(recent)
            group_scores[w] = {
                "decile": _positive_residual_scores(decile_counts),
                "tail": _positive_residual_scores(tail_counts),
                "sum": _positive_residual_scores(
                    sum_counts,
                    expected_weights=SUM_GROUP_CARDINALITIES,
                ),
            }
            p_strengths[w] = {
                "decile": _p_strength(decile_counts),
                "tail": _p_strength(tail_counts),
                "sum": _p_strength(
                    sum_counts,
                    expected_weights=SUM_GROUP_CARDINALITIES,
                ),
            }

        pair_freq_30 = appeared[-min(n, 30):].mean(axis=0)
        pair_freq_60 = appeared[-min(n, 60):].mean(axis=0)

        for pair in range(100):
            decile = pair // 10
            tail = pair % 10
            digit_sum = (pair // 10) + tail

            stat_score = 0.0
            for w in windows:
                stat_score += window_weight[w] * (
                    group_scores[w]["decile"][decile] * p_strengths[w]["decile"] * 0.45 +
                    group_scores[w]["tail"][tail] * p_strengths[w]["tail"] * 0.35 +
                    group_scores[w]["sum"][digit_sum] * p_strengths[w]["sum"] * 0.20
                )

            gap = _gap_since_last(appeared[:, pair])
            gap_score = 1.0 if 5 <= gap <= 20 else 0.4 if gap > 20 else 0.15
            pair_support = pair_freq_30[pair] * 0.6 + pair_freq_60[pair] * 0.4

            scores[pair] = stat_score * 0.70 + pair_support * 0.18 + gap_score * 0.12

        scores = _safe_minmax(scores)
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "chisquare_gof",
            "source_family": "distributional_frequency",
            "province": province,
            "top_pairs": top_pairs,
            "score_vector": [float(score) for score in scores],
            "score_semantics": "relative_score_uncalibrated",
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
    except Exception as e:
        return _error_result("chisquare_gof", province, 0, str(e), start_ms)


def _group_counts(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pair_counts = matrix.sum(axis=0)
    decile_counts = np.zeros(10, dtype=float)
    tail_counts = np.zeros(10, dtype=float)
    sum_counts = np.zeros(19, dtype=float)
    for pair, count in enumerate(pair_counts):
        decile_counts[pair // 10] += count
        tail_counts[pair % 10] += count
        sum_counts[(pair // 10) + (pair % 10)] += count
    return decile_counts, tail_counts, sum_counts


def _expected_counts(
    counts: np.ndarray,
    expected_weights: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return group expectations, accounting for unequal group cardinality."""
    total = float(counts.sum())
    if total <= 0:
        return np.zeros_like(counts, dtype=float)
    if expected_weights is None:
        return np.full(len(counts), total / len(counts), dtype=float)
    weights = np.asarray(expected_weights, dtype=float)
    if weights.shape != counts.shape or np.any(weights < 0) or weights.sum() <= 0:
        raise ValueError("expected_weights must match counts and have positive mass")
    return total * weights / weights.sum()


def _positive_residual_scores(
    counts: np.ndarray,
    expected_weights: Optional[np.ndarray] = None,
) -> np.ndarray:
    expected = _expected_counts(counts, expected_weights)
    if expected.sum() <= 0:
        return np.zeros_like(counts, dtype=float)
    residual = (counts - expected) / np.sqrt(expected + 1e-9)
    return _safe_minmax(np.maximum(residual, 0.0))


def _p_strength(
    counts: np.ndarray,
    expected_weights: Optional[np.ndarray] = None,
) -> float:
    total = float(counts.sum())
    if total <= 0:
        return 0.0
    expected = _expected_counts(counts, expected_weights)
    chi2 = float(((counts - expected) ** 2 / (expected + 1e-9)).sum())
    p_value = _chi_square_sf_approx(chi2, len(counts) - 1)
    return min(max(1.0 - p_value, 0.0), 1.0)


def _chi_square_sf_approx(chi2: float, df: int) -> float:
    if df <= 0:
        return 1.0
    z = ((chi2 / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))) / math.sqrt(2.0 / (9.0 * df))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


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
