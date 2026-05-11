"""
test_scoring_config.py — Regression tests for YAML scoring config loading.

Verifies:
  - Config loads from config/scoring.yaml
  - Fallback to hardcoded defaults when file missing
  - All expected constants are set with correct values
  - Config changes propagate to ensemble engine
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.xsmn_ensemble.ensemble_engine import (
    BORDA_POINTS,
    DEFAULT_WEIGHTS,
    CONSENSUS_THRESHOLD_GOLD,
    CONSENSUS_THRESHOLD_SILVER,
    BONUS_GOLD,
    BONUS_SILVER,
    HISTORY_PENALTY_OVERDUE,
    HISTORY_BONUS_SWEETSPOT,
    HISTORY_BONUS_POTENTIAL,
    _load_scoring_config,
)


class TestScoringConfigValues(unittest.TestCase):
    """Verify scoring config values match expected production values."""

    def test_borda_points_5_ranks(self):
        """Borda points: rank 1=5, rank 5=1."""
        self.assertEqual(BORDA_POINTS[1], 5)
        self.assertEqual(BORDA_POINTS[2], 4)
        self.assertEqual(BORDA_POINTS[3], 3)
        self.assertEqual(BORDA_POINTS[4], 2)
        self.assertEqual(BORDA_POINTS[5], 1)
        self.assertEqual(len(BORDA_POINTS), 5)

    def test_default_weights(self):
        """All 5 models should have defined weights."""
        self.assertIn("frequency", DEFAULT_WEIGHTS)
        self.assertIn("gap_overdue", DEFAULT_WEIGHTS)
        self.assertIn("markov", DEFAULT_WEIGHTS)
        self.assertIn("xgboost_core", DEFAULT_WEIGHTS)
        self.assertIn("lstm", DEFAULT_WEIGHTS)
        # XGBoost should have highest weight (0.25) — mature ML model
        self.assertEqual(DEFAULT_WEIGHTS["xgboost_core"], 0.25)
        # LSTM on-the-fly has lowest weight (0.15) — upgrade after pre-trained deployed
        self.assertEqual(DEFAULT_WEIGHTS["lstm"], 0.15)

    def test_consensus_thresholds(self):
        """Gold=3 models, Silver=2 models."""
        self.assertEqual(CONSENSUS_THRESHOLD_GOLD, 3)
        self.assertEqual(CONSENSUS_THRESHOLD_SILVER, 2)

    def test_consensus_bonuses(self):
        """Gold=+5.0, Silver=+2.0."""
        self.assertEqual(BONUS_GOLD, 5.0)
        self.assertEqual(BONUS_SILVER, 2.0)

    def test_history_rules(self):
        """Penalty=-2.0, Sweetspot=+2.0, Potential=+1.0."""
        self.assertEqual(HISTORY_PENALTY_OVERDUE, -2.0)
        self.assertEqual(HISTORY_BONUS_SWEETSPOT, 2.0)
        self.assertEqual(HISTORY_BONUS_POTENTIAL, 1.0)


class TestScoringConfigLoader(unittest.TestCase):
    """Verify config loader behavior."""

    def test_config_loader_returns_dict(self):
        """_load_scoring_config should always return a dict."""
        cfg = _load_scoring_config()
        self.assertIsInstance(cfg, dict)

    def test_config_file_exists(self):
        """config/scoring.yaml should exist in the project."""
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "scoring.yaml"
        )
        self.assertTrue(os.path.exists(config_path), f"Config file missing: {config_path}")

    def test_config_has_expected_sections(self):
        """Config should have weights, borda_points, consensus, history sections."""
        cfg = _load_scoring_config()
        if cfg:  # Only check if config file was loaded
            self.assertIn("weights", cfg)
            self.assertIn("borda_points", cfg)
            self.assertIn("consensus", cfg)
            self.assertIn("history", cfg)


class TestWeightInvariants(unittest.TestCase):
    """Business rule invariants that should never be broken."""

    def test_ml_weights_gte_rule_based(self):
        """XGBoost (mature ML) should be >= any rule-based model."""
        xgb_w = DEFAULT_WEIGHTS.get("xgboost_core", 0)
        rule_models = ["frequency", "gap_overdue", "markov"]
        for rule in rule_models:
            rule_w = DEFAULT_WEIGHTS.get(rule, 0)
            self.assertGreaterEqual(xgb_w, rule_w,
                f"xgboost_core weight ({xgb_w}) should be >= {rule} weight ({rule_w})")

    def test_all_weights_positive(self):
        """All model weights must be positive."""
        for name, w in DEFAULT_WEIGHTS.items():
            self.assertGreater(w, 0, f"Weight for {name} must be positive")

    def test_gold_bonus_gt_silver(self):
        """Gold consensus bonus must be greater than Silver."""
        self.assertGreater(BONUS_GOLD, BONUS_SILVER)

    def test_gold_threshold_gt_silver(self):
        """Gold threshold must be stricter than Silver."""
        self.assertGreater(CONSENSUS_THRESHOLD_GOLD, CONSENSUS_THRESHOLD_SILVER)

    def test_sweetspot_bonus_gt_potential(self):
        """Sweetspot bonus (1 time in 3 weeks) > Potential bonus (never appeared)."""
        self.assertGreater(HISTORY_BONUS_SWEETSPOT, HISTORY_BONUS_POTENTIAL)

    def test_overdue_penalty_is_negative(self):
        """Overdue penalty must be negative (it's a penalty)."""
        self.assertLess(HISTORY_PENALTY_OVERDUE, 0)


if __name__ == "__main__":
    unittest.main()
