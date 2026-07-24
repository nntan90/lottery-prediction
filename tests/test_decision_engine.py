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
from src.agent.hyperparameter_strategy import build_train_args, recommend_params


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

    def test_hit_today_triggers_maintain_retrain(self):
        """Continuous learning retrains after a hit with maintain strategy."""
        engine = DecisionEngine()
        db = MockDB()
        result = engine.analyze("XSMB", None, 0, True, db, date(2026, 5, 10))
        self.assertTrue(result.should_retrain)
        self.assertEqual(result.action_type, "retrain_triggered")
        self.assertEqual(result.strategy, "maintain")

    def test_miss_but_metric_ok_still_retrains_for_continuous_learning(self):
        """Continuous learning retrains on miss even when metrics are OK."""
        engine = DecisionEngine(auc_threshold=0.55, hit_rate_threshold=0.40)
        db = MockDB({
            "model_registry": [{"metric_auc": 0.65, "metric_hit_rate": 0.50, "trained_at": "2026-05-01T00:00:00Z", "version": "v3"}],
        })
        result = engine.analyze("XSMB", None, 0, False, db, date(2026, 5, 10))
        self.assertTrue(result.should_retrain)
        self.assertEqual(result.action_type, "retrain_triggered")
        self.assertEqual(result.strategy, "boost_estimators")

    def test_three_recent_misses_trigger_retrain_even_when_metric_ok(self):
        """3 kỳ gần nhất đều miss → retrain dù validation metric cũ vẫn OK."""
        engine = DecisionEngine(auc_threshold=0.55, hit_rate_threshold=0.40)
        db = MockDB({
            "model_registry": [{
                "metric_auc": 0.65,
                "metric_hit_rate": 0.50,
                "trained_at": "2026-05-01T00:00:00Z",
                "version": "v3",
            }],
            "prediction_results": [
                {"prediction_date": "2026-05-09", "hit": False},
                {"prediction_date": "2026-05-02", "hit": False},
                {"prediction_date": "2026-04-25", "hit": False},
            ],
            "agent_actions": [],
        })
        result = engine.analyze("XSMN", "tp-hcm", 5, False, db, date(2026, 5, 9))

        self.assertTrue(result.should_retrain)
        self.assertEqual(result.action_type, "retrain_triggered")
        self.assertEqual(result.consecutive_fails, 3)
        self.assertIn("3_miss_streak", result.reason)

    def test_three_recent_misses_override_cooldown(self):
        """3-miss streak là hard trigger để sửa model sai ngay."""
        engine = DecisionEngine(min_days_since_retrain=14)
        db = MockDB({
            "model_registry": [{
                "metric_auc": 0.66,
                "metric_hit_rate": 0.55,
                "trained_at": "2026-05-01T00:00:00Z",
                "version": "v3",
            }],
            "prediction_results": [
                {"prediction_date": "2026-05-09", "hit": False},
                {"prediction_date": "2026-05-02", "hit": False},
                {"prediction_date": "2026-04-25", "hit": False},
            ],
            "agent_actions": [{"action_date": "2026-05-09", "old_metric_auc": 0.66}],
        })
        result = engine.analyze("XSMN", "tp-hcm", 5, False, db, date(2026, 5, 9))

        self.assertTrue(result.should_retrain)
        self.assertIn("3_miss_streak", result.reason)

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

    def test_twice_weekly_station_miss_streak_isolated_by_weekday(self):
        """TP.HCM Monday streak must not stop at a Saturday hit."""
        engine = DecisionEngine()
        db = MockDB({
            "prediction_results": [
                {"prediction_date": "2026-07-20", "hit": False},  # Monday
                {"prediction_date": "2026-07-18", "hit": True},   # Saturday
                {"prediction_date": "2026-07-13", "hit": False},  # Monday
                {"prediction_date": "2026-07-11", "hit": True},   # Saturday
                {"prediction_date": "2026-07-06", "hit": True},   # Monday
            ],
        })

        fails = engine._count_consecutive_fails(
            db, "XSMN", "tp-hcm", date(2026, 7, 20), weekday=0
        )

        self.assertEqual(fails, 2)


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


class TestRecommendParams(unittest.TestCase):
    """Tests for context-aware hyperparameter recommendation."""

    def test_three_miss_streak_forces_retrain_and_tunes_params(self):
        old_params, new_params = recommend_params(
            "boost_estimators",
            region="XSMN",
            consecutive_fails=3,
            old_auc=0.51,
            old_hit_rate=0.20,
        )
        args = build_train_args("XSMN", "tp-hcm", 5, new_params)

        self.assertEqual(old_params["n_estimators"], 300)
        self.assertTrue(new_params["_force"])
        self.assertGreaterEqual(new_params["n_estimators"], 500)
        self.assertLessEqual(new_params["learning_rate"], 0.03)
        self.assertGreaterEqual(new_params["scale_pos_weight"], 2.5)
        self.assertLessEqual(new_params["max_depth"], 3)
        self.assertEqual(new_params["_min_draws"], 36)
        self.assertIn("--force", args)
        self.assertIn("--min_draws", args)
        self.assertIn("36", args)

    def test_xsmb_three_miss_streak_uses_longer_training_window(self):
        _, new_params = recommend_params(
            "boost_estimators",
            region="XSMB",
            consecutive_fails=3,
        )
        args = build_train_args("XSMB", None, 1, new_params)

        self.assertEqual(new_params["_min_draws"], 60)
        self.assertIn("--min_draws", args)
        self.assertIn("60", args)


if __name__ == "__main__":
    unittest.main()
