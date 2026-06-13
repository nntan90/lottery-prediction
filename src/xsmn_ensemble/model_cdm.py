"""
model_cdm.py — Compound Dirichlet-Multinomial Model (XSMN)

Bayesian baseline model sử dụng Dirichlet-Multinomial conjugate prior
để ước lượng xác suất xuất hiện cho 100 cặp số (00-99).

Công thức CDM:
  p_j = (α_j + n_j) / Σ_k(α_k + n_k)

Tương tự XSMB version nhưng dùng data_utils của XSMN
(pagination-based loading cho dữ liệu weekly).

Config: scoring.yaml → xsmn_overrides section
"""

import time
import numpy as np
from datetime import date
from typing import Optional, Dict

from src.xsmn_ensemble.data_utils import _load_tails_by_draws


# ─── Constants ───────────────────────────────────────────────────────────────

K = 100
MIN_DRAWS = 5             # XSMN mỗi tỉnh ~1 lần/tuần → threshold thấp hơn
DEFAULT_ALPHA = 1.0


# ─── Helper ──────────────────────────────────────────────────────────────────

def _compute_pair_appeared_matrix(history) -> np.ndarray:
    """Chuyển DataFrame history thành binary matrix (n_draws, 100)."""
    n = len(history)
    matrix = np.zeros((n, K), dtype=np.float32)
    for i, tail_set in enumerate(history["tail_set"]):
        for pair in tail_set:
            if 0 <= pair <= 99:
                matrix[i, pair] = 1.0
    return matrix


def _compute_cdm_probabilities(count_vector: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """CDM posterior: p_j = (α_j + n_j) / Σ(α_k + n_k)."""
    posterior = alpha + count_vector
    total = posterior.sum()
    if total > 0:
        return posterior / total
    else:
        return np.full(K, 1.0 / K, dtype=np.float64)


# ─── Main Predict Function ──────────────────────────────────────────────────

def predict_cdm(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 250,
    top_n: int = 5,
    region: str = "XSMN",
    alpha_strategy: str = "uniform",
    alpha_value: float = DEFAULT_ALPHA,
) -> Dict:
    """
    CDM (Compound Dirichlet-Multinomial) prediction cho XSMN.

    Pipeline:
      1. Load N kỳ quay gần nhất cho province → binary matrix (N, 100)
      2. Tính count vector n_j
      3. CDM posterior: p_j = (α_j + n_j) / Σ(α_k + n_k)
      4. Score normalization → top_n selection

    Args:
        db: LotteryDB instance
        province: province slug (e.g. 'tp-hcm') or None
        target_date: ngày target
        n_draws: lookback window
        top_n: số cặp output
        region: "XSMN"
        alpha_strategy: "uniform" (Phase 1)
        alpha_value: scalar prior

    Returns:
        Dict compatible với ensemble engine interface
    """
    t0 = time.time()

    try:
        # ── Step 1: Load historical tails data ──
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < MIN_DRAWS:
            return {
                "model_name": "cdm",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "status": "error",
                "error_message": f"Insufficient data: {n} draws < {MIN_DRAWS} minimum",
                "execution_time_ms": int((time.time() - t0) * 1000),
            }

        # ── Step 2: Count matrix & vector ──
        appeared = _compute_pair_appeared_matrix(history)
        count_vector = appeared.sum(axis=0)

        # ── Step 3: Dirichlet prior (uniform Phase 1) ──
        alpha = np.full(K, alpha_value, dtype=np.float64)

        # ── Step 4: CDM posterior probabilities ──
        probabilities = _compute_cdm_probabilities(count_vector, alpha)

        # ── Step 5: Score normalization (min-max → [0, 1]) ──
        scores = probabilities.copy()
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-10:
            scores = (scores - s_min) / (s_max - s_min)
        else:
            scores = np.full(K, 0.5, dtype=np.float64)

        # ── Top N selection ──
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        elapsed = int((time.time() - t0) * 1000)

        return {
            "model_name": "cdm",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "alpha_strategy": alpha_strategy,
            "status": "success",
            "error_message": None,
            "execution_time_ms": elapsed,
        }

    except Exception as e:
        return {
            "model_name": "cdm",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - t0) * 1000),
        }
