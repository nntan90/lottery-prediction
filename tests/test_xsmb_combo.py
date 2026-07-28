"""Tests for the additive, default-off XSMB combo selector."""

from __future__ import annotations

import asyncio
import math
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from src.xsmb_combo.adapters import adapt_legacy_model_results
from src.xsmb_combo.domain import ComboSelectorResult, SelectorStatus
from src.xsmb_combo.joint_probability import JointProbabilityEstimator
from src.xsmb_combo.metrics import (
    combo_probability_from_joint,
    evaluate_combo,
    random_combo_hit_probability,
    random_expected_winning_circles,
)
from src.xsmb_combo.selector import select_combo
from src.xsmb_combo.shadow import (
    _history_before_target,
    get_combo_selector_mode,
    maybe_run_xsmb_combo_shadow,
)


def _legacy_result(model_name: str, pairs: list[tuple[int, float]]) -> dict:
    return {
        "model_name": model_name,
        "status": "success",
        "top_pairs": pairs,
    }


def test_combo_metrics_use_two_of_three_rule_and_circle_profit() -> None:
    evaluation = evaluate_combo([10, 20, 30], {10, 20, 88})

    assert evaluation.hit_count == 2
    assert evaluation.combo_hit is True
    assert evaluation.matched_pairs == (10, 20)
    assert evaluation.winning_circles == 1
    assert evaluation.revenue == 1_100_000
    assert evaluation.profit == 772_000


def test_random_metrics_match_hypergeometric_formulas() -> None:
    probability = random_combo_hit_probability(10)
    expected = (
        math.comb(10, 2) * math.comb(90, 1) + math.comb(10, 3)
    ) / math.comb(100, 3)

    assert probability == expected
    assert random_expected_winning_circles(10) == (
        3 * math.comb(10, 2) / math.comb(100, 2)
    )


def test_combo_probability_uses_pairwise_and_triple_intersection() -> None:
    assert combo_probability_from_joint([0.20, 0.15, 0.10], 0.05) == 0.35


def test_joint_estimator_rejects_out_of_range_pair_ids() -> None:
    estimator = JointProbabilityEstimator(
        [frozenset({10, 20, 30}) for _ in range(30)]
    )

    for operation in (
        lambda: estimator.pair_probability(-1, 20),
        lambda: estimator.pair_probability(10, 100),
        lambda: estimator.triple_probability(10, 20, 100),
    ):
        try:
            operation()
        except ValueError as exc:
            assert "between 0 and 99" in str(exc)
        else:
            raise AssertionError("out-of-range pairs must be rejected")


def test_legacy_adapter_skips_bad_models_and_sanitizes_entries() -> None:
    adapted = adapt_legacy_model_results([
        None,
        _legacy_result("good", [(10, 2.0), (10, 1.0), (20, 0.0), (101, 4.0)]),
        {"model_name": "failed", "status": "error", "top_pairs": []},
        {"model_name": "malformed", "status": "success", "top_pairs": "bad"},
    ])

    assert [vector.model_name for vector in adapted.vectors] == ["good"]
    assert adapted.vectors[0].source_pairs == (10, 20)
    assert adapted.vectors[0].scores[10] == 2.0
    assert adapted.skipped_models == ("unknown_0", "failed", "malformed")
    assert any("not a mapping" in warning for warning in adapted.warnings)
    assert any("ignored 1" in warning for warning in adapted.warnings)


def test_full_vectors_cover_all_pairs_and_exhaust_all_triples_with_weights() -> None:
    first_vector = [float(100 - pair) for pair in range(100)]
    second_vector = [float(pair + 1) for pair in range(100)]
    adapted = adapt_legacy_model_results([
        {
            **_legacy_result("first", [(0, 1.0), (1, 0.9), (2, 0.8)]),
            "score_vector": first_vector,
            "source_family": "family_a",
            "score_semantics": "relative_score_uncalibrated",
        },
        {
            **_legacy_result("second", [(99, 1.0), (98, 0.9), (97, 0.8)]),
            "score_vector": second_vector,
            "source_family": "family_b",
            "score_semantics": "relative_score_uncalibrated",
        },
    ])
    history = [
        frozenset({index % 100, (index + 17) % 100, (index + 43) % 100})
        for index in range(40)
    ]

    result = select_combo(
        adapted,
        history,
        weights={"first": 0.75, "second": 0.25},
    )

    assert result.status == SelectorStatus.SUCCESS
    assert len(result.candidate_pool) == 100
    assert result.evaluated_triples == math.comb(100, 3) == 161_700
    assert dict(result.active_weights) == {"first": 0.75, "second": 0.25}
    assert dict(result.source_families) == {
        "first": "family_a",
        "second": "family_b",
    }
    assert result.score_semantics == "combo_score_uncalibrated"


def test_invalid_full_vector_falls_back_to_legacy_top_pairs() -> None:
    adapted = adapt_legacy_model_results([
        {
            **_legacy_result("legacy", [(10, 1.0), (20, 0.5), (30, 0.25)]),
            "score_vector": [1.0] * 99,
        }
    ])

    assert adapted.vectors[0].coverage_kind == "legacy_top_n"
    assert adapted.vectors[0].source_pairs == (10, 20, 30)
    assert any("invalid score_vector" in warning for warning in adapted.warnings)


def test_full_vector_is_usable_without_legacy_top_pairs() -> None:
    vector = [float(pair + 1) for pair in range(100)]
    adapted = adapt_legacy_model_results([
        {
            "model_name": "vector_only",
            "status": "success",
            "score_vector": vector,
            "source_family": "test_family",
        }
    ])

    assert adapted.skipped_models == ()
    assert adapted.vectors[0].coverage_kind == "full_100"
    assert adapted.vectors[0].scores == tuple(vector)


def test_zero_mass_full_vector_falls_back_to_legacy_ranking() -> None:
    adapted = adapt_legacy_model_results([
        {
            **_legacy_result("zero_vector", [(10, 1.0), (20, 0.5), (30, 0.25)]),
            "score_vector": [0.0] * 100,
        }
    ])

    assert adapted.vectors[0].coverage_kind == "legacy_top_n"
    assert adapted.vectors[0].source_pairs == (10, 20, 30)
    assert any("zero-mass score_vector" in warning for warning in adapted.warnings)


def test_non_finite_weight_fails_closed_to_finite_normalized_weights() -> None:
    adapted = adapt_legacy_model_results([
        _legacy_result("a", [(10, 1.0), (20, 0.5), (30, 0.25)]),
    ])
    result = select_combo(
        adapted,
        [frozenset({10, 20})] * 30,
        weights={"a": float("nan")},
    )

    assert result.status == SelectorStatus.SUCCESS
    assert dict(result.active_weights) == {"a": 1.0}


def test_selector_prefers_joint_combo_evidence_over_marginal_rank() -> None:
    adapted = adapt_legacy_model_results([
        _legacy_result(
            "ranker",
            [(0, 1.0), (1, 0.95), (2, 0.90), (3, 0.85), (4, 0.80), (5, 0.75)],
        )
    ])
    history = []
    for index in range(40):
        if index < 20:
            history.append(frozenset({3, 4, 5, 70 + index % 10}))
        else:
            history.append(frozenset({index % 3, 80 + index % 10}))

    result = select_combo(
        adapted,
        history,
        candidate_pool_size=6,
        minimum_history=30,
    )

    assert result.status == SelectorStatus.SUCCESS
    assert set(result.top_pairs) == {3, 4, 5}
    assert result.evaluated_triples == math.comb(6, 3)


def test_selector_is_deterministic_and_supports_expected_circles() -> None:
    adapted = adapt_legacy_model_results([
        _legacy_result("a", [(10, 5), (20, 4), (30, 3), (40, 2)]),
        _legacy_result("b", [(20, 5), (10, 4), (40, 3), (30, 2)]),
    ])
    history = [frozenset({10, 20, 30}) for _ in range(35)]

    first = select_combo(adapted, history, objective="expected_circles")
    second = select_combo(adapted, history, objective="expected_circles")

    assert first == second
    assert first.status == SelectorStatus.SUCCESS
    assert first.objective == "expected_circles"


def test_selector_reports_insufficient_candidates_and_history() -> None:
    two_candidates = adapt_legacy_model_results([
        _legacy_result("small", [(10, 1.0), (20, 0.5)])
    ])
    candidate_result = select_combo(two_candidates, [frozenset()] * 30)
    assert candidate_result.status == SelectorStatus.INSUFFICIENT_CANDIDATES

    three_candidates = adapt_legacy_model_results([
        _legacy_result("small", [(10, 1.0), (20, 0.5), (30, 0.25)])
    ])
    history_result = select_combo(three_candidates, [frozenset()] * 10)
    assert history_result.status == SelectorStatus.INSUFFICIENT_HISTORY


def test_history_cutoff_rejects_target_date_leakage() -> None:
    history = pd.DataFrame({
        "draw_date": pd.to_datetime(["2026-07-23", "2026-07-24"]),
        "tail_set": [frozenset({10}), frozenset({20})],
    })

    try:
        _history_before_target(history, date(2026, 7, 24))
    except ValueError as exc:
        assert "history leakage" in str(exc)
    else:
        raise AssertionError("target-date history must be rejected")


def test_history_rejects_incomplete_xsmb_draws() -> None:
    history = pd.DataFrame({
        "draw_date": pd.to_datetime(["2026-07-22", "2026-07-23"]),
        "tail_set": [frozenset({10}), frozenset({20})],
        "tail_count": [27, 26],
    })

    try:
        _history_before_target(history, date(2026, 7, 24))
    except ValueError as exc:
        assert "incomplete XSMB draw" in str(exc)
        assert "26/27" in str(exc)
    else:
        raise AssertionError("partial XSMB draws must be rejected")


def test_shadow_mode_defaults_off_and_does_not_call_runner() -> None:
    assert get_combo_selector_mode(None) == "off"
    assert get_combo_selector_mode("unexpected") == "off"

    with patch(
        "src.xsmb_combo.shadow.run_xsmb_combo_shadow",
        side_effect=AssertionError("must not run"),
    ):
        assert maybe_run_xsmb_combo_shadow(None, [], date(2026, 7, 25), mode="off") is None


def test_shadow_failure_preserves_legacy_path() -> None:
    messages: list[str] = []
    with patch(
        "src.xsmb_combo.shadow.run_xsmb_combo_shadow",
        side_effect=RuntimeError("shadow boom"),
    ):
        result = maybe_run_xsmb_combo_shadow(
            None,
            [],
            date(2026, 7, 25),
            mode="shadow",
            logger=messages.append,
        )

    assert result is None
    assert "legacy output preserved" in messages[0]


def test_xsmb_pipeline_default_off_preserves_legacy_db_and_telegram(
    monkeypatch,
) -> None:
    """The default-off hook must not alter the production XSMB surface."""
    import src.scripts.predict_ensemble as pipeline

    monkeypatch.delenv("XSMB_COMBO_SELECTOR_MODE", raising=False)
    target_date = date(2026, 7, 25)
    db = MagicMock()
    storage = MagicMock()
    notifier = MagicMock()
    model_results = [
        _legacy_result(model_name, [(50, 1.0), (83, 0.8), (89, 0.6)])
        for model_name in (
            "frequency",
            "markov",
            "chisquare_gof",
            "cdm",
            "loto_statistical",
        )
    ]
    ensemble_output = {
        "top_pairs": [(50, 0.157), (83, 0.157), (89, 0.093)],
        "consensus_pairs": [50, 83, 89],
        "models_active": 5,
        "active_weights": {
            "frequency": 0.16,
            "markov": 0.26,
            "chisquare_gof": 0.19,
            "cdm": 0.20,
            "loto_statistical": 0.20,
        },
        "top_candidates": [{"pair": 50}, {"pair": 83}, {"pair": 89}, {"pair": 90}],
        "ensemble_method": "xsmb_precision_v5.1",
        "contributing_models": [result["model_name"] for result in model_results],
    }
    legacy_prediction = {
        "prediction_date": target_date.isoformat(),
        "region": "XSMB",
        "province": None,
        "pair_1": 50,
        "pair_2": 83,
        "pair_3": 89,
        "model_version": "ensemble_v5.1",
        "scoring_log": "runtime",
        "candidate_log": "runtime",
    }

    shadow_runner = MagicMock(side_effect=AssertionError("shadow must stay off"))
    saved_prediction = MagicMock()
    telegram_formatter = MagicMock(return_value="legacy telegram message")
    telegram_sender = AsyncMock(return_value=True)
    ensemble_formatter = MagicMock(return_value=legacy_prediction.copy())

    with (
        patch.object(
            pipeline,
            "run_xsmb_models",
            new=AsyncMock(return_value=model_results),
        ),
        patch.object(
            pipeline,
            "compute_credibility_scores",
            return_value={"credibility_weights": {}, "scoring_log": ""},
        ),
        patch.object(pipeline, "get_recent_tails", return_value=[]),
        patch.object(pipeline, "get_last_7_days_tails", return_value=[]),
        patch.object(
            pipeline,
            "compute_xsmb_ensemble",
            return_value=ensemble_output,
        ),
        patch(
            "src.xsmb_combo.shadow.run_xsmb_combo_shadow",
            shadow_runner,
        ),
        patch.object(
            pipeline,
            "xsmb_format_ensemble_result",
            ensemble_formatter,
        ),
        patch.object(pipeline, "save_prediction", saved_prediction),
        patch.object(
            pipeline,
            "format_compact_ensemble_message",
            telegram_formatter,
        ),
        patch.object(pipeline, "_send_chunked", telegram_sender),
        patch.object(pipeline, "get_dow_label", return_value="Thứ Bảy"),
        patch(
            "src.xsmb_ensemble.xsmb_loto_analyzer.XSMBLotoAnalyzer"
        ) as analyzer_class,
        patch(
            "src.xsmb_ensemble.xsmb_loto_report.format_loto_report_telegram",
            return_value=[],
        ),
    ):
        analyzer_class.return_value.generate_full_report.return_value = {}
        asyncio.run(
            pipeline.run_xsmb_ensemble(
                target_date,
                db,
                storage,
                notifier,
                "/tmp",
            )
        )

    shadow_runner.assert_not_called()
    ensemble_formatter.assert_called_once_with(
        "XSMB", None, ensemble_output, target_date
    )
    saved_payload = saved_prediction.call_args.args[1]
    assert (saved_payload["pair_1"], saved_payload["pair_2"], saved_payload["pair_3"]) == (
        50,
        83,
        89,
    )
    assert saved_payload["model_version"] == "ensemble_v5.1"
    telegram_top_pairs = telegram_formatter.call_args.kwargs["top_pairs"]
    assert telegram_top_pairs == ensemble_output["top_pairs"]
    telegram_sender.assert_awaited_once_with(
        notifier,
        "legacy telegram message",
        "predict_ensemble_xsmb",
    )


def test_xsmb_pipeline_omits_shadow_from_telegram_when_persistence_raises(
    monkeypatch,
) -> None:
    """An unauditable shadow row must never be published to Telegram."""
    import src.scripts.predict_ensemble as pipeline

    monkeypatch.setenv("XSMB_COMBO_SELECTOR_MODE", "shadow")
    target_date = date(2026, 7, 28)
    model_results = [
        _legacy_result(model_name, [(50, 1.0), (83, 0.8), (89, 0.6)])
        for model_name in (
            "frequency",
            "markov",
            "chisquare_gof",
            "cdm",
            "loto_statistical",
        )
    ]
    ensemble_output = {
        "top_pairs": [(50, 0.157), (83, 0.138), (89, 0.093)],
        "consensus_pairs": [50, 83, 89],
        "models_active": 5,
        "active_weights": {"frequency": 1.0},
        "top_candidates": [{"pair": 50}, {"pair": 83}, {"pair": 89}],
    }
    shadow_result = ComboSelectorResult(
        status=SelectorStatus.SUCCESS,
        top_pairs=(12, 34, 56),
        objective_score=0.184321,
    )
    telegram_formatter = MagicMock(return_value="production message")

    with (
        patch.object(
            pipeline,
            "run_xsmb_models",
            new=AsyncMock(return_value=model_results),
        ),
        patch.object(
            pipeline,
            "compute_credibility_scores",
            return_value={"credibility_weights": {}, "scoring_log": ""},
        ),
        patch.object(pipeline, "get_recent_tails", return_value=[]),
        patch.object(pipeline, "get_last_7_days_tails", return_value=[]),
        patch.object(
            pipeline,
            "compute_xsmb_ensemble",
            return_value=ensemble_output,
        ),
        patch.object(
            pipeline,
            "maybe_run_xsmb_combo_shadow",
            return_value=shadow_result,
        ),
        patch.object(
            pipeline,
            "save_shadow_prediction",
            side_effect=RuntimeError("db unavailable"),
        ),
        patch.object(
            pipeline,
            "xsmb_format_ensemble_result",
            return_value={
                "prediction_date": target_date.isoformat(),
                "region": "XSMB",
                "province": None,
                "pair_1": 50,
                "pair_2": 83,
                "pair_3": 89,
            },
        ),
        patch.object(pipeline, "save_prediction"),
        patch.object(
            pipeline,
            "format_compact_ensemble_message",
            telegram_formatter,
        ),
        patch.object(pipeline, "_send_chunked", new=AsyncMock(return_value=True)),
        patch.object(pipeline, "get_dow_label", return_value="Thứ Ba"),
        patch(
            "src.xsmb_ensemble.xsmb_loto_analyzer.XSMBLotoAnalyzer"
        ) as analyzer_class,
        patch(
            "src.xsmb_ensemble.xsmb_loto_report.format_loto_report_telegram",
            return_value=[],
        ),
    ):
        analyzer_class.return_value.generate_full_report.return_value = {}
        asyncio.run(
            pipeline.run_xsmb_ensemble(
                target_date,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                "/tmp",
            )
        )

    assert "additional_shadows" not in telegram_formatter.call_args.kwargs
