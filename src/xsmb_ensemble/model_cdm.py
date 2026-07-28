"""
model_cdm.py — Compound Dirichlet-Multinomial Model (XSMB)

Bayesian baseline model sử dụng Dirichlet-Multinomial conjugate prior
để ước lượng xác suất xuất hiện cho 100 cặp số (00-99).

Công thức CDM:
  p_j = (α_j + n_j) / Σ_k(α_k + n_k)

Trong đó:
  K = 100 trạng thái (00-99)
  n_j = tổng đếm số j trên N kỳ lookback (count, KHÔNG phải binary)
  α_j = Dirichlet prior cho từng số (Phase 1: uniform α_j = α_0)

So sánh với model_bayesian.py:
  - Bayesian: posterior = prior × L_weekday × L_recency × L_gap × L_month
  - CDM: posterior = pure Dirichlet-Multinomial (chỉ dùng count data, không heuristic)

CDM hoạt động như Bayesian baseline thuần túy, không thêm các likelihood
heuristic, giúp đánh giá "pure count signal" có giá trị thế nào.

Alpha strategies:
  - "uniform": α_j = α_0 (default 1.0, Laplace smoothing)
  - "moment":  Method of moments estimation (Phase 2)
  - "mle":     Maximum likelihood estimation (Phase 2)

Config: scoring.yaml → xsmb_v5 section
"""

import time
import numpy as np
from datetime import date
from typing import Optional, Dict

from src.xsmb_ensemble.data_utils import (
    _load_tails_by_draws,
    compute_pair_appeared_matrix,
)


# ─── Constants ───────────────────────────────────────────────────────────────

K = 100                   # Number of states (pairs 00-99)
MIN_DRAWS = 10            # Minimum draws required for meaningful estimation
DEFAULT_ALPHA = 1.0       # Laplace smoothing constant (uniform prior)


# ─── Alpha Estimation Strategies ─────────────────────────────────────────────

def _alpha_uniform(alpha_value: float = DEFAULT_ALPHA) -> np.ndarray:
    """
    Uniform Dirichlet prior: α_j = α_0 for all j.

    Tương đương Laplace smoothing cho counts.
    α_0 = 1.0 → add-one smoothing (non-informative prior).
    α_0 < 1.0 → sparse prior (ưu tiên phân phối tập trung).
    α_0 > 1.0 → smooth prior (ưu tiên phân phối đều).

    Args:
        alpha_value: scalar prior value for all K states

    Returns:
        np.ndarray shape (K,) with α_j = alpha_value
    """
    return np.full(K, alpha_value, dtype=np.float64)


def _alpha_moment(count_matrix: np.ndarray) -> np.ndarray:
    """
    Method of Moments estimation cho Dirichlet prior.

    Estimate α from the observed variance of proportions across draws.
    Khi variance nhỏ → α lớn (phân phối ổn định).
    Khi variance lớn → α nhỏ (phân phối biến động).

    Công thức:
      p̄_j = mean(count_j / M) across draws
      S² = mean variance of proportions across draws
      α_0 = (p̄(1 - p̄) / S² - 1) × p̄  (simplified for K-dimensional case)

    Args:
        count_matrix: shape (N, K) — count of each pair per draw

    Returns:
        np.ndarray shape (K,) with estimated α_j
    """
    N, _ = count_matrix.shape
    if N < 3:
        return _alpha_uniform(DEFAULT_ALPHA)

    # Proportions per draw: p_ij = count_ij / M_i
    row_sums = count_matrix.sum(axis=1, keepdims=True)
    row_sums = np.maximum(row_sums, 1)  # Avoid division by zero
    proportions = count_matrix / row_sums

    # Mean and variance of proportions across draws
    p_bar = proportions.mean(axis=0)   # shape (K,)
    p_var = proportions.var(axis=0)    # shape (K,)

    # Estimate concentration parameter α_0
    # Using method of moments: α_0 ≈ (p̄(1-p̄) / var(p) - 1)
    # We compute per-category and take the median for robustness
    valid = p_var > 1e-10
    if not valid.any():
        return _alpha_uniform(DEFAULT_ALPHA)

    alpha_estimates = np.zeros(K, dtype=np.float64)
    alpha_estimates[valid] = (p_bar[valid] * (1 - p_bar[valid]) / p_var[valid] - 1) * p_bar[valid]

    # Clamp to reasonable range [0.01, 10.0]
    alpha_estimates = np.clip(alpha_estimates, 0.01, 10.0)

    # For invalid categories (zero variance), use median of valid estimates
    if (~valid).any():
        median_alpha = np.median(alpha_estimates[valid])
        alpha_estimates[~valid] = median_alpha

    return alpha_estimates


def init_alpha(strategy: str = "uniform", count_matrix: np.ndarray = None,
               alpha_value: float = DEFAULT_ALPHA) -> np.ndarray:
    """
    Initialize Dirichlet prior α based on strategy.

    Args:
        strategy: "uniform", "moment", or "mle"
        count_matrix: required for "moment" and "mle" strategies
        alpha_value: scalar for "uniform" strategy

    Returns:
        np.ndarray shape (K,)
    """
    if strategy == "uniform":
        return _alpha_uniform(alpha_value)
    elif strategy == "moment":
        if count_matrix is None:
            return _alpha_uniform(alpha_value)
        return _alpha_moment(count_matrix)
    else:
        # Fallback to uniform for unimplemented strategies
        return _alpha_uniform(alpha_value)


# ─── CDM Core Prediction ────────────────────────────────────────────────────

def compute_cdm_probabilities(
    count_vector: np.ndarray,
    alpha: np.ndarray,
) -> np.ndarray:
    """
    Compute CDM posterior probabilities.

    CDM formula:
      p_j = (α_j + n_j) / Σ_k(α_k + n_k)

    Args:
        count_vector: shape (K,) — total count n_j per pair over lookback window
        alpha: shape (K,) — Dirichlet prior parameters

    Returns:
        np.ndarray shape (K,) — posterior probabilities (sum ≈ 1.0)
    """
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
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
    alpha_strategy: str = "uniform",
    alpha_value: float = DEFAULT_ALPHA,
) -> Dict:
    """
    CDM (Compound Dirichlet-Multinomial) prediction cho XSMB.

    Pipeline:
      1. Load N kỳ quay gần nhất → binary matrix (N, 100)
      2. Tính count vector n_j = Σ appeared[i][j] cho mỗi pair
      3. Init Dirichlet prior α_j theo strategy
      4. CDM posterior: p_j = (α_j + n_j) / Σ(α_k + n_k)
      5. Score normalization → top_n selection

    Args:
        db: LotteryDB instance
        province: None for XSMB
        target_date: ngày target (dự đoán cho ngày này)
        n_draws: lookback window (số kỳ quay)
        top_n: số cặp output
        region: "XSMB"
        alpha_strategy: "uniform" | "moment"
        alpha_value: scalar prior cho uniform strategy

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

        # ── Step 2: Compute count matrix and count vector ──
        appeared = compute_pair_appeared_matrix(history)  # (N, 100) binary
        count_vector = appeared.sum(axis=0)  # (100,) — total count per pair

        # ── Step 3: Initialize Dirichlet prior ──
        alpha = init_alpha(strategy=alpha_strategy, count_matrix=appeared, alpha_value=alpha_value)

        # ── Step 4: CDM posterior probabilities ──
        probabilities = compute_cdm_probabilities(count_vector, alpha)

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
            "source_family": "frequency_bayesian",
            "province": province,
            "top_pairs": top_pairs,
            "score_vector": [float(score) for score in scores],
            "score_semantics": "relative_score_uncalibrated",
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
