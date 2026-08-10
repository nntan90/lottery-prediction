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
from datetime import date
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
    def neq(self, *a, **kw): return self
    def is_(self, *a, **kw): return self
    def limit(self, *a, **kw): return self
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


class TestShadowPredictionLifecycle(unittest.TestCase):
    def test_normalize_xsmb_combo_stores_one_aggregate_score_and_audit(self):
        from src.database.prediction_repo import normalize_xsmb_combo_shadow
        from src.xsmb_combo.domain import ComboSelectorResult, SelectorStatus

        result = ComboSelectorResult(
            status=SelectorStatus.SUCCESS,
            top_pairs=(12, 34, 56),
            objective_score=0.184321,
            candidate_pool=tuple(range(100)),
            contributing_models=("frequency", "markov"),
            skipped_models=("cdm",),
            evaluated_triples=161_700,
            active_weights=(("frequency", 0.4), ("markov", 0.6)),
            source_families=(
                ("frequency", "frequency_gap"),
                ("markov", "transition"),
            ),
        )

        record = normalize_xsmb_combo_shadow(
            result,
            target_date=date(2026, 7, 28),
            execution_source="github_actions",
        )

        self.assertEqual(record["region"], "XSMB")
        self.assertIsNone(record["province"])
        self.assertEqual(record["model_name"], "xsmb_combo_shadow")
        self.assertEqual(
            [record["pair_1"], record["pair_2"], record["pair_3"]],
            [12, 34, 56],
        )
        self.assertEqual(record["score_1"], 0.184321)
        self.assertIsNone(record["score_2"])
        self.assertIsNone(record["score_3"])
        self.assertEqual(
            record["score_semantics"],
            "combo_score_uncalibrated",
        )
        self.assertEqual(record["run_metadata"]["evaluated_triples"], 161_700)
        self.assertEqual(
            record["run_metadata"]["fusion_role"],
            "production_weighted_tie_break",
        )
        self.assertEqual(
            record["run_metadata"]["active_weights"],
            {"frequency": 0.4, "markov": 0.6},
        )

    def test_normalize_ddt_keeps_top_three_and_audit_metadata(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "uncalibrated",
                "model_name": "provincial_digit_transition_v1",
                "score_semantics": "merged_pair_hit_likelihood_uncalibrated",
                "data_cutoff": "2026-07-26",
                "selected_evidence": [
                    {"pair": 3, "estimated_likelihood_uncalibrated": 0.3},
                    {"pair": 12, "estimated_likelihood_uncalibrated": 0.2},
                    {"pair": 25, "estimated_likelihood_uncalibrated": 0.1},
                ],
            },
            model_name="ddt_shadow",
            target_date=date(2026, 7, 26),
            provinces=["tien-giang", "kien-giang"],
            execution_source="local_telegram",
            runtime_ms=1234,
            config_metadata={"oof_recent_anchors_per_province": 64},
        )

        self.assertEqual(
            [record["pair_1"], record["pair_2"], record["pair_3"]],
            [3, 12, 25],
        )
        self.assertEqual(record["prediction_mode"], "shadow")
        self.assertEqual(record["province"], "all")
        self.assertEqual(
            record["run_metadata"]["provinces"],
            ["tien-giang", "kien-giang"],
        )
        self.assertEqual(record["run_metadata"]["runtime_ms"], 1234)

    def test_invalid_success_is_normalized_to_error_without_false_top_three(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {"status": "success", "selected_evidence": [{"pair": 3}]},
            model_name="ddt_shadow",
            target_date="2026-07-26",
            provinces=("tien-giang", "kien-giang"),
            execution_source="local_telegram",
        )

        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error_message"], "invalid_shadow_top_3")
        self.assertIsNone(record["pair_1"])

    def test_normalize_relationship_keeps_ranking_scores_and_full_audit(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "success",
                "model_version": "relationship_v1",
                "score_semantics": "ranking_score_uncalibrated",
                "data_cutoff": "2026-08-02",
                "selected_evidence": [
                    {"pair": 11, "ranking_score_uncalibrated": 0.91},
                    {"pair": 25, "ranking_score_uncalibrated": 0.72},
                    {"pair": 3, "ranking_score_uncalibrated": 0.68},
                ],
                "run_metadata": {
                    "selected_anchor": 11,
                    "selected_combo": {"relationship_score": 0.61},
                },
            },
            model_name="relationship",
            target_date=date(2026, 8, 2),
            provinces=["tien-giang", "kien-giang"],
            execution_source="production_post_save",
            runtime_ms=321,
            config_metadata={"top_k_per_source": 5},
        )

        self.assertEqual(record["model_name"], "relationship")
        self.assertEqual(
            [record["pair_1"], record["pair_2"], record["pair_3"]],
            [11, 25, 3],
        )
        self.assertEqual(record["score_1"], 0.91)
        self.assertEqual(record["model_version"], "relationship_v1")
        self.assertEqual(record["run_metadata"]["selected_anchor"], 11)
        self.assertEqual(
            record["run_metadata"]["selected_combo"]["relationship_score"],
            0.61,
        )
        self.assertEqual(
            record["run_metadata"]["provinces"],
            ["tien-giang", "kien-giang"],
        )

    def test_shadow_status_fits_schema_and_preserves_producer_status(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "insufficient_candidate_diversity",
                "reason": "no_top_3_with_distinct_unit_digits",
            },
            model_name="relationship",
            target_date="2026-08-02",
            provinces=["tien-giang", "kien-giang"],
            execution_source="production_post_save",
        )

        self.assertEqual(record["status"], "insufficient")
        self.assertLessEqual(len(record["status"]), 20)
        self.assertEqual(
            record["run_metadata"]["producer_status"],
            "insufficient_candidate_diversity",
        )

    def test_relationship_persistence_rejects_same_unit_digit_top_three(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "success",
                "selected_evidence": [
                    {"pair": 2, "ranking_score_uncalibrated": 0.9},
                    {"pair": 32, "ranking_score_uncalibrated": 0.8},
                    {"pair": 42, "ranking_score_uncalibrated": 0.7},
                ],
            },
            model_name="relationship",
            target_date="2026-08-02",
            provinces=["tien-giang", "kien-giang"],
            execution_source="production_post_save",
        )

        self.assertEqual(record["status"], "error")
        self.assertEqual(
            record["error_message"],
            "invalid_relationship_unit_digits",
        )
        self.assertIsNone(record["pair_1"])

    def test_normalize_llm_gen_keeps_uncalibrated_scores_and_audit(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "success",
                "model_version": "llm_gen_v1",
                "score_semantics": "ranking_score_uncalibrated",
                "data_cutoff": "2026-08-04",
                "selected_evidence": [
                    {"pair": 11, "ranking_score_uncalibrated": 0.91},
                    {"pair": 25, "ranking_score_uncalibrated": 0.72},
                    {"pair": 3, "ranking_score_uncalibrated": 0.68},
                ],
                "run_metadata": {
                    "provider": "openai",
                    "provider_model": "gpt-5.6-sol",
                    "prompt_version": "llm_gen_prompt_v1",
                    "schema_version": "llm_gen_response_v1",
                    "input_hash": "abc123",
                },
            },
            model_name="llm_gen",
            target_date=date(2026, 8, 4),
            provinces=["vung-tau", "ben-tre"],
            execution_source="production_post_save",
            runtime_ms=432,
        )

        self.assertEqual(record["model_name"], "llm_gen")
        self.assertEqual(record["model_version"], "llm_gen_v1")
        self.assertEqual(record["score_semantics"], "ranking_score_uncalibrated")
        self.assertEqual(
            [record["pair_1"], record["pair_2"], record["pair_3"]],
            [11, 25, 3],
        )
        self.assertEqual(record["run_metadata"]["provider"], "openai")
        self.assertEqual(record["run_metadata"]["input_hash"], "abc123")

    def test_llm_gen_persistence_rejects_same_unit_digit_top_three(self):
        from src.database.prediction_repo import normalize_shadow_prediction

        record = normalize_shadow_prediction(
            {
                "status": "success",
                "selected_evidence": [
                    {"pair": 2, "ranking_score_uncalibrated": 0.9},
                    {"pair": 32, "ranking_score_uncalibrated": 0.8},
                    {"pair": 42, "ranking_score_uncalibrated": 0.7},
                ],
            },
            model_name="llm_gen",
            target_date="2026-08-04",
            provinces=["vung-tau", "ben-tre"],
            execution_source="production_post_save",
        )

        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error_message"], "invalid_llm_gen_unit_digits")
        self.assertIsNone(record["pair_1"])

    def test_public_reason_sanitizer_redacts_urls_and_tokens(self):
        from src.database.prediction_repo import sanitize_prediction_reason

        reason = sanitize_prediction_reason(
            "request failed https://private.example/path token=super-secret"
        )

        self.assertNotIn("private.example", reason)
        self.assertNotIn("super-secret", reason)
        self.assertIn("[redacted-url]", reason)
        self.assertIn("token=[redacted]", reason)

    def test_public_reason_sanitizer_redacts_provider_headers_and_env_keys(self):
        from src.database.prediction_repo import sanitize_prediction_reason

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "openai-secret",
                "ANTHROPIC_API_KEY": "anthropic-secret",
            },
        ):
            reason = sanitize_prediction_reason(
                "Authorization: Bearer openai-secret x-api-key=anthropic-secret"
            )

        self.assertNotIn("openai-secret", reason)
        self.assertNotIn("anthropic-secret", reason)
        self.assertGreaterEqual(reason.count("[redacted]"), 2)

    def test_public_reason_sanitizer_does_not_read_provider_key_environment(self):
        from src.database.prediction_repo import sanitize_prediction_reason

        reads = []

        def tracked_getenv(key, default=""):
            reads.append(key)
            return default

        with patch("src.database.prediction_repo.os.getenv", side_effect=tracked_getenv):
            reason = sanitize_prediction_reason(
                "OPENAI_API_KEY=secret Authorization: Bearer another-secret"
            )

        self.assertNotIn("secret", reason)
        self.assertNotIn("OPENAI_API_KEY", reads)
        self.assertNotIn("ANTHROPIC_API_KEY", reads)

    def test_reason_sanitizer_reads_only_selected_agentrouter_key_value(self):
        from src.database.prediction_repo import sanitize_prediction_reason

        values = {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            "AGENTROUTER_API_KEY": "raw-router-secret",
            "OPENAI_API_KEY": "unselected-official-secret",
            "ANTHROPIC_API_KEY": "unselected-anthropic-secret",
        }
        reads = []

        def tracked_getenv(key, default=""):
            reads.append(key)
            return values.get(key, default)

        with patch("src.database.prediction_repo.os.getenv", side_effect=tracked_getenv):
            reason = sanitize_prediction_reason(
                "unexpected transport failure raw-router-secret"
            )

        self.assertNotIn("raw-router-secret", reason)
        self.assertIn("[redacted]", reason)
        self.assertIn("AGENTROUTER_API_KEY", reads)
        self.assertNotIn("OPENAI_API_KEY", reads)
        self.assertNotIn("ANTHROPIC_API_KEY", reads)

    def test_shadow_tracking_schema_preflight_detects_available_columns(self):
        from src.database.prediction_repo import shadow_tracking_schema_ready

        self.assertTrue(shadow_tracking_schema_ready(MockDB({"model_predictions": []})))

    def test_later_failure_does_not_downgrade_existing_success(self):
        from src.database.prediction_repo import save_shadow_prediction

        db = MockDB(
            {
                "model_predictions": [
                    {"id": 9, "status": "success", "model_name": "ddt_shadow"}
                ]
            }
        )
        saved = save_shadow_prediction(
            db,
            {
                "prediction_date": "2026-07-26",
                "model_name": "ddt_shadow",
                "status": "error",
            },
        )

        self.assertFalse(saved)
        self.assertNotIn("model_predictions", db.updated)

    def test_shadow_save_falls_back_to_legacy_columns(self):
        from src.database.prediction_repo import save_shadow_prediction

        class LegacyQuery(MockQueryChain):
            def execute(self):
                if self._payload and "prediction_mode" in self._payload:
                    raise Exception(
                        "Could not find the 'prediction_mode' column of "
                        "'model_predictions' in the schema cache PGRST204"
                    )
                return super().execute()

        class LegacyDB(MockDB):
            def _mock_table(self, name):
                self._calls.append(name)
                return LegacyQuery(self, name, self._responses.get(name, []))

        db = LegacyDB({"model_predictions": []})
        record = {
            "prediction_date": "2026-07-26",
            "region": "XSMN",
            "province": "all",
            "model_name": "ddt_shadow",
            "model_type": "shadow",
            "pair_1": 3,
            "status": "success",
            "prediction_mode": "shadow",
            "run_metadata": {"provinces": ["a", "b"]},
            "hit_count": None,
            "combo_hit": None,
            "verified_at": None,
        }

        self.assertTrue(save_shadow_prediction(db, record))
        inserted = db.inserted["model_predictions"][0]
        self.assertEqual(inserted["pair_1"], 3)
        self.assertNotIn("prediction_mode", inserted)
        self.assertNotIn("run_metadata", inserted)

    def test_llm_gen_never_falls_back_to_legacy_columns(self):
        from src.database.prediction_repo import save_shadow_prediction

        class LegacyQuery(MockQueryChain):
            def execute(self):
                if self._payload and "prediction_mode" in self._payload:
                    raise Exception(
                        "Could not find the 'prediction_mode' column of "
                        "'model_predictions' in the schema cache PGRST204"
                    )
                return super().execute()

        class LegacyDB(MockDB):
            def _mock_table(self, name):
                self._calls.append(name)
                return LegacyQuery(self, name, self._responses.get(name, []))

        db = LegacyDB({"model_predictions": []})
        record = {
            "prediction_date": "2026-08-04",
            "region": "XSMN",
            "province": "all",
            "model_name": "llm_gen",
            "model_type": "shadow",
            "pair_1": 11,
            "pair_2": 25,
            "pair_3": 3,
            "status": "success",
            "prediction_mode": "shadow",
            "run_metadata": {"input_hash": "abc123"},
        }

        self.assertFalse(save_shadow_prediction(db, record))
        self.assertNotIn("model_predictions", db.inserted)

    def test_llm_gen_existing_success_rejects_different_run_identity(self):
        from src.database.prediction_repo import save_shadow_prediction

        existing = {
            "id": 9,
            "status": "success",
            "model_name": "llm_gen",
            "pair_1": 11,
            "pair_2": 25,
            "pair_3": 3,
            "run_metadata": {
                "provider": "openai",
                "provider_model": "gpt-5.6-sol",
                "prompt_version": "llm_gen_prompt_v1",
                "schema_version": "llm_gen_response_v1",
                "input_hash": "old-hash",
                "provinces": ["a", "b"],
            },
        }
        db = MockDB({"model_predictions": [existing]})
        incoming = {
            **existing,
            "prediction_date": "2026-08-04",
            "region": "XSMN",
            "province": "all",
            "pair_1": 12,
            "pair_2": 26,
            "pair_3": 4,
            "run_metadata": {**existing["run_metadata"], "input_hash": "new-hash"},
        }

        self.assertFalse(save_shadow_prediction(db, incoming))
        self.assertNotIn("model_predictions", db.updated)

    def test_llm_gen_existing_success_rejects_changed_config(self):
        from src.database.prediction_repo import save_shadow_prediction

        config = {
            "mode": "shadow",
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "max_output_tokens": 2000,
        }
        existing = {
            "id": 9,
            "status": "success",
            "model_name": "llm_gen",
            "model_version": "llm_gen_v1",
            "pair_1": 11,
            "pair_2": 25,
            "pair_3": 3,
            "score_1": 0.9,
            "score_2": 0.8,
            "score_3": 0.7,
            "run_metadata": {
                "provider": "openai",
                "provider_model": "gpt-5.6-sol",
                "prompt_version": "llm_gen_prompt_v1",
                "schema_version": "llm_gen_response_v1",
                "input_hash": "same-hash",
                "provinces": ["a", "b"],
                "config": config,
            },
        }
        db = MockDB({"model_predictions": [existing]})
        incoming = {
            **existing,
            "prediction_date": "2026-08-04",
            "region": "XSMN",
            "province": "all",
            "run_metadata": {
                **existing["run_metadata"],
                "config": {**config, "max_output_tokens": 1000},
            },
        }

        self.assertFalse(save_shadow_prediction(db, incoming))
        self.assertNotIn("model_predictions", db.updated)

    def test_llm_gen_identity_rejects_changed_backend_model_and_legacy_wire(self):
        from src.database.prediction_repo import _same_llm_gen_identity

        base = {
            "model_version": "llm_gen_v1",
            "run_metadata": {
                "provider": "openai",
                "provider_model": "gpt-5.6-sol",
                "api_backend": "official",
                "wire_api": "responses",
                "prompt_version": "llm_gen_prompt_v1",
                "schema_version": "llm_gen_response_v1",
                "input_hash": "same-hash",
                "config": {
                    "provider": "openai",
                    "provider_model": "gpt-5.6-sol",
                    "api_backend": "official",
                    "wire_api": "responses",
                },
            },
        }
        agentrouter = {
            **base,
            "run_metadata": {
                **base["run_metadata"],
                "provider_model": "gpt-5.6",
                "api_backend": "agentrouter",
                "wire_api": "responses",
                "config": {
                    **base["run_metadata"]["config"],
                    "provider_model": "gpt-5.6",
                    "api_backend": "agentrouter",
                    "wire_api": "responses",
                },
            },
        }
        legacy_agentrouter = {
            **agentrouter,
            "run_metadata": {
                **agentrouter["run_metadata"],
                "provider_model": "gpt-5.6-sol",
                "wire_api": "chat_completions",
                "config": {
                    **agentrouter["run_metadata"]["config"],
                    "provider_model": "gpt-5.6-sol",
                    "wire_api": "chat_completions",
                },
            },
        }

        self.assertFalse(_same_llm_gen_identity(base, agentrouter))
        self.assertFalse(_same_llm_gen_identity(agentrouter, legacy_agentrouter))

    def test_llm_gen_unique_insert_race_never_overwrites_first_success(self):
        from src.database.prediction_repo import save_shadow_prediction

        config = {
            "mode": "shadow",
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "max_output_tokens": 2000,
        }
        raced = {
            "id": 10,
            "status": "success",
            "model_name": "llm_gen",
            "model_version": "llm_gen_v1",
            "pair_1": 11,
            "pair_2": 25,
            "pair_3": 3,
            "score_1": 0.91,
            "score_2": 0.82,
            "score_3": 0.73,
            "run_metadata": {
                "provider": "openai",
                "provider_model": "gpt-5.6-sol",
                "prompt_version": "llm_gen_prompt_v1",
                "schema_version": "llm_gen_response_v1",
                "input_hash": "same-hash",
                "provinces": ["a", "b"],
                "config": config,
            },
        }

        class RaceQuery:
            def __init__(self, database):
                self.database = database
                self.action = "select"

            def select(self, *_args, **_kwargs): return self
            def eq(self, *_args, **_kwargs): return self
            def neq(self, *_args, **_kwargs): return self
            def is_(self, *_args, **_kwargs): return self
            def limit(self, *_args, **_kwargs): return self

            def insert(self, _payload, *_args, **_kwargs):
                self.action = "insert"
                return self

            def update(self, _payload, *_args, **_kwargs):
                self.database.update_attempted = True
                self.action = "update"
                return self

            def execute(self):
                if self.action == "insert":
                    raise Exception("duplicate key violates unique constraint 23505")
                if self.action == "update":
                    raise AssertionError("concurrent canonical row must not be updated")
                self.database.select_count += 1
                data = [] if self.database.select_count == 1 else [raced]
                return type("Response", (), {"data": data})()

        class RaceDB:
            def __init__(self):
                self.select_count = 0
                self.update_attempted = False
                self.supabase = type(
                    "Supabase",
                    (),
                    {"table": lambda _self, _name: RaceQuery(self)},
                )()

        incoming = {
            **raced,
            "prediction_date": "2026-08-04",
            "region": "XSMN",
            "province": "all",
            "score_1": 0.89,
            "score_2": 0.80,
            "score_3": 0.70,
        }
        incoming.pop("id")
        db = RaceDB()

        self.assertFalse(save_shadow_prediction(db, incoming))
        self.assertFalse(db.update_attempted)

    def test_same_success_retry_preserves_existing_verification(self):
        from src.database.prediction_repo import save_shadow_prediction

        existing = {
            "id": 9,
            "status": "success",
            "model_name": "ddt_shadow",
            "pair_1": 25,
            "pair_2": 3,
            "pair_3": 12,
            "run_metadata": {"provinces": ["a", "b"]},
            "hit": True,
            "matched_pairs": [3, 12],
            "hit_count": 2,
            "combo_hit": True,
            "verified_at": "2026-07-26T14:00:00+00:00",
        }
        db = MockDB({"model_predictions": [existing]})
        incoming = {
            "prediction_date": "2026-07-26",
            "region": "XSMN",
            "province": "all",
            "model_name": "ddt_shadow",
            "status": "success",
            "pair_1": 3,
            "pair_2": 12,
            "pair_3": 25,
            "run_metadata": {"provinces": ["a", "b"]},
            "hit": None,
            "matched_pairs": None,
            "hit_count": None,
            "combo_hit": None,
            "verified_at": None,
        }

        self.assertTrue(save_shadow_prediction(db, incoming))
        updated = db.updated["model_predictions"][0]
        for field in (
            "hit",
            "matched_pairs",
            "hit_count",
            "combo_hit",
            "verified_at",
        ):
            self.assertNotIn(field, updated)

    def test_verified_shadow_rejects_retry_with_different_top_three(self):
        from src.database.prediction_repo import save_shadow_prediction

        existing = {
            "id": 9,
            "status": "success",
            "model_name": "xsmb_combo_shadow",
            "pair_1": 12,
            "pair_2": 34,
            "pair_3": 56,
            "hit_count": 2,
            "combo_hit": True,
            "verified_at": "2026-07-28T14:00:00+00:00",
        }
        db = MockDB({"model_predictions": [existing]})
        incoming = {
            "prediction_date": "2026-07-28",
            "region": "XSMB",
            "province": None,
            "model_name": "xsmb_combo_shadow",
            "status": "success",
            "pair_1": 10,
            "pair_2": 20,
            "pair_3": 30,
            "verified_at": None,
        }

        self.assertFalse(save_shadow_prediction(db, incoming))
        self.assertNotIn("model_predictions", db.updated)


if __name__ == "__main__":
    unittest.main()
