from datetime import date

from src.agent.master_retrain_agent import (
    _is_directly_trainable_prediction,
    _latest_verified_date,
    _prediction_scope,
)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows})()


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "prediction_results"
        return FakeQuery(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.supabase = FakeSupabase(rows)


def test_prediction_scope_detects_multi_ensemble():
    assert _prediction_scope({"model_version": "ensemble_v3.2"}) == "multi"


def test_prediction_scope_defaults_to_single():
    assert _prediction_scope({"model_version": "v3_agent_20260514_wd3"}) == "single"
    assert _prediction_scope({}) == "single"


def test_xsmn_global_multi_is_monitor_only_for_direct_retrain():
    assert not _is_directly_trainable_prediction("XSMN", "all", "multi")
    assert not _is_directly_trainable_prediction("XSMN", None, "multi")
    assert _is_directly_trainable_prediction("XSMN", "dong-nai", "single")
    assert not _is_directly_trainable_prediction("XSMB", None, "multi")


def test_latest_verified_date_reads_most_recent_verified_row():
    db = FakeDB([{"prediction_date": "2026-05-13"}])
    assert _latest_verified_date(db) == date(2026, 5, 13)


def test_latest_verified_date_handles_empty_result():
    db = FakeDB([])
    assert _latest_verified_date(db) is None
