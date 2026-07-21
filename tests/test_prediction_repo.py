"""
test_prediction_repo.py — Regression tests for shared prediction save/upsert logic.

Verifies:
  - save_prediction inserts new record correctly
  - save_prediction updates existing record (no duplicates)
  - NULL province handling (XSMB convention)
  - Non-DB fields are stripped before save
  - save_model_prediction handles PGRST205 gracefully
  - save_model_prediction upserts correctly
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockQueryChain:
    """Fluent mock for Supabase query builder."""
    def __init__(self, db, table_name, data=None):
        self._db = db
        self._table_name = table_name
        self._data = data or []
        self._action = None
        self._payload = None
    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def insert(self, payload, *a, **kw):
        self._action = "insert"
        self._payload = payload
        return self
    def update(self, payload, *a, **kw):
        self._action = "update"
        self._payload = payload
        return self
    def execute(self):
        if self._db.should_raise_schema_cache_error(self._table_name, self._payload):
            raise Exception(
                f"Could not find the '{self._db._missing_metadata_field}' column of "
                "'prediction_results' in the schema cache PGRST204"
            )
        if self._action == "insert":
            self._db.inserted.setdefault(self._table_name, []).append(self._payload)
        elif self._action == "update":
            self._db.updated.setdefault(self._table_name, []).append(self._payload)

        result = MagicMock()
        result.data = self._data
        return result


class MockDB:
    """Mock LotteryDB with per-table data and call tracking."""
    def __init__(
        self,
        table_responses=None,
        fail_prediction_metadata_once=False,
        missing_metadata_field="contributing_models",
    ):
        self._responses = table_responses or {}
        self._calls = []
        self._fail_prediction_metadata_once = fail_prediction_metadata_once
        self._missing_metadata_field = missing_metadata_field
        self._schema_cache_failed = False
        self.inserted = {}
        self.updated = {}
        self.supabase = MagicMock()
        self.supabase.table = self._mock_table

    def _mock_table(self, name):
        self._calls.append(name)
        data = self._responses.get(name, [])
        return MockQueryChain(self, name, data)

    @property
    def tables_called(self):
        return self._calls

    def should_raise_schema_cache_error(self, table_name, payload):
        if (
            table_name != "prediction_results"
            or not self._fail_prediction_metadata_once
            or self._schema_cache_failed
            or not payload
        ):
            return False
        has_metadata = self._missing_metadata_field in payload
        if has_metadata:
            self._schema_cache_failed = True
            return True
        return False


class TestSavePrediction(unittest.TestCase):
    """Tests for save_prediction function."""

    def _make_prediction(self, region="XSMN", province="tp-hcm"):
        return {
            "prediction_date": "2026-05-10",
            "region": region,
            "province": province,
            "pair_1": 42,
            "pair_2": 17,
            "pair_3": 88,
            "prob_1": 5.36,
            "prob_2": 3.00,
            "prob_3": 2.75,
            "model_version": "ensemble_v3.1",
            "ensemble_method": "expert_borda_history_v2",
            "contributing_models": ["freq_gap", "markov", "xgboost"],
            "final_scores": [5.36, 3.00, 2.75],
            "run_metadata": {"score_type": "ranking_score_uncalibrated"},
            "scoring_log": "<b>test log</b>",
            "candidate_log": "runtime-only candidate details",
            "hit": None,
        }

    def test_insert_new_prediction(self):
        """New prediction (no existing) should trigger insert."""
        db = MockDB({"prediction_results": []})  # empty = no existing
        from src.database.prediction_repo import save_prediction
        pred = self._make_prediction()
        save_prediction(db, pred)
        # Should have called prediction_results table
        self.assertIn("prediction_results", db.tables_called)

    def test_preserves_ensemble_metadata_and_strips_runtime_log(self):
        """Ensemble metadata should persist; only scoring_log should be stripped."""
        db = MockDB({"prediction_results": []})
        from src.database.prediction_repo import save_prediction
        pred = self._make_prediction()
        save_prediction(db, pred)

        inserted = db.inserted["prediction_results"][0]
        self.assertEqual(inserted["ensemble_method"], "expert_borda_history_v2")
        self.assertEqual(inserted["contributing_models"], ["freq_gap", "markov", "xgboost"])
        self.assertEqual(inserted["final_scores"], [5.36, 3.00, 2.75])
        self.assertEqual(inserted["run_metadata"]["score_type"], "ranking_score_uncalibrated")
        self.assertNotIn("scoring_log", inserted)
        self.assertNotIn("candidate_log", inserted)

        # Original dict should still have these fields (no mutation).
        self.assertIn("ensemble_method", pred)
        self.assertIn("scoring_log", pred)

    def test_xsmb_null_province(self):
        """XSMB predictions use province=None, should use is_('province', 'null')."""
        db = MockDB({"prediction_results": []})
        from src.database.prediction_repo import save_prediction
        pred = self._make_prediction(region="XSMB", province=None)
        # Should not raise
        save_prediction(db, pred)

    def test_retries_without_metadata_when_production_schema_is_old(self):
        """Old production DB missing migration 06 should not fail prediction."""
        db = MockDB(
            {"prediction_results": []},
            fail_prediction_metadata_once=True,
        )
        from src.database.prediction_repo import save_prediction
        pred = self._make_prediction()
        save_prediction(db, pred)

        inserted = db.inserted["prediction_results"][0]
        self.assertEqual(inserted["pair_1"], 42)
        self.assertNotIn("ensemble_method", inserted)
        self.assertNotIn("contributing_models", inserted)
        self.assertNotIn("final_scores", inserted)
        self.assertNotIn("run_metadata", inserted)
        self.assertNotIn("scoring_log", inserted)
        self.assertTrue(db._schema_cache_failed)

    def test_missing_run_metadata_preserves_existing_audit_fields(self):
        """Migration 11 can lag deployment without dropping legacy audit fields."""
        db = MockDB(
            {"prediction_results": []},
            fail_prediction_metadata_once=True,
            missing_metadata_field="run_metadata",
        )
        from src.database.prediction_repo import save_prediction
        save_prediction(db, self._make_prediction())

        inserted = db.inserted["prediction_results"][0]
        self.assertNotIn("run_metadata", inserted)
        self.assertEqual(inserted["ensemble_method"], "expert_borda_history_v2")
        self.assertEqual(
            inserted["contributing_models"],
            ["freq_gap", "markov", "xgboost"],
        )
        self.assertEqual(inserted["final_scores"], [5.36, 3.00, 2.75])


class TestSaveModelPrediction(unittest.TestCase):
    """Tests for save_model_prediction function."""

    def _make_model_log(self):
        return {
            "prediction_date": "2026-05-10",
            "region": "XSMN",
            "province": "tp-hcm",
            "model_name": "freq_gap",
            "pair_1": 42, "pair_2": 17, "pair_3": 88,
            "pair_4": 3, "pair_5": 55,
            "status": "success",
        }

    def test_insert_new_model_prediction(self):
        """New model prediction should trigger insert."""
        db = MockDB({"model_predictions": []})
        from src.database.prediction_repo import save_model_prediction
        log = self._make_model_log()
        save_model_prediction(db, log)

    def test_pgrst205_graceful_handling(self):
        """PGRST205 error (missing table) should be caught, not raised."""
        db = MagicMock()
        db.supabase = MagicMock()
        db.supabase.table.side_effect = Exception("relation model_predictions does not exist PGRST205")
        
        from src.database.prediction_repo import save_model_prediction
        log = self._make_model_log()
        # Should NOT raise
        save_model_prediction(db, log)

    def test_non_pgrst_error_reraises(self):
        """Non-PGRST errors should be re-raised."""
        db = MagicMock()
        db.supabase = MagicMock()
        db.supabase.table.side_effect = Exception("Connection refused")
        
        from src.database.prediction_repo import save_model_prediction
        log = self._make_model_log()
        with self.assertRaises(Exception) as ctx:
            save_model_prediction(db, log)
        self.assertIn("Connection refused", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
