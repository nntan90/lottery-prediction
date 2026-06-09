"""
model_chisquare_independence.py — Model J: Chi-square Independence/Homogeneity (XSMB)

Checks whether group distributions depend on weekday or shift between earlier
and recent periods. Pairs get ranked when their decile/tail groups are unusually
strong for the target weekday or show a recent homogeneous-distribution shift.
"""

import math
import time
from datetime import date
from typing import Dict, Optional

import numpy as np

from src.xsmb_ensemble.data_utils import _load_tails_by_draws, compute_pair_appeared_matrix


def predict_chisquare_independence(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 2,
    region: str = "XSMB",
) -> Dict:
    """
    Model J: chi-square independence/homogeneity scorer.

    Tests:
      - group × weekday independence
      - group × recent/older period homogeneity
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)
        if n < 60:
            return _error_result(
                "chisquare_independence", province, n, f"Không đủ lịch sử: {n} kỳ (cần ≥ 60)", start_ms
            )

        appeared = compute_pair_appeared_matrix(history)
        weekdays = history["draw_date"].dt.weekday.to_numpy()
        target_weekday = target_date.weekday() if target_date else 0

        decile_weekday = _group_by_weekday(appeared, weekdays, "decile")
        tail_weekday = _group_by_weekday(appeared, weekdays, "tail")
        sum_weekday = _group_by_weekday(appeared, weekdays, "sum")

        decile_phase = _group_by_phase(appeared, "decile")
        tail_phase = _group_by_phase(appeared, "tail")
        sum_phase = _group_by_phase(appeared, "sum")

        weekday_strength = {
            "decile": _independence_strength(decile_weekday),
            "tail": _independence_strength(tail_weekday),
            "sum": _independence_strength(sum_weekday),
        }
        phase_strength = {
            "decile": _independence_strength(decile_phase),
            "tail": _independence_strength(tail_phase),
            "sum": _independence_strength(sum_phase),
        }

        decile_weekday_resid = _positive_cell_residuals(decile_weekday)[:, target_weekday]
        tail_weekday_resid = _positive_cell_residuals(tail_weekday)[:, target_weekday]
        sum_weekday_resid = _positive_cell_residuals(sum_weekday)[:, target_weekday]

        decile_phase_resid = _positive_cell_residuals(decile_phase)[:, 1]
        tail_phase_resid = _positive_cell_residuals(tail_phase)[:, 1]
        sum_phase_resid = _positive_cell_residuals(sum_phase)[:, 1]

        pair_freq_target_wd = _pair_freq_for_weekday(appeared, weekdays, target_weekday)
        recent_freq = appeared[-min(n, 30):].mean(axis=0)
        older_freq = appeared[: max(n - 30, 1)].mean(axis=0)
        trend = _safe_minmax(np.maximum(recent_freq - older_freq, 0.0))

        scores = np.zeros(100, dtype=float)
        for pair in range(100):
            decile = pair // 10
            tail = pair % 10
            digit_sum = (pair // 10) + tail

            weekday_score = (
                decile_weekday_resid[decile] * weekday_strength["decile"] * 0.45 +
                tail_weekday_resid[tail] * weekday_strength["tail"] * 0.35 +
                sum_weekday_resid[digit_sum] * weekday_strength["sum"] * 0.20
            )
            phase_score = (
                decile_phase_resid[decile] * phase_strength["decile"] * 0.45 +
                tail_phase_resid[tail] * phase_strength["tail"] * 0.35 +
                sum_phase_resid[digit_sum] * phase_strength["sum"] * 0.20
            )

            scores[pair] = (
                weekday_score * 0.42 +
                phase_score * 0.34 +
                pair_freq_target_wd[pair] * 0.14 +
                trend[pair] * 0.10
            )

        scores = _safe_minmax(scores)
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "chisquare_independence",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
    except Exception as e:
        return _error_result("chisquare_independence", province, 0, str(e), start_ms)


def _group_by_weekday(matrix: np.ndarray, weekdays: np.ndarray, group: str) -> np.ndarray:
    n_groups = 19 if group == "sum" else 10
    table = np.zeros((n_groups, 7), dtype=float)
    for row_idx, weekday in enumerate(weekdays):
        for pair in np.where(matrix[row_idx] > 0)[0]:
            table[_group_index(pair, group), int(weekday)] += 1
    return table


def _group_by_phase(matrix: np.ndarray, group: str) -> np.ndarray:
    n_groups = 19 if group == "sum" else 10
    table = np.zeros((n_groups, 2), dtype=float)
    split = max(matrix.shape[0] - 30, 1)
    for row_idx in range(matrix.shape[0]):
        phase = 0 if row_idx < split else 1
        for pair in np.where(matrix[row_idx] > 0)[0]:
            table[_group_index(pair, group), phase] += 1
    return table


def _group_index(pair: int, group: str) -> int:
    if group == "tail":
        return pair % 10
    if group == "sum":
        return (pair // 10) + (pair % 10)
    return pair // 10


def _positive_cell_residuals(table: np.ndarray) -> np.ndarray:
    total = table.sum()
    if total <= 0:
        return np.zeros_like(table, dtype=float)
    row_sum = table.sum(axis=1, keepdims=True)
    col_sum = table.sum(axis=0, keepdims=True)
    expected = row_sum @ col_sum / total
    residual = (table - expected) / np.sqrt(expected + 1e-9)
    return _safe_minmax(np.maximum(residual, 0.0))


def _independence_strength(table: np.ndarray) -> float:
    total = float(table.sum())
    if total <= 0:
        return 0.0
    row_sum = table.sum(axis=1, keepdims=True)
    col_sum = table.sum(axis=0, keepdims=True)
    expected = row_sum @ col_sum / total
    chi2 = float(((table - expected) ** 2 / (expected + 1e-9)).sum())
    df = max((table.shape[0] - 1) * (table.shape[1] - 1), 1)
    p_value = _chi_square_sf_approx(chi2, df)
    return min(max(1.0 - p_value, 0.0), 1.0)


def _pair_freq_for_weekday(matrix: np.ndarray, weekdays: np.ndarray, weekday: int) -> np.ndarray:
    mask = weekdays == weekday
    if not mask.any():
        return np.zeros(100, dtype=float)
    return _safe_minmax(matrix[mask].mean(axis=0))


def _chi_square_sf_approx(chi2: float, df: int) -> float:
    z = ((chi2 / df) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * df))) / math.sqrt(2.0 / (9.0 * df))
    return 0.5 * math.erfc(z / math.sqrt(2.0))


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
