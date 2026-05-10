"""
test_decision_engine.py — Unit tests for DecisionEngine
P1#7: Ensure retrain decision logic is correct.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from unittest.mock import MagicMock, patch
from datetime import date
from src.agent.decision_engine import DecisionEngine, DecisionResult


class MockQueryBuilder:
    """Mock Supabase query builder chain."""
    def __init__(self, data=None):
        self._data = data or []
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def lte(self, *a, **kw): return self
    def gte(self, *a, **kw): return self
    def order(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
    @property
    def not_(self): return self
    def execute(self):
        result = MagicMock()
        result.data = self._data
        return result


class MockDB:
    """Mock LotteryDB with configurable responses per table."""
    def __init__(self, table_data=None):
        self._table_data = table_data or {}
        self.supabase = MagicMock()
        self.supabase.table = self._mock_table
    
    def _mock_table(self, name):
        data = self._table_data.get(name, [])
        return MockQueryBuilder(data)


class TestDecisionEngine(unittest.TestCase):
    """Tests for DecisionEngine.analyze and _pick_strategy."""

    def test_hit_today_no_action(self):
        """If hit today, should return no_action."""
        engine = DecisionEngine()
        db = MockDB()
        result = engine.analyze("XSMB", None, 0, True, db, date(2026, 5, 10))
        self.assertFalse(result.should_retrain)
        self.assertEqual(result.action_type, "no_action")

    def test_miss_but_metric_ok_skipped(self):
        """Miss today but metrics above threshold → skipped."""
        engine = DecisionEngine(auc_threshold=0.55, hit_rate_threshold=0.40)
        db = MockDB({
            "model_registry": [{"metric_auc": 0.65, "metric_hit_rate": 0.50, "trained_at": "2026-05-01T00:00:00Z", "version": "v3"}],
        })
        result = engine.analyze("XSMB", None, 0, False, db, date(2026, 5, 10))
        self.assertFalse(result.should_retrain)
        self.assertEqual(result.action_type, "skipped")

    def test_miss_metric_bad_no_model_retrain(self):
        """Miss + no model found (no metrics) → retrain triggered."""
        engine = DecisionEngine()
        db = MockDB({
            "model_registry": [],
            "agent_actions": [],
            "prediction_results": [],
        })
        result = engine.analyze("XSMB", None, 0, False, db, date(2026, 5, 10))
        self.assertTrue(result.should_retrain)
        self.assertEqual(result.action_type, "retrain_triggered")


class TestPickStrategy(unittest.TestCase):
    """Tests for strategy selection logic."""

    def test_low_fails_boost(self):
        self.assertEqual(DecisionEngine._pick_strategy(2, 0), "boost_estimators")

    def test_medium_fails_conservative(self):
        self.assertEqual(DecisionEngine._pick_strategy(5, 0), "conservative")

    def test_high_fails_full_reset(self):
        self.assertEqual(DecisionEngine._pick_strategy(7, 0), "full_reset")

    def test_no_improve_1_conservative(self):
        self.assertEqual(DecisionEngine._pick_strategy(3, 1), "conservative")

    def test_no_improve_2_scale_weight(self):
        self.assertEqual(DecisionEngine._pick_strategy(3, 2), "scale_weight")

    def test_no_improve_3_full_reset(self):
        self.assertEqual(DecisionEngine._pick_strategy(3, 3), "full_reset")

    def test_no_improve_1_high_fails(self):
        """no_improve=1 + consecutive_fails>=5 → full_reset."""
        self.assertEqual(DecisionEngine._pick_strategy(5, 1), "full_reset")


if __name__ == "__main__":
    unittest.main()
