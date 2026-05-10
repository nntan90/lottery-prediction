"""
test_ensemble_engine.py — Unit tests for compute_global_borda
P1#7: Ensure ensemble scoring logic is correct and regression-safe.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from src.xsmn_ensemble.ensemble_engine import compute_global_borda, BORDA_POINTS


class TestComputeGlobalBorda(unittest.TestCase):
    """Tests for the weighted Borda count aggregation engine."""

    def _make_result(self, model_name: str, province: str, pairs: list, status: str = "success"):
        return {
            "model_name": model_name,
            "province": province,
            "status": status,
            "top_pairs": [(p, float(5 - i)) for i, p in enumerate(pairs)],
        }

    def test_empty_results(self):
        """Empty model_results should return empty top_pairs."""
        out = compute_global_borda([], [])
        self.assertEqual(out["top_pairs"], [])
        self.assertEqual(out["contributing_models"], [])

    def test_all_failed_models(self):
        """All failed models should return empty top_pairs."""
        results = [
            self._make_result("freq_gap", "tp-hcm", [10, 20, 30], status="error"),
        ]
        out = compute_global_borda(results, [])
        self.assertEqual(out["top_pairs"], [])

    def test_single_model_ranking(self):
        """Single model: top 3 should follow model's ranking."""
        results = [
            self._make_result("xgboost_core", "tp-hcm", [42, 17, 88, 3, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        top_pairs = [p for p, _ in out["top_pairs"]]
        self.assertEqual(top_pairs[0], 42)  # Rank 1
        self.assertEqual(top_pairs[1], 17)  # Rank 2
        self.assertEqual(top_pairs[2], 88)  # Rank 3

    def test_xgboost_weight_dominates(self):
        """XGBoost (weight=2.0) should outweigh freq_gap (weight=1.0)."""
        results = [
            self._make_result("freq_gap", "tp-hcm", [10, 20, 30, 40, 50]),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 77, 66, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        top_pairs = [p for p, _ in out["top_pairs"]]
        # XGBoost's #1 (99) gets 2.0*5=10, freq_gap's #1 (10) gets 1.0*5=5
        self.assertEqual(top_pairs[0], 99)

    def test_consensus_bonus(self):
        """Pair appearing in >=2 models should get consensus silver bonus."""
        results = [
            self._make_result("freq_gap", "tp-hcm", [42, 20, 30, 40, 50]),
            self._make_result("markov", "tp-hcm", [42, 88, 77, 66, 55]),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 42, 66, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        # 42 appears in all 3 models → gold consensus bonus (+5.0)
        self.assertIn(42, [p for p, _ in out["top_pairs"]])
        self.assertIn(42, out["consensus_pairs"])

    def test_history_penalty_overdue(self):
        """Pair appearing >=2 times in recent tails should get penalty."""
        results = [
            self._make_result("xgboost_core", "tp-hcm", [42, 17, 88, 3, 55]),
        ]
        # 42 appeared 3 times in recent history → penalty
        out_penalty = compute_global_borda(results, [42, 42, 42], top_n_output=3)
        # 42 should still be ranked but with lower score
        out_clean = compute_global_borda(results, [], top_n_output=3)
        
        score_penalty = dict(out_penalty["top_pairs"]).get(42, 0)
        score_clean = dict(out_clean["top_pairs"]).get(42, 0)
        self.assertLess(score_penalty, score_clean)

    def test_history_sweetspot_bonus(self):
        """Pair appearing exactly 1 time should get sweetspot bonus."""
        results = [
            self._make_result("xgboost_core", "tp-hcm", [42, 17, 88, 3, 55]),
        ]
        out = compute_global_borda(results, [42], top_n_output=3)
        out_clean = compute_global_borda(results, [], top_n_output=3)
        
        score_sweet = dict(out["top_pairs"]).get(42, 0)
        score_clean = dict(out_clean["top_pairs"]).get(42, 0)
        # Sweetspot bonus (+2.0) > Potential bonus (+1.0)
        self.assertGreater(score_sweet, score_clean)

    def test_custom_weights_override(self):
        """Custom weights should override defaults."""
        results = [
            self._make_result("freq_gap", "tp-hcm", [10, 20, 30, 40, 50]),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 77, 66, 55]),
        ]
        # Give freq_gap massive weight
        custom_w = {"freq_gap": 10.0, "xgboost_core": 0.1}
        out = compute_global_borda(results, [], weights=custom_w, top_n_output=3)
        top_pairs = [p for p, _ in out["top_pairs"]]
        # freq_gap's #1 (10) gets 10*5=50, xgboost's #1 (99) gets 0.1*5=0.5
        self.assertEqual(top_pairs[0], 10)

    def test_fault_tolerance_partial_failure(self):
        """One model failing should not break ensemble."""
        results = [
            self._make_result("freq_gap", "tp-hcm", [10, 20, 30, 40, 50]),
            self._make_result("markov", "tp-hcm", [], status="error"),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 77, 66, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        self.assertTrue(len(out["top_pairs"]) == 3)

    def test_scoring_log_generated(self):
        """scoring_log should be present for top 3 pairs."""
        results = [
            self._make_result("xgboost_core", "tp-hcm", [42, 17, 88, 3, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        self.assertIn("scoring_log", out)
        self.assertIn("[42]", out["scoring_log"])


class TestBordaPoints(unittest.TestCase):
    """Test Borda point mapping."""

    def test_borda_points_mapping(self):
        self.assertEqual(BORDA_POINTS[1], 5)
        self.assertEqual(BORDA_POINTS[5], 1)
        self.assertNotIn(6, BORDA_POINTS)


if __name__ == "__main__":
    unittest.main()
