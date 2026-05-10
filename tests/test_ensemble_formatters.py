"""
test_ensemble_formatters.py — Regression tests for format_ensemble_result & format_model_prediction_log.

Verifies:
  - format_ensemble_result produces correct DB-ready dict
  - Padding when fewer than 3 pairs
  - format_model_prediction_log produces correct model log dict
  - Padding when fewer than 5 pairs
  - model_type classification (rule_based vs ml)
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.xsmn_ensemble.ensemble_engine import format_ensemble_result, format_model_prediction_log


class TestFormatEnsembleResult(unittest.TestCase):
    """Tests for format_ensemble_result."""

    def _make_ensemble_output(self, top_pairs):
        return {
            "top_pairs": top_pairs,
            "contributing_models": ["freq_gap_tp-hcm", "markov_tp-hcm"],
            "ensemble_method": "expert_borda_history_v2",
            "borda_details": {},
            "consensus_pairs": [],
            "scoring_log": "test log",
        }

    def test_normal_3_pairs(self):
        """3 pairs should produce correct prediction_results record."""
        out = self._make_ensemble_output([(42, 5.36), (17, 3.0), (88, 2.75)])
        result = format_ensemble_result("XSMN", "tp-hcm", out, date(2026, 5, 10))

        self.assertEqual(result["prediction_date"], "2026-05-10")
        self.assertEqual(result["region"], "XSMN")
        self.assertEqual(result["province"], "tp-hcm")
        self.assertEqual(result["pair_1"], 42)
        self.assertEqual(result["pair_2"], 17)
        self.assertEqual(result["pair_3"], 88)
        self.assertAlmostEqual(result["prob_1"], 5.36)
        self.assertEqual(result["model_version"], "ensemble_v3.1")
        self.assertIsNone(result["hit"])  # Not verified yet
        self.assertIn("scoring_log", result)

    def test_fewer_than_3_pairs_padded(self):
        """Should pad to 3 with (-1, 0.0) when fewer than 3 pairs."""
        out = self._make_ensemble_output([(42, 5.0)])
        result = format_ensemble_result("XSMN", None, out, date(2026, 5, 10))

        self.assertEqual(result["pair_1"], 42)
        self.assertEqual(result["pair_2"], -1)
        self.assertEqual(result["pair_3"], -1)
        self.assertEqual(result["prob_2"], 0.0)

    def test_empty_pairs_padded(self):
        """Should handle completely empty pairs."""
        out = self._make_ensemble_output([])
        result = format_ensemble_result("XSMN", None, out, date(2026, 5, 10))

        self.assertEqual(result["pair_1"], -1)
        self.assertEqual(result["pair_2"], -1)
        self.assertEqual(result["pair_3"], -1)

    def test_province_none_preserved(self):
        """Province=None (XSMB/global) should be preserved as None."""
        out = self._make_ensemble_output([(1, 1.0), (2, 1.0), (3, 1.0)])
        result = format_ensemble_result("XSMB", None, out, date(2026, 5, 10))
        self.assertIsNone(result["province"])

    def test_date_string_input(self):
        """Should handle string date input (no .isoformat())."""
        out = self._make_ensemble_output([(1, 1.0), (2, 1.0), (3, 1.0)])
        result = format_ensemble_result("XSMN", "tp-hcm", out, "2026-05-10")
        self.assertEqual(result["prediction_date"], "2026-05-10")


class TestFormatModelPredictionLog(unittest.TestCase):
    """Tests for format_model_prediction_log."""

    def _make_model_result(self, name, pairs, status="success"):
        return {
            "model_name": name,
            "province": "tp-hcm",
            "top_pairs": [(p, float(5 - i)) for i, p in enumerate(pairs)],
            "status": status,
            "execution_time_ms": 42,
            "error_message": None,
        }

    def test_normal_5_pairs(self):
        """5 pairs should produce correct model_predictions record."""
        mr = self._make_model_result("freq_gap", [10, 20, 30, 40, 50])
        log = format_model_prediction_log("XSMN", "tp-hcm", mr, date(2026, 5, 10))

        self.assertEqual(log["model_name"], "freq_gap")
        self.assertEqual(log["pair_1"], 10)
        self.assertEqual(log["pair_5"], 50)
        self.assertAlmostEqual(log["score_1"], 5.0)
        self.assertEqual(log["status"], "success")

    def test_model_type_rule_based(self):
        """freq_gap and markov should be classified as rule_based."""
        for name in ("freq_gap", "markov"):
            mr = self._make_model_result(name, [1, 2, 3, 4, 5])
            log = format_model_prediction_log("XSMN", None, mr, date(2026, 5, 10))
            self.assertEqual(log["model_type"], "rule_based", f"{name} should be rule_based")

    def test_model_type_ml(self):
        """xgboost_core should be classified as ml."""
        mr = self._make_model_result("xgboost_core", [1, 2, 3, 4, 5])
        log = format_model_prediction_log("XSMN", None, mr, date(2026, 5, 10))
        self.assertEqual(log["model_type"], "ml")

    def test_fewer_than_5_pairs_padded(self):
        """Should pad to 5 with None when fewer pairs."""
        mr = self._make_model_result("freq_gap", [10, 20])
        log = format_model_prediction_log("XSMN", "tp-hcm", mr, date(2026, 5, 10))
        self.assertEqual(log["pair_1"], 10)
        self.assertEqual(log["pair_2"], 20)
        self.assertIsNone(log["pair_3"])
        self.assertIsNone(log["pair_4"])
        self.assertIsNone(log["pair_5"])

    def test_error_model_result(self):
        """Error model results should preserve error_message."""
        mr = {
            "model_name": "xgboost_core",
            "province": None,
            "top_pairs": [],
            "status": "error",
            "execution_time_ms": 10,
            "error_message": "Model file not found",
        }
        log = format_model_prediction_log("XSMN", None, mr, date(2026, 5, 10))
        self.assertEqual(log["status"], "error")
        self.assertEqual(log["error_message"], "Model file not found")


if __name__ == "__main__":
    unittest.main()
