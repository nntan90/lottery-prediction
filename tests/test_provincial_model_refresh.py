from datetime import date

import pandas as pd

from src.agent import provincial_model_refresh as refresh


def test_refresh_uses_target_as_latest_history_and_next_same_weekday(monkeypatch):
    target = date(2026, 7, 24)
    history = pd.DataFrame({
        "draw_date": pd.to_datetime(["2026-07-17", "2026-07-24"]),
        "tail_set": [frozenset({1}), frozenset({2})],
    })
    calls = []

    monkeypatch.setattr(refresh, "_load_tails_by_draws", lambda *_args, **_kwargs: history)

    def predictor(**kwargs):
        calls.append(kwargs)
        return {
            "status": "success",
            "model_name": "frequency" if len(calls) == 1 else "gap_overdue",
            "top_pairs": [(1, 0.5)],
            "n_draws_used": 2,
            "error_message": None,
        }

    result = refresh.refresh_rule_families(
        object(),
        "vinh-long",
        4,
        target,
        families={"frequency": predictor, "gap": predictor},
    )

    assert set(result) == {"frequency", "gap"}
    assert all(update["status"] == "refreshed" for update in result.values())
    assert all(update["latest_history_date"] == "2026-07-24" for update in result.values())
    assert all(call["target_date"] == date(2026, 7, 31) for call in calls)


def test_refresh_fails_all_families_before_scoring_when_cutoff_is_stale(monkeypatch):
    history = pd.DataFrame({
        "draw_date": pd.to_datetime(["2026-07-17"]),
        "tail_set": [frozenset({1})],
    })
    called = False

    monkeypatch.setattr(refresh, "_load_tails_by_draws", lambda *_args, **_kwargs: history)

    def predictor(**_kwargs):
        nonlocal called
        called = True
        return {"status": "success"}

    result = refresh.refresh_rule_families(
        object(),
        "vinh-long",
        4,
        date(2026, 7, 24),
        families={"frequency": predictor},
    )

    assert result["frequency"]["status"] == "failed"
    assert "cutoff mismatch" in result["frequency"]["error"]
    assert not called


def test_one_rule_failure_does_not_block_other_family(monkeypatch):
    history = pd.DataFrame({
        "draw_date": pd.to_datetime(["2026-07-24"]),
        "tail_set": [frozenset({1})],
    })
    monkeypatch.setattr(refresh, "_load_tails_by_draws", lambda *_args, **_kwargs: history)

    def broken(**_kwargs):
        raise RuntimeError("boom")

    def healthy(**_kwargs):
        return {
            "status": "success",
            "model_name": "gap_overdue",
            "top_pairs": [(1, 0.5)],
            "n_draws_used": 1,
            "error_message": None,
        }

    result = refresh.refresh_rule_families(
        object(),
        "vinh-long",
        4,
        date(2026, 7, 24),
        families={"frequency": broken, "gap": healthy},
    )

    assert result["frequency"]["status"] == "failed"
    assert result["gap"]["status"] == "refreshed"


def test_history_load_failure_marks_each_rule_family_failed(monkeypatch):
    def fail_history(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(refresh, "_load_tails_by_draws", fail_history)

    result = refresh.refresh_rule_families(
        object(),
        "vinh-long",
        4,
        date(2026, 7, 24),
        families={
            "frequency": lambda **_kwargs: {"status": "success"},
            "gap": lambda **_kwargs: {"status": "success"},
        },
    )

    assert all(update["status"] == "failed" for update in result.values())
    assert all("History load failed" in update["error"] for update in result.values())
