"""
test_cdm.py — Unit tests for CDM (Compound Dirichlet-Multinomial) model.

Tests:
  1. CDM probability sums to 1.0
  2. No zero probabilities when alpha > 0
  3. Uniform prior equals Laplace smoothing
  4. Output format matches ensemble interface
  5. Edge case: small data
  6. Method of moments estimation
  7. Top-N selection correctness
"""

import numpy as np
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.xsmb_ensemble.model_cdm import (
    K,
    DEFAULT_ALPHA,
    _alpha_uniform,
    _alpha_moment,
    init_alpha,
    compute_cdm_probabilities,
)


class TestCDMProbabilities:
    """Test CDM posterior probability computation."""

    def test_probability_sum_to_one(self):
        """Σ p_j ≈ 1.0 for any valid inputs."""
        count_vector = np.random.randint(0, 50, size=K).astype(np.float64)
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)
        assert abs(probs.sum() - 1.0) < 1e-10, f"Sum = {probs.sum()}, expected 1.0"

    def test_no_zero_probability(self):
        """All p_j > 0 when α_j > 0 (even if n_j = 0)."""
        count_vector = np.zeros(K, dtype=np.float64)
        count_vector[42] = 100  # Only pair 42 has counts
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)
        assert (probs > 0).all(), "Some probabilities are zero despite alpha > 0"

    def test_uniform_prior_equals_laplace(self):
        """With α=1 (uniform), CDM = Laplace smoothing."""
        count_vector = np.array([10, 5, 0, 15] + [0] * 96, dtype=np.float64)
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)

        # Manual Laplace: (count + 1) / (total_count + K)
        total = count_vector.sum() + K * 1.0
        expected = (count_vector + 1.0) / total
        np.testing.assert_allclose(probs, expected, atol=1e-10)

    def test_higher_count_higher_probability(self):
        """Pair with higher count should have higher probability."""
        count_vector = np.zeros(K, dtype=np.float64)
        count_vector[10] = 50
        count_vector[20] = 30
        count_vector[30] = 10
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)
        assert probs[10] > probs[20] > probs[30], "Higher count should → higher probability"

    def test_zero_count_vector(self):
        """All-zero count vector should give uniform probabilities."""
        count_vector = np.zeros(K, dtype=np.float64)
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)
        expected = np.full(K, 1.0 / K)
        np.testing.assert_allclose(probs, expected, atol=1e-10)

    def test_shape_output(self):
        """Output shape should be (K,)."""
        count_vector = np.random.randint(0, 10, size=K).astype(np.float64)
        alpha = _alpha_uniform(1.0)
        probs = compute_cdm_probabilities(count_vector, alpha)
        assert probs.shape == (K,), f"Expected shape (100,), got {probs.shape}"


class TestAlphaEstimation:
    """Test Dirichlet prior estimation strategies."""

    def test_alpha_uniform(self):
        """Uniform alpha returns constant vector."""
        alpha = _alpha_uniform(2.5)
        assert alpha.shape == (K,)
        assert (alpha == 2.5).all()

    def test_alpha_uniform_default(self):
        """Default alpha = 1.0."""
        alpha = _alpha_uniform()
        assert (alpha == DEFAULT_ALPHA).all()

    def test_init_alpha_uniform(self):
        """init_alpha with strategy='uniform' returns uniform vector."""
        alpha = init_alpha(strategy="uniform", alpha_value=3.0)
        assert (alpha == 3.0).all()

    def test_init_alpha_moment(self):
        """init_alpha with strategy='moment' returns estimated alpha."""
        # Generate synthetic count matrix
        np.random.seed(42)
        count_matrix = np.random.binomial(1, 0.3, size=(60, K)).astype(np.float32)
        alpha = init_alpha(strategy="moment", count_matrix=count_matrix)
        assert alpha.shape == (K,)
        assert (alpha > 0).all(), "All alpha values should be positive"
        assert (alpha <= 10.0).all(), "Alpha should be clamped to max 10.0"

    def test_init_alpha_moment_small_data(self):
        """Method of moments falls back to uniform for very small data."""
        count_matrix = np.ones((2, K), dtype=np.float32)  # Only 2 draws
        alpha = init_alpha(strategy="moment", count_matrix=count_matrix)
        assert alpha.shape == (K,)
        assert (alpha == DEFAULT_ALPHA).all(), "Small data should fallback to uniform"

    def test_init_alpha_unknown_strategy(self):
        """Unknown strategy falls back to uniform."""
        alpha = init_alpha(strategy="unknown_strategy", alpha_value=1.5)
        assert (alpha == 1.5).all()


class TestCDMIntegration:
    """Test full CDM pipeline behavior (without DB)."""

    def test_deterministic_output(self):
        """Same input → same output (no randomness in CDM)."""
        count = np.array([5] * K, dtype=np.float64)
        count[42] = 20
        alpha = _alpha_uniform(1.0)

        probs1 = compute_cdm_probabilities(count, alpha)
        probs2 = compute_cdm_probabilities(count, alpha)
        np.testing.assert_array_equal(probs1, probs2)

    def test_alpha_sensitivity(self):
        """Higher alpha → more uniform distribution (less sensitive to data)."""
        count = np.zeros(K, dtype=np.float64)
        count[0] = 100  # Extreme: only pair 00 has data

        # Low alpha: posterior dominated by data
        probs_low = compute_cdm_probabilities(count, _alpha_uniform(0.01))
        # High alpha: prior dominates
        probs_high = compute_cdm_probabilities(count, _alpha_uniform(100.0))

        # Low alpha → pair 00 has much higher prob
        ratio_low = probs_low[0] / probs_low[1]
        ratio_high = probs_high[0] / probs_high[1]
        assert ratio_low > ratio_high, "Lower alpha should make distribution more peaked"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
