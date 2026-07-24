"""Regression tests for the XSMN correctness refactor."""

from datetime import date

from src.analytics.backtest import random_at_least_hits_probability, summarize_predictions
from src.scoring.credibility_scorer import (
    _align_with_actuals,
    _compute_stability_index,
    _extract_model_predictions,
    _get_default_weights,
    _has_minimum_scope_samples,
)
from src.scripts.predict_ensemble import _execute_paged_rows, _flatten_tail_rows_by_draw
from src.xsmn_ensemble.model_lstm import (
    LotteryLSTM,
    _deterministic_training_seed,
    _get_lstm_model,
)
from src.xsmn_ensemble.model_markov import _valid_context
from src.xsmn_ensemble.model_xgboost import _get_active_model


class _RegistryQuery:
    def __init__(self, rows_by_key):
        self.rows_by_key = rows_by_key
        self.filters = {}

    def select(self, *_args):
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def is_(self, column, _null):
        self.filters[column] = None
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        key = (
            self.filters.get("province"),
            self.filters.get("weekday"),
            self.filters.get("model_name"),
        )
        return type("Result", (), {"data": self.rows_by_key.get(key, [])})()


class _RegistryDB:
    def __init__(self, rows_by_key):
        self.rows_by_key = rows_by_key
        self.supabase = self

    def table(self, name):
        assert name == "model_registry"
        return _RegistryQuery(self.rows_by_key)


def test_history_rows_preserve_one_occurrence_per_draw() -> None:
    rows = [
        {"draw_date": "2026-06-01", "tail_2d": 42},
        {"draw_date": "2026-06-01", "tail_2d": 42},
        {"draw_date": "2026-06-08", "tail_2d": 42},
        {"draw_date": "2026-06-15", "tail_2d": 42},
    ]

    assert _flatten_tail_rows_by_draw(rows) == [42, 42, 42]


def test_merged_history_query_is_not_truncated_at_one_thousand_rows() -> None:
    class Query:
        def __init__(self) -> None:
            self.rows = [{"id": idx} for idx in range(1872)]
            self.start = 0
            self.end = 999

        def range(self, start: int, end: int):
            self.start, self.end = start, end
            return self

        def execute(self):
            result = type("Result", (), {})()
            result.data = self.rows[self.start:self.end + 1]
            return result

    rows = _execute_paged_rows(Query())

    assert len(rows) == 1872
    assert rows[-1]["id"] == 1871


def test_credibility_keeps_province_grain() -> None:
    history = {
        "2026-06-01": [
            {
                "prediction_date": "2026-06-01",
                "province": "ben-tre",
                "model_name": "frequency",
                "pair_1": 10,
                "status": "success",
            },
            {
                "prediction_date": "2026-06-01",
                "province": "vung-tau",
                "model_name": "frequency",
                "pair_1": 20,
                "status": "success",
            },
        ]
    }

    predictions = _extract_model_predictions(history, "frequency", lookback_draws=8)
    actuals = {
        ("2026-06-01", "ben-tre"): {99},
        ("2026-06-01", "vung-tau"): {20},
    }
    evaluated = _align_with_actuals(predictions, actuals)

    assert len(predictions) == 2
    assert {row["province"] for row in predictions} == {"ben-tre", "vung-tau"}
    assert {row["province"]: row["hit"] for row in evaluated} == {
        "ben-tre": False,
        "vung-tau": True,
    }


def test_xsmn_credibility_anchor_uses_xsmn_override() -> None:
    weights = _get_default_weights("XSMN")

    assert set(weights) == {
        "frequency",
        "gap_overdue",
        "markov",
        "xgboost_core",
        "lstm",
        "cdm",
    }
    assert all(abs(weight - (1.0 / 6.0)) < 1e-12 for weight in weights.values())
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_xsmb_credibility_anchor_contains_only_active_models() -> None:
    weights = _get_default_weights("XSMB")

    assert set(weights) == {
        "frequency",
        "markov",
        "chisquare_gof",
        "cdm",
        "loto_statistical",
    }
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_retired_model_history_cannot_dilute_active_weights(monkeypatch) -> None:
    from src.scoring import credibility_scorer as scorer

    history = {
        "2026-06-01": [
            {
                "prediction_date": "2026-06-01",
                "province": "ben-tre",
                "model_name": "retired_alias",
                "pair_1": 10,
                "status": "success",
            }
        ]
    }
    monkeypatch.setattr(scorer, "_query_model_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(
        scorer,
        "_query_actual_tails",
        lambda *args, **kwargs: {("2026-06-01", "ben-tre"): {10}},
    )
    monkeypatch.setattr(scorer, "_query_ensemble_predictions", lambda *args, **kwargs: {})
    monkeypatch.setattr(scorer, "_save_credibility_to_db", lambda *args, **kwargs: None)

    result = scorer.compute_credibility_scores(
        object(),
        "XSMN",
        date(2026, 7, 19),
        config_weights={"frequency": 1.0},
    )

    assert set(result["scorecard"]) == {"frequency"}
    assert "retired_alias" not in result["credibility_weights"]


def test_credibility_enabled_flag_is_honored(monkeypatch) -> None:
    from src.scoring import credibility_config, credibility_scorer as scorer

    monkeypatch.setattr(
        credibility_config,
        "load_credibility_config_from_yaml",
        lambda: {"enabled": False},
    )

    result = scorer.compute_credibility_scores(
        object(),
        "XSMN",
        date(2026, 7, 19),
        config_weights={"frequency": 1.0},
    )

    assert not result["using_dynamic_weights"]
    assert result["credibility_weights"] == _get_default_weights("XSMN")
    assert "disabled" in result["scoring_log"]


def test_dynamic_credibility_requires_minimum_for_both_provinces() -> None:
    evaluated = [{"province": "ben-tre"} for _ in range(30)]
    evaluated += [{"province": "vung-tau"} for _ in range(29)]

    assert not _has_minimum_scope_samples(evaluated, 30, is_xsmb=False)
    evaluated.append({"province": "vung-tau"})
    assert _has_minimum_scope_samples(evaluated, 30, is_xsmb=False)


def test_disjoint_predictions_are_not_perfectly_stable() -> None:
    predictions = [
        {"predicted_pairs": [1, 2, 3, 4, 5]},
        {"predicted_pairs": [6, 7, 8, 9, 10]},
        {"predicted_pairs": [11, 12, 13, 14, 15]},
    ]

    assert _compute_stability_index(predictions) < 0.75


def test_backtest_reports_two_of_three_as_primary_combo_kpi() -> None:
    predictions = [
        {"region": "XSMN", "province": "all", "pair_1": 1, "pair_2": 2, "pair_3": 3, "tail_set": [1]},
        {"region": "XSMN", "province": "all", "pair_1": 1, "pair_2": 2, "pair_3": 3, "tail_set": [1, 2]},
        {"region": "XSMN", "province": "all", "pair_1": 1, "pair_2": 2, "pair_3": 3, "tail_set": [1, 2, 3]},
    ]

    overall = summarize_predictions(predictions)["overall"]

    assert overall["hit_count_distribution"] == {"0": 0, "1": 1, "2": 1, "3": 1}
    assert overall["hit_at_least_2"] == 2
    assert overall["hit_at_least_2_rate"] == 0.6667
    assert overall["hit_all_3"] == 1
    assert random_at_least_hits_probability(20, picks=3, min_hits=2) > 0


def test_lstm_fallback_seed_is_reproducible() -> None:
    target = date(2026, 7, 19)

    assert _deterministic_training_seed("XSMN", "tien-giang", target) == _deterministic_training_seed(
        "XSMN", "tien-giang", target
    )
    assert _deterministic_training_seed("XSMN", "tien-giang", target) != _deterministic_training_seed(
        "XSMN", "kien-giang", target
    )


def test_lstm_seed_is_applied_before_parameter_initialization(monkeypatch) -> None:
    from src.xsmn_ensemble import model_lstm

    events: list[tuple[str, int | None]] = []

    class FakeTorch:
        @staticmethod
        def manual_seed(seed: int) -> None:
            events.append(("seed", seed))

    lstm = LotteryLSTM()

    def fail_after_build() -> None:
        events.append(("build", None))
        raise RuntimeError("stop after initialization order check")

    monkeypatch.setattr(model_lstm, "_ensure_torch", lambda: (FakeTorch(), object()))
    monkeypatch.setattr(lstm, "_build_model", fail_after_build)

    try:
        lstm.train_model(None, None, seed=1234)
    except RuntimeError as error:
        assert "initialization order" in str(error)

    assert events == [("seed", 1234), ("build", None)]


def test_lstm_registry_prefers_exact_province_weekday_artifact() -> None:
    db = _RegistryDB({
        ("tp-hcm", 5, "lstm"): [{"version": "sat"}],
        ("tp-hcm", None, "lstm"): [{"version": "legacy"}],
    })

    assert _get_lstm_model(db, "XSMN", "tp-hcm", 5)["version"] == "sat"


def test_xgb_registry_preserves_province_priority_during_legacy_fallback() -> None:
    db = _RegistryDB({
        ("tp-hcm", None, None): [{"version": "province-legacy"}],
        (None, 5, "xgboost_core"): [{"version": "global-new"}],
    })

    result = _get_active_model(db, "XSMN", "tp-hcm", 5)

    assert result["version"] == "province-legacy"


def test_markov_context_does_not_drop_high_numbered_pairs() -> None:
    context = set(range(10, 30))

    assert _valid_context(context) == list(range(10, 30))
