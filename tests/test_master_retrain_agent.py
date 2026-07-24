import asyncio
from datetime import date

from src.agent import master_retrain_agent as coordinator
from src.agent.master_retrain_agent import (
    ProvincialTarget,
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


class FeatureQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    @property
    def not_(self):
        return self

    def is_(self, *_args):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows})()


class FeatureDB:
    def __init__(self, rows):
        self.supabase = self
        self.rows = rows

    def table(self, name):
        assert name == "pair_features"
        return FeatureQuery(self.rows)


def test_target_resolution_uses_schedule_and_requires_all_100_labels(monkeypatch):
    monkeypatch.setattr(coordinator, "XSMN_WEEKDAY_MAP", {4: ["vinh-long"]})
    complete = coordinator._resolve_targets(
        FeatureDB([{"pair": pair, "hit": pair % 2} for pair in range(100)]),
        date(2026, 7, 24),
    )
    incomplete = coordinator._resolve_targets(
        FeatureDB([{"pair": pair, "hit": pair % 2} for pair in range(99)]),
        date(2026, 7, 24),
    )

    assert complete == [ProvincialTarget("vinh-long", 4, 100, True, None)]
    assert not incomplete[0].complete
    assert "Expected 100" in incomplete[0].error


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def send_message(self, message, **_kwargs):
        self.messages.append(message)


def _rule_updates(status="refreshed"):
    return {
        family: {
            "status": status,
            "latest_history_date": "2026-07-24",
            "n_draws_used": 200,
            "error": None,
        }
        for family in coordinator.RULE_FAMILIES
    }


def test_missing_single_prediction_still_updates_all_six_families(monkeypatch):
    target = date(2026, 7, 24)
    calls = []
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )
    monkeypatch.setattr(coordinator, "_is_ml_fresh", lambda *_args: False)
    monkeypatch.setattr(coordinator, "_run_train_process", lambda family, *_args: calls.append(family) or True)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: {})
    monkeypatch.setattr(
        coordinator,
        "refresh_rule_families",
        lambda *_args, **_kwargs: _rule_updates(),
    )

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=True)
    )

    assert calls == ["xgboost", "lstm"]
    assert summaries[0]["success"]
    assert set(summaries[0]["model_updates"]) == {
        "xgboost", "lstm", "frequency", "gap", "markov", "cdm",
    }
    assert summaries[0]["strategy"] == "maintain"


def test_recovery_skips_fresh_xgb_rules_and_only_trains_stale_lstm(monkeypatch):
    target = date(2026, 7, 24)
    calls = []
    lstm_fresh = {"value": False}
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )

    def fresh(_db, _province, _weekday, family, _target):
        return family == "xgboost" or (family == "lstm" and lstm_fresh["value"])

    def run(family, *_args):
        calls.append(family)
        lstm_fresh["value"] = True
        return True

    monkeypatch.setattr(coordinator, "_is_ml_fresh", fresh)
    monkeypatch.setattr(coordinator, "_run_train_process", run)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: _rule_updates())
    monkeypatch.setattr(
        coordinator,
        "refresh_rule_families",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("rules must be skipped")),
    )
    monkeypatch.setattr(coordinator, "_log_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(coordinator, "_complete_training_queue", lambda *_args: True)

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=False)
    )

    assert calls == ["lstm"]
    assert summaries[0]["success"]
    assert summaries[0]["action_type"] == "retrain_triggered"
    assert summaries[0]["model_updates"]["xgboost"]["status"] == "fresh"


def test_partial_ml_failure_does_not_block_next_province(monkeypatch):
    target = date(2026, 7, 24)
    calls = []
    trained = set()
    targets = [
        ProvincialTarget("vinh-long", 4, 100, True),
        ProvincialTarget("binh-duong", 4, 100, True),
    ]
    monkeypatch.setattr(coordinator, "_resolve_targets", lambda *_args: targets)
    monkeypatch.setattr(coordinator, "_newer_ml_train_end", lambda *_args: None)

    def fresh(_db, province, _weekday, family, _target):
        return (province, family) in trained

    def run(family, args, _dry_run):
        province = args[args.index("--province") + 1]
        calls.append((province, family))
        if province == "vinh-long" and family == "xgboost":
            return False
        trained.add((province, family))
        return True

    monkeypatch.setattr(coordinator, "_is_ml_fresh", fresh)
    monkeypatch.setattr(coordinator, "_run_train_process", run)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: {})
    monkeypatch.setattr(
        coordinator,
        "refresh_rule_families",
        lambda *_args, **_kwargs: _rule_updates(),
    )
    monkeypatch.setattr(coordinator, "_log_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(coordinator, "_complete_training_queue", lambda *_args: True)

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=False)
    )

    assert not summaries[0]["success"]
    assert summaries[1]["success"]
    assert ("binh-duong", "xgboost") in calls
    assert ("binh-duong", "lstm") in calls


def test_historical_recovery_preserves_newer_ml_artifacts(monkeypatch):
    target = date(2026, 7, 17)
    calls = []
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )
    monkeypatch.setattr(
        coordinator,
        "_newer_ml_train_end",
        lambda *_args: "2026-07-24",
    )
    monkeypatch.setattr(
        coordinator,
        "_run_train_process",
        lambda family, *_args: calls.append(family) or True,
    )
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: _rule_updates())

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=True)
    )

    assert calls == []
    assert summaries[0]["success"]
    assert summaries[0]["model_updates"]["xgboost"]["status"] == "newer"
    assert summaries[0]["model_updates"]["lstm"]["train_end_date"] == "2026-07-24"


class AuditQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows})()


class AuditDB:
    def __init__(self, rows):
        self.supabase = self
        self.rows = rows

    def table(self, name):
        assert name == "agent_actions"
        return AuditQuery(self.rows)


def test_previous_rule_updates_ignores_malformed_or_stale_audit():
    rows = [
        {"new_params": "not-json"},
        {"new_params": []},
        {"new_params": {"model_updates": {"frequency": "bad"}}},
        {"new_params": {"model_updates": {
            "gap": {
                "status": "refreshed",
                "latest_history_date": "2026-07-17",
                "n_draws_used": 200,
            },
            "markov": {
                "status": "refreshed",
                "latest_history_date": "2026-07-24",
                "n_draws_used": 0,
            },
            "cdm": {
                "status": "refreshed",
                "latest_history_date": "2026-07-24",
                "n_draws_used": 200,
            },
        }}},
    ]

    updates = coordinator._previous_rule_updates(
        AuditDB(rows), "vinh-long", 4, date(2026, 7, 24)
    )

    assert set(updates) == {"cdm"}


def test_audit_failure_marks_target_failed_and_does_not_complete_queue(monkeypatch):
    target = date(2026, 7, 24)
    queue_calls = []
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )
    monkeypatch.setattr(coordinator, "_newer_ml_train_end", lambda *_args: None)
    monkeypatch.setattr(coordinator, "_is_ml_fresh", lambda *_args: True)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: _rule_updates())
    monkeypatch.setattr(coordinator, "_log_action", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        coordinator,
        "_complete_training_queue",
        lambda *_args: queue_calls.append(True) or True,
    )

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=False)
    )

    assert not summaries[0]["success"]
    assert not summaries[0]["audit_ok"]
    assert queue_calls == []


def test_nonzero_trainer_is_success_when_fresh_artifact_was_published(monkeypatch):
    target = date(2026, 7, 24)
    freshness_calls = {"xgboost": 0, "lstm": 0}
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )
    monkeypatch.setattr(coordinator, "_newer_ml_train_end", lambda *_args: None)

    def fresh(_db, _province, _weekday, family, _target):
        freshness_calls[family] += 1
        return freshness_calls[family] >= 2

    monkeypatch.setattr(coordinator, "_is_ml_fresh", fresh)
    monkeypatch.setattr(coordinator, "_run_train_process", lambda *_args: False)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: _rule_updates())
    monkeypatch.setattr(coordinator, "_log_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(coordinator, "_complete_training_queue", lambda *_args: True)

    summaries = asyncio.run(
        coordinator.run_agent(object(), FakeNotifier(), [], target, dry_run=False)
    )

    assert summaries[0]["success"]
    assert summaries[0]["model_updates"]["xgboost"]["status"] == "trained"
    assert "nonzero" in summaries[0]["model_updates"]["lstm"]["error"]


def test_recovery_noop_does_not_send_telegram(monkeypatch):
    target = date(2026, 7, 24)
    notifier = FakeNotifier()
    monkeypatch.setattr(
        coordinator,
        "_resolve_targets",
        lambda *_args: [ProvincialTarget("vinh-long", 4, 100, True)],
    )
    monkeypatch.setattr(coordinator, "_newer_ml_train_end", lambda *_args: None)
    monkeypatch.setattr(coordinator, "_is_ml_fresh", lambda *_args: True)
    monkeypatch.setattr(coordinator, "_previous_rule_updates", lambda *_args: _rule_updates())
    monkeypatch.setattr(coordinator, "_log_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(coordinator, "_complete_training_queue", lambda *_args: True)

    summaries = asyncio.run(
        coordinator.run_agent(object(), notifier, [], target, dry_run=False)
    )

    assert summaries[0]["action_type"] == "no_action"
    assert notifier.messages == []
