"""
model_bayesian.py — Model F: Bayesian Posterior Estimation (XSMB v4)

Concept:
  - Prior: P(pair) = historical frequency (từ 180 kỳ)
  - Likelihood: P(evidence | pair) kết hợp:
    ├── Weekday context: pair xuất hiện bao nhiêu lần cùng thứ
    ├── Month/season: seasonal pattern
    ├── Previous draws: conditional probability given last 3 kỳ
    └── Gap status: overdue / on-time / recently-hit
  - Posterior: P(pair | evidence) ∝ Prior × Likelihood
  - Confidence: posterior entropy → biết model tự tin bao nhiêu

Key insight: Bayesian approach tự nhiên handles uncertainty —
cho phép ensemble biết khi nào model KHÔNG TỰ TIN.

Dependencies: chỉ numpy + scipy (đã có qua scikit-learn)
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


def _compute_prior(
    appeared: np.ndarray,
    n: int,
    smoothing: float = 0.01,
) -> np.ndarray:
    """
    Compute prior distribution P(pair) từ historical frequency.

    Laplace smoothing: P(pair) = (count + α) / (total + 100α)
    để tránh prior = 0 cho pair chưa bao giờ xuất hiện.

    Args:
        appeared: binary matrix (n_draws, 100)
        n: number of draws
        smoothing: Laplace smoothing parameter

    Returns:
        np.ndarray shape (100,) — prior probabilities, sum ≈ 1.0
    """
    counts = appeared.sum(axis=0)  # (100,)
    total_appearances = counts.sum()

    prior = (counts + smoothing) / (total_appearances + 100 * smoothing)

    # Normalize to sum = 1
    prior = prior / prior.sum()
    return prior


def _compute_weekday_likelihood(
    wd_appeared: Optional[np.ndarray],
    overall_freq: np.ndarray,
) -> np.ndarray:
    """
    Compute likelihood P(weekday_evidence | pair).

    Nếu pair xuất hiện thường xuyên hơn trong cùng weekday so với overall
    → likelihood cao hơn.

    Returns:
        np.ndarray shape (100,) — likelihood ratios
    """
    if wd_appeared is None or len(wd_appeared) < 3:
        return np.ones(100, dtype=float)

    n_wd = len(wd_appeared)
    wd_freq = wd_appeared[-min(n_wd, 30):].mean(axis=0)

    # Likelihood ratio: weekday_freq / overall_freq
    # > 1 = pair xuất hiện thường hơn vào thứ này
    ratio = wd_freq / (overall_freq + 1e-8)

    # Clamp to [0.5, 2.0] to prevent extreme values
    ratio = np.clip(ratio, 0.5, 2.0)

    return ratio


def _compute_recency_likelihood(
    appeared: np.ndarray,
    n: int,
) -> np.ndarray:
    """
    Compute likelihood based on recent appearance pattern (last 3 draws).

    Logic:
      - Xuất hiện 1/3 kỳ gần nhất → high likelihood (riding rhythm)
      - Xuất hiện 0/3 → medium-high (building pressure)
      - Xuất hiện 2-3/3 → lower likelihood (likely cooling)

    Returns:
        np.ndarray shape (100,) — recency likelihood
    """
    if n < 3:
        return np.ones(100, dtype=float)

    recent_3 = appeared[-3:]
    count_3 = recent_3.sum(axis=0)  # (100,) — how many of last 3 draws pair appeared

    likelihood = np.ones(100, dtype=float)

    # 0/3: building pressure → moderate boost
    mask_0 = count_3 == 0
    likelihood[mask_0] = 1.2

    # 1/3: sweetspot, riding rhythm → highest
    mask_1 = count_3 == 1
    likelihood[mask_1] = 1.5

    # 2/3: recent hot, might continue or cool
    mask_2 = count_3 == 2
    likelihood[mask_2] = 1.0

    # 3/3: very hot, likely to cool down
    mask_3 = count_3 >= 3
    likelihood[mask_3] = 0.7

    return likelihood


def _compute_gap_likelihood(
    appeared: np.ndarray,
    n: int,
) -> np.ndarray:
    """
    Compute likelihood based on gap status.

    Overdue pairs (gap > avg + 1σ) get higher likelihood.
    Recently appeared pairs (gap < avg - 1σ) get lower likelihood.

    Returns:
        np.ndarray shape (100,) — gap-based likelihood
    """
    if n < 10:
        return np.ones(100, dtype=float)

    likelihood = np.ones(100, dtype=float)

    for pair in range(100):
        col = appeared[:, pair]
        positions = np.where(col > 0)[0]

        if len(positions) < 2:
            likelihood[pair] = 1.2  # Chưa đủ data → slight boost
            continue

        gaps = np.diff(positions)
        avg_gap = gaps.mean()
        std_gap = gaps.std() if len(gaps) > 1 else 1.0

        current_gap = n - 1 - positions[-1]
        z_score = (current_gap - avg_gap) / (std_gap + 1e-6)

        if z_score > 2.0:
            likelihood[pair] = 1.8    # Extreme overdue
        elif z_score > 1.0:
            likelihood[pair] = 1.4    # Overdue
        elif z_score > 0:
            likelihood[pair] = 1.1    # Slightly overdue
        elif z_score > -1.0:
            likelihood[pair] = 0.9    # On time
        else:
            likelihood[pair] = 0.7    # Recently appeared, likely cooling

    return likelihood


def _compute_month_likelihood(
    history,
    target_date: date,
    appeared: np.ndarray,
) -> np.ndarray:
    """
    Compute likelihood based on month/seasonal pattern.

    Returns:
        np.ndarray shape (100,) — monthly likelihood ratios
    """
    if len(history) < 30 or "draw_date" not in history.columns:
        return np.ones(100, dtype=float)

    target_month = target_date.month

    month_mask = history["draw_date"].dt.month == target_month
    month_history = history[month_mask]

    if len(month_history) < 5:
        return np.ones(100, dtype=float)

    month_appeared = compute_pair_appeared_matrix(month_history)
    n_month = len(month_history)
    month_freq = month_appeared.mean(axis=0)

    overall_freq = appeared.mean(axis=0)

    ratio = month_freq / (overall_freq + 1e-8)
    ratio = np.clip(ratio, 0.5, 2.0)

    return ratio


def _posterior_entropy(posterior: np.ndarray) -> float:
    """
    Compute entropy of posterior distribution.
    High entropy = uncertain, Low entropy = confident.

    Returns:
        float — entropy value (0 = certain, log(100) ≈ 4.6 = max uncertainty)
    """
    p = posterior + 1e-10
    p = p / p.sum()
    return float(-np.sum(p * np.log(p)))


def predict_bayesian(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model F: Bayesian posterior estimation cho XSMB.

    Posterior(pair) ∝ Prior(pair) × L_weekday × L_recency × L_gap × L_month

    Trả về thêm field 'confidence' = 1 - normalized_entropy
    để ensemble biết model tự tin bao nhiêu.

    Args:
        db: LotteryDB instance
        province: None cho XSMB
        target_date: ngày predict
        n_draws: lookback
        top_n: top-N output
        region: 'XSMB'

    Returns:
        Dict with model_name, top_pairs, status, confidence, etc.
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 15:
            return _error_result("bayesian", province, n,
                                 f"Không đủ lịch sử: {n} kỳ (cần ≥ 15)", start_ms)

        appeared = compute_pair_appeared_matrix(history)

        # Load weekday history
        weekday = target_date.weekday() if target_date else 0
        wd_history = _load_tails_by_weekday(db, weekday, region, province, 60, before_date=target_date)
        wd_appeared = compute_pair_appeared_matrix(wd_history) if len(wd_history) >= 5 else None

        overall_freq = appeared[-min(n, 60):].mean(axis=0)

        # ── Compute Prior ──
        prior = _compute_prior(appeared, n)

        # ── Compute Likelihoods ──
        l_weekday = _compute_weekday_likelihood(wd_appeared, overall_freq)
        l_recency = _compute_recency_likelihood(appeared, n)
        l_gap = _compute_gap_likelihood(appeared, n)
        l_month = _compute_month_likelihood(history, target_date, appeared)

        # ── Posterior ∝ Prior × ΠLikelihood ──
        posterior = prior * l_weekday * l_recency * l_gap * l_month

        # Normalize
        posterior_sum = posterior.sum()
        if posterior_sum > 0:
            posterior = posterior / posterior_sum
        else:
            posterior = np.ones(100) / 100.0

        # ── Confidence: 1 - normalized entropy ──
        entropy = _posterior_entropy(posterior)
        max_entropy = np.log(100)  # ≈ 4.605
        confidence = max(0.0, 1.0 - (entropy / max_entropy))

        # Min-max normalize posterior to [0, 1] for scoring
        p_min, p_max = posterior.min(), posterior.max()
        if p_max - p_min > 1e-10:
            scores = (posterior - p_min) / (p_max - p_min)
        else:
            scores = np.ones(100) / 100.0

        # Top N
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "bayesian",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "confidence": round(confidence, 4),
            "entropy": round(entropy, 4),
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("bayesian", province, 0, str(e), start_ms)


def _error_result(model_name: str, province: Optional[str],
                  n_draws: int, error_msg: str, start_ms: float) -> Dict:
    """Helper tạo error result dict."""
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "confidence": 0.0,
        "entropy": 0.0,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
