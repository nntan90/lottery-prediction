"""
test_ensemble_engine.py — Unit tests for compute_global_borda
P1#7: Ensure ensemble scoring logic is correct and regression-safe.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
import numpy as np
from src.xsmn_ensemble.ensemble_engine import (
    compute_global_borda,
    compute_xsmn_province_representative_ensemble,
    compute_xsmn_merged_combo_selector_ensemble,
    _combo_history_strength,
    _select_best_two_of_three_combo,
    BORDA_POINTS,
)
from src.xsmb_ensemble.ensemble_engine import compute_xsmb_ensemble
from src.xsmb_ensemble.model_chisquare_gof import (
    SUM_GROUP_CARDINALITIES,
    _p_strength,
    _positive_residual_scores,
)
from src.xsmb_ensemble.model_markov import _compress_context_by_frequency


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

    def test_xgboost_weight_dominates_over_markov(self):
        """XGBoost (weight=0.25) should outweigh markov (weight=0.20) for same rank."""
        results = [
            self._make_result("markov", "tp-hcm", [10, 20, 30, 40, 50]),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 77, 66, 55]),
        ]
        out = compute_global_borda(results, [], top_n_output=3)
        top_pairs = [p for p, _ in out["top_pairs"]]
        # CombSUM: XGB #1 (99) gets 0.25*5.0=1.25, Markov #1 (10) gets 0.20*5.0=1.0
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

    def test_xsmn_consensus_separates_sources_from_model_families(self):
        """Cross-province copies remain sources but not independent model votes."""
        results = [
            self._make_result("frequency", "tp-hcm", [42, 20, 30, 40, 50]),
            self._make_result("frequency", "dong-thap", [42, 21, 31, 41, 51]),
            self._make_result("markov", "tp-hcm", [42, 88, 77, 66, 55]),
        ]

        out = compute_global_borda(results, [], top_n_output=3, region="XSMN")
        candidate_42 = next(c for c in out["top_candidates"] if c["pair"] == 42)

        self.assertIn(42, [p for p, _ in out["top_pairs"]])
        self.assertNotIn(42, out["consensus_pairs"])
        self.assertEqual(candidate_42["unique_model_count"], 3)
        self.assertEqual(candidate_42["model_family_count"], 2)
        self.assertEqual(candidate_42["province_count"], 2)
        self.assertIn("frequency@tp-hcm", candidate_42["sources"])
        self.assertIn("frequency@dong-thap", candidate_42["sources"])
        self.assertIn("Freq/tp-hcm", out["candidate_log"])
        self.assertIn("Freq/dong-thap", out["candidate_log"])

    def test_xsmn_combo_selector_scores_merged_province_pool(self):
        """XSMN picker should choose a 3-number combo from the merged province pool."""
        results = [
            self._make_result("frequency", "ben-tre", [78, 72, 31, 64, 50]),
            self._make_result("gap_overdue", "ben-tre", [48, 6, 86, 4, 76]),
            self._make_result("markov", "ben-tre", [82, 64, 72, 31, 94]),
            self._make_result("cdm", "ben-tre", [68, 72, 20, 92, 32]),
            self._make_result("frequency", "vung-tau", [77, 85, 48, 20, 73]),
            self._make_result("gap_overdue", "vung-tau", [38, 69, 50, 49, 87]),
            self._make_result("lstm", "vung-tau", [79, 32, 73, 47, 43]),
            self._make_result("cdm", "vung-tau", [36, 1, 48, 14, 27]),
        ]

        out = compute_xsmn_merged_combo_selector_ensemble(
            results,
            provinces=["ben-tre", "vung-tau"],
            recent_tails_by_province={"ben-tre": [], "vung-tau": []},
            top_n_output=3,
            representatives_per_province=2,
        )

        reps_by_province = {}
        for rep in out["province_representatives"]:
            reps_by_province.setdefault(rep["province"], []).append(rep["pair"])

        self.assertEqual(len(reps_by_province["ben-tre"]), 2)
        self.assertEqual(len(reps_by_province["vung-tau"]), 2)
        self.assertEqual(len(out["top_pairs"]), 3)
        self.assertEqual(out["selected_province"], "all")
        self.assertGreater(out["combo_score"], 0)
        self.assertTrue(
            {p for p, _ in out["top_pairs"]}.issubset(
                {
                    int(candidate["pair"])
                    for candidate in out["merged_combo_output"]["candidate_pool"]
                }
            )
        )
        self.assertEqual(out["ensemble_method"], "xsmn_merged_combo_selector_v3.5")
        self.assertIn("combo selector", out["candidate_log"])

    def test_xsmn_representative_picker_merges_duplicate_pairs(self):
        """A pair represented by two provinces should be merged into one stronger candidate."""
        results = [
            self._make_result("frequency", "ben-tre", [42, 10, 11, 12, 13]),
            self._make_result("markov", "ben-tre", [42, 20, 21, 22, 23]),
            self._make_result("frequency", "vung-tau", [42, 30, 31, 32, 33]),
            self._make_result("markov", "vung-tau", [42, 40, 41, 42, 43]),
        ]

        out = compute_xsmn_province_representative_ensemble(
            results,
            provinces=["ben-tre", "vung-tau"],
            recent_tails_by_province={"ben-tre": [], "vung-tau": []},
            top_n_output=3,
            representatives_per_province=2,
        )

        top_candidate = out["top_candidates"][0]
        self.assertEqual(top_candidate["pair"], 42)
        self.assertEqual(top_candidate["province_count"], 2)
        self.assertIn(42, out["consensus_pairs"])

    def test_xsmn_combo_selector_shrinks_sparse_pair_history(self):
        """Four history rows must not override much stronger current evidence."""
        candidates = [
            {"pair": 10, "score": 10.0, "support_count": 6, "unique_model_count": 6},
            {"pair": 11, "score": 9.8, "support_count": 6, "unique_model_count": 6},
            {"pair": 12, "score": 9.6, "support_count": 6, "unique_model_count": 6},
            {"pair": 20, "score": 9.4, "support_count": 6, "unique_model_count": 6},
            {"pair": 21, "score": 9.2, "support_count": 6, "unique_model_count": 6},
            {"pair": 22, "score": 9.0, "support_count": 6, "unique_model_count": 6},
        ]
        history = [{20, 21}, {20, 22}, {21, 22}, {20, 21, 22}]

        out = _select_best_two_of_three_combo(
            candidates,
            top_n_output=3,
            candidate_pool_size=6,
            history_tail_sets=history,
        )

        self.assertEqual({p for p, _ in out["top_pairs"]}, {10, 11, 12})
        self.assertEqual(out["history_strength"], 0.0)
        self.assertGreater(_combo_history_strength((20, 21, 22), history), 0.0)
        self.assertEqual(out["score_type"], "ranking_score_uncalibrated")

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
            self._make_result("frequency", "tp-hcm", [10, 20, 30, 40, 50]),
            self._make_result("xgboost_core", "tp-hcm", [99, 88, 77, 66, 55]),
        ]
        # Give frequency massive weight
        custom_w = {"frequency": 10.0, "xgboost_core": 0.1}
        out = compute_global_borda(results, [], weights=custom_w, top_n_output=3)
        top_pairs = [p for p, _ in out["top_pairs"]]
        # CombSUM: frequency #1 (10) gets 10.0*5.0=50, xgboost #1 (99) gets 0.1*5.0=0.5
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
        self.assertEqual(BORDA_POINTS[6], 0.5)
        self.assertEqual(BORDA_POINTS[10], 0.1)
        self.assertNotIn(11, BORDA_POINTS)


class TestComputeXsmbEnsemble(unittest.TestCase):
    """Tests for the XSMB dedicated 7-model ensemble engine."""

    def _make_result(self, model_name: str, pairs: list, status: str = "success"):
        return {
            "model_name": model_name,
            "province": None,
            "status": status,
            "top_pairs": [(p, float(10 - i)) for i, p in enumerate(pairs)],
        }

    def test_xsmb_single_pool_counts_unique_models(self):
        """XSMB should count unique model sources in one shared regional pool."""
        results = [
            self._make_result("frequency", [42, 10, 11, 12, 13, 14, 15, 16, 17, 18]),
            self._make_result("gap_overdue", [42, 20, 21, 22, 23, 24, 25, 26, 27, 28]),
            self._make_result("markov", [42, 30, 31, 32, 33, 34, 35, 36, 37, 38]),
            self._make_result("xgboost_core", [99, 42, 41, 40, 39, 37, 36, 35, 34, 33]),
        ]

        out = compute_xsmb_ensemble(results, [], top_n_output=3, extended_tails=[])
        candidate_42 = next(c for c in out["top_candidates"] if c["pair"] == 42)
        candidate_pairs = {c["pair"] for c in out["top_candidates"]}
        final_pairs = {p for p, _ in out["top_pairs"]}

        self.assertIn(42, final_pairs)
        self.assertIn(42, out["consensus_pairs"])
        self.assertEqual(candidate_42["unique_model_count"], 4)
        self.assertIn("frequency", candidate_42["models"])
        self.assertIn("gap_overdue", candidate_42["models"])
        self.assertIn("Freq", out["candidate_log"])
        self.assertTrue(final_pairs.issubset(candidate_pairs))

    def test_xsmb_medals_are_sorted_by_final_score(self):
        results = [
            self._make_result(
                "frequency",
                [83, 10, 20, 30, 40, 50, 60, 70, 80, 90],
            ),
        ]

        out = compute_xsmb_ensemble(results, [], top_n_output=3)
        displayed_scores = [score for _, score in out["top_pairs"]]

        self.assertEqual(displayed_scores, sorted(displayed_scores, reverse=True))

    def test_markov_context_compression_uses_frequency_not_numeric_value(self):
        frequency = np.zeros(100, dtype=float)
        frequency[90] = 10.0
        frequency[80] = 8.0
        frequency[1] = 1.0

        compressed = _compress_context_by_frequency(
            {1, 80, 90},
            frequency,
            top_k=2,
        )

        self.assertEqual(compressed, [90, 80])

    def test_chigof_digit_sum_expectation_accounts_for_cardinality(self):
        proportional_counts = SUM_GROUP_CARDINALITIES * 10.0

        residuals = _positive_residual_scores(
            proportional_counts,
            expected_weights=SUM_GROUP_CARDINALITIES,
        )
        strength = _p_strength(
            proportional_counts,
            expected_weights=SUM_GROUP_CARDINALITIES,
        )

        self.assertTrue(np.allclose(residuals, 0.0))
        self.assertAlmostEqual(strength, 0.0)


if __name__ == "__main__":
    unittest.main()
