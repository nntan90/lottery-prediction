from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.xsmn_digit_transition.calibration import (
    CalibrationObservation,
    fit_reliability_model,
)
from src.xsmn_digit_transition.backtest import (
    evaluate_permutation_controls,
    permute_rows,
    walk_forward_backtest,
)
from src.xsmn_digit_transition.config import DigitTransitionConfig
from src.xsmn_digit_transition.domain import EXPECTED_PRIZE_COUNTS, normalize_tail_rows
from src.xsmn_digit_transition import service
from src.xsmn_digit_transition.service import (
    _bounded_oof_anchor_dates,
    _oof_forecasts,
    generate_shadow_prediction,
    predict_digit_transition,
)
from src.xsmn_digit_transition.state import build_state_sequences


def _draw_rows(
    province: str,
    draw_date: date,
    dominant_unit: int,
    head_offset: int,
) -> list[dict]:
    units = [dominant_unit] * 5 + [
        (dominant_unit + offset) % 10 for offset in range(1, 10)
    ]
    units.extend(((dominant_unit + offset) % 10 for offset in range(4)))
    heads = [((head_offset + index * 3) % 10) for index in range(18)]
    tails = [head * 10 + unit for head, unit in zip(heads, units)]
    rows = []
    index = 0
    for prize_code, count in EXPECTED_PRIZE_COUNTS.items():
        for _ in range(count):
            rows.append(
                {
                    "id": len(rows) + 1,
                    "region": "XSMN",
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                    "prize_code": prize_code,
                    "tail_2d": tails[index],
                }
            )
            index += 1
    return rows


def _history(count: int = 18) -> tuple[list[dict], tuple[str, str], date]:
    provinces = ("dong-nai", "can-tho")
    start = date(2026, 2, 4)
    rows: list[dict] = []
    row_id = 1
    for index in range(count):
        draw_date = start + timedelta(days=7 * index)
        for province, leader, head_offset in (
            (provinces[0], (3 + index * 2) % 10, index),
            (provinces[1], (7 + index * 3) % 10, index + 4),
        ):
            draw_rows = _draw_rows(province, draw_date, leader, head_offset)
            for row in draw_rows:
                row["id"] = row_id
                row_id += 1
            rows.extend(draw_rows)
    return rows, provinces, start + timedelta(days=7 * count)


def _config() -> DigitTransitionConfig:
    return DigitTransitionConfig(
        top_k_states=8,
        min_transitions=4,
        province_prior_strength=4.0,
        pair_prior_strength=5.0,
        interaction_prior_strength=6.0,
        calibration_min_folds=3,
        calibration_bins=5,
        coupling_prior_strength=4.0,
    )


def test_service_generates_calibrated_province_first_merged_top_three() -> None:
    rows, provinces, target = _history()

    result = predict_digit_transition(
        rows,
        provinces,
        target,
        _config(),
        regional_rows=rows,
    )

    assert result["status"] == "success"
    assert result["provinces"] == list(provinces)
    assert result["score_semantics"] == "merged_pair_hit_probability_calibrated"
    assert len(result["top_3"]) == 3
    assert len(set(result["top_3"])) == 3
    assert len(result["top_100_audit"]) == 100
    assert sum(item["share"] for item in result["merged_unit_share"]) == pytest.approx(1.0)
    assert all(
        sum(pair % 10 == digit for pair in result["top_3"]) <= 2
        for digit in range(10)
    )
    assert any(pair % 10 == result["top_unit_digits"][0] for pair in result["top_3"])
    for province in provinces:
        payload = result["per_province"][province]
        assert sum(item["share"] for item in payload["unit_share"]) == pytest.approx(1.0)
        assert payload["calibration"]["status"] == "calibrated"


def test_service_is_deterministic_and_ignores_target_or_future_rows() -> None:
    rows, provinces, target = _history()
    leaked = rows + _draw_rows(provinces[0], target, 9, 1)
    leaked += _draw_rows(provinces[1], target + timedelta(days=7), 8, 2)

    expected = predict_digit_transition(rows, provinces, target, _config(), regional_rows=rows)
    actual = predict_digit_transition(
        reversed(leaked),
        provinces,
        target,
        _config(),
        regional_rows=reversed(leaked),
    )

    assert actual == expected


def test_oof_budget_is_recent_deterministic_union() -> None:
    rows, provinces, target = _history(count=12)
    draws = normalize_tail_rows(rows, list(provinces), before_date=target)
    states = build_state_sequences(draws, target)
    config = DigitTransitionConfig(
        min_transitions=2,
        oof_recent_anchors_per_province=3,
        oof_recent_common_anchors=2,
    )

    anchors = _bounded_oof_anchor_dates(states, config)

    assert anchors == _bounded_oof_anchor_dates(states, config)
    assert all(len(values) <= 5 for values in anchors.values())
    for province in provinces:
        eligible = [state.draw_date for state in states[province]][3:]
        assert set(eligible[-3:]).issubset(anchors[province])


@pytest.mark.parametrize("invalid", [True, 2.5])
def test_oof_anchor_budget_requires_integer(invalid) -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        DigitTransitionConfig(oof_recent_anchors_per_province=invalid)


def test_each_selected_oof_fold_excludes_future_hierarchical_states(
    monkeypatch,
) -> None:
    rows, provinces, target = _history(count=10)
    draws = normalize_tail_rows(rows, list(provinces), before_date=target)
    states = build_state_sequences(draws, target)
    hierarchical = tuple(
        state
        for province in provinces
        for state in states[province]
    )
    config = DigitTransitionConfig(min_transitions=2)
    original = service.estimate_transition
    observed = []

    def leakage_guard(training, hierarchical_training, estimator_config):
        cutoff = training[-1].draw_date
        assert all(state.draw_date <= cutoff for state in hierarchical_training)
        observed.append(cutoff)
        return original(training, hierarchical_training, estimator_config)

    monkeypatch.setattr(service, "estimate_transition", leakage_guard)
    _oof_forecasts(
        states[provinces[0]],
        hierarchical,
        config,
        frozenset(state.draw_date for state in states[provinces[0]][-2:]),
    )

    assert len(observed) == 2


def test_service_returns_insufficient_without_padding() -> None:
    rows, provinces, target = _history(count=3)

    result = predict_digit_transition(rows, provinces, target, _config())

    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "not_enough_province_transitions"
    assert result["top_3"] == []


def _boundary_manifest(watermark: str = "a" * 64) -> dict:
    return {
        "manifest_version": "ddt_input_v1",
        "status": "certified",
        "target_date": "2026-06-10",
        "target_provinces": ["dong-nai", "can-tho"],
        "expected_anchors": {
            "dong-nai": "2026-06-03",
            "can-tho": "2026-06-03",
        },
        "actual_anchors": {
            "dong-nai": "2026-06-03",
            "can-tho": "2026-06-03",
        },
        "regional_boundary_date": "2026-06-09",
        "regional_scheduled_provinces": ["ben-tre", "vung-tau", "bac-lieu"],
        "regional_certified_provinces": ["ben-tre", "vung-tau", "bac-lieu"],
        "boundary_watermark": watermark,
        "issues": [],
    }


def test_operational_service_fails_before_scoring_when_boundary_is_incomplete(
    monkeypatch,
) -> None:
    manifest = {
        **_boundary_manifest(),
        "status": "input_not_fresh",
        "boundary_watermark": None,
        "issues": [
            {
                "code": "tails_incomplete",
                "province": "bac-lieu",
                "draw_date": "2026-06-09",
            }
        ],
    }
    monkeypatch.setattr(service, "load_current_freshness_manifest", lambda *_a: manifest)
    monkeypatch.setattr(
        service,
        "load_regional_tail_history",
        lambda *_a: (_ for _ in ()).throw(AssertionError("history must not load")),
    )

    result = generate_shadow_prediction(
        object(),
        ("dong-nai", "can-tho"),
        date(2026, 6, 10),
    )

    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "input_not_fresh"
    assert result["top_3"] == []
    assert result["run_metadata"]["input_manifest"] == manifest


def test_operational_service_attaches_history_hash_and_postchecks_boundary(
    monkeypatch,
) -> None:
    rows, provinces, target = _history()
    manifest = _boundary_manifest()
    calls = iter((manifest, manifest))
    monkeypatch.setattr(
        service,
        "load_current_freshness_manifest",
        lambda *_a: next(calls),
    )
    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: rows)
    monkeypatch.setattr(
        service,
        "predict_digit_transition",
        lambda *_a, **_k: {
            "status": "success",
            "top_3": [1, 32, 92],
        },
    )

    result = generate_shadow_prediction(object(), provinces, target, _config())

    audit = result["run_metadata"]
    assert result["status"] == "success"
    assert len(audit["input_manifest"]["full_history_hash"]) == 64
    assert audit["input_manifest"]["full_history_draw_count"] == 36
    assert audit["postcheck_boundary_watermark"] == "a" * 64


def test_operational_service_discards_result_if_boundary_changes_during_run(
    monkeypatch,
) -> None:
    rows, provinces, target = _history()
    before = _boundary_manifest("a" * 64)
    after = _boundary_manifest("b" * 64)
    calls = iter((before, after))
    monkeypatch.setattr(
        service,
        "load_current_freshness_manifest",
        lambda *_a: next(calls),
    )
    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: rows)
    monkeypatch.setattr(
        service,
        "predict_digit_transition",
        lambda *_a, **_k: {"status": "success", "top_3": [1, 32, 92]},
    )

    result = generate_shadow_prediction(object(), provinces, target, _config())

    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "input_changed_during_run"
    assert result["top_3"] == []
    assert result["run_metadata"]["postcheck_boundary_watermark"] == "b" * 64


def test_operational_service_rejects_history_whose_consumed_anchor_is_older(
    monkeypatch,
) -> None:
    rows, provinces, target = _history()
    stale_rows = [
        row
        for row in rows
        if not (
            row["province"] == provinces[0]
            and row["draw_date"] == "2026-06-03"
        )
    ]
    manifest = _boundary_manifest()
    calls = iter((manifest, manifest))
    monkeypatch.setattr(
        service,
        "load_current_freshness_manifest",
        lambda *_a: next(calls),
    )
    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: stale_rows)
    monkeypatch.setattr(
        service,
        "predict_digit_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("stale anchor must not be scored")
        ),
    )

    result = generate_shadow_prediction(object(), provinces, target, _config())

    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "consumed_anchor_mismatch"
    assert result["consumed_anchors"][provinces[0]] == "2026-05-27"
    assert result["run_metadata"]["input_manifest"]["consumed_anchors"] == (
        result["consumed_anchors"]
    )


def test_operational_service_sanitizes_history_load_failure_with_precheck_audit(
    monkeypatch,
) -> None:
    _rows, provinces, target = _history()
    manifest = _boundary_manifest()
    monkeypatch.setattr(service, "load_current_freshness_manifest", lambda *_a: manifest)
    monkeypatch.setattr(
        service,
        "load_regional_tail_history",
        lambda *_a: (_ for _ in ()).throw(
            RuntimeError("https://secret.invalid?token=raw-secret")
        ),
    )

    result = generate_shadow_prediction(object(), provinces, target, _config())

    assert result["status"] == "error"
    assert result["reason"] == "history_load_failed"
    assert result["failure_stage"] == "history_load"
    assert "secret" not in str(result)
    assert result["run_metadata"]["input_manifest"] == manifest


def test_operational_service_sanitizes_normalization_and_scoring_failures(
    monkeypatch,
) -> None:
    rows, provinces, target = _history()
    manifest = _boundary_manifest()
    monkeypatch.setattr(service, "load_current_freshness_manifest", lambda *_a: manifest)
    invalid_rows = [dict(row) for row in rows]
    invalid_rows[0]["tail_2d"] = "not-a-tail"
    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: invalid_rows)

    normalization = generate_shadow_prediction(object(), provinces, target, _config())

    assert normalization["reason"] == "history_normalization_failed"
    assert normalization["failure_stage"] == "history_normalization"
    assert normalization["run_metadata"]["input_manifest"] == manifest

    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: rows)
    monkeypatch.setattr(
        service,
        "predict_digit_transition",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("token=raw-secret")),
    )
    scoring = generate_shadow_prediction(object(), provinces, target, _config())

    assert scoring["reason"] == "scoring_failed"
    assert scoring["failure_stage"] == "scoring"
    assert "raw-secret" not in str(scoring)
    assert len(scoring["run_metadata"]["input_manifest"]["full_history_hash"]) == 64


def test_operational_service_sanitizes_postcheck_failure_with_audited_history(
    monkeypatch,
) -> None:
    rows, provinces, target = _history()
    manifest = _boundary_manifest()
    calls = iter((manifest, RuntimeError("authorization=raw-secret")))

    def freshness(*_args):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(service, "load_current_freshness_manifest", freshness)
    monkeypatch.setattr(service, "load_regional_tail_history", lambda *_a: rows)
    monkeypatch.setattr(
        service,
        "predict_digit_transition",
        lambda *_a, **_k: {"status": "success", "top_3": [1, 32, 92]},
    )

    result = generate_shadow_prediction(object(), provinces, target, _config())

    assert result["reason"] == "freshness_postcheck_failed"
    assert result["failure_stage"] == "freshness_postcheck"
    assert "raw-secret" not in str(result)
    assert len(result["run_metadata"]["input_manifest"]["full_history_hash"]) == 64


def test_service_keeps_likelihood_semantics_until_merged_calibration_gate() -> None:
    rows, provinces, target = _history(count=7)
    config = DigitTransitionConfig(
        min_transitions=3,
        calibration_min_folds=100,
        top_k_states=6,
    )

    result = predict_digit_transition(
        rows, provinces, target, config, regional_rows=rows
    )

    assert result["status"] == "uncalibrated"
    assert result["reason"] == "merged_calibration_gate_not_met"
    assert result["score_semantics"] == "merged_pair_hit_likelihood_uncalibrated"
    assert len(result["top_3"]) == 3
    assert "estimated_likelihood_uncalibrated" in result["selected_evidence"][0]
    assert "probability" not in result["selected_evidence"][0]


def test_reliability_model_keeps_likelihood_name_until_draw_gate() -> None:
    observations = tuple(
        CalibrationObservation(date(2026, 1, 1), value, int(value > 0.5))
        for value in (0.1, 0.2, 0.8, 0.9)
    )

    model = fit_reliability_model(observations, bins=4, minimum_draws=2)

    assert model.status == "uncalibrated"
    assert model.apply((0.2, 0.8)) == (0.2, 0.8)


def test_walk_forward_backtest_reports_primary_metrics() -> None:
    rows, provinces, _ = _history(count=10)
    config = DigitTransitionConfig(
        top_k_states=6,
        min_transitions=2,
        calibration_min_folds=2,
        calibration_bins=4,
    )

    report = walk_forward_backtest(rows, provinces, config, max_folds=2)

    assert report["fold_count"] == 2
    assert sum(report["hit_count_distribution"].values()) == 2
    assert 0.0 <= report["hit_at_least_2_rate"] <= 1.0
    assert set(report["province_mean_hits"]) == set(provinces)
    assert set(report["baseline_mean_hits"]) == {"frequency", "marginal_only"}
    assert report["route_metrics"]
    assert report["confidence_buckets"]
    assert all(fold["target_date"] for fold in report["folds"])


def test_permutation_controls_are_evaluated_against_observed_signal() -> None:
    rows, provinces, _ = _history(count=8)
    config = DigitTransitionConfig(min_transitions=2, calibration_min_folds=2)

    report = evaluate_permutation_controls(
        rows, provinces, config, seed=7, max_folds=1
    )

    assert set(report["controls"]) == {
        "province_labels",
        "draw_order",
        "head_unit_association",
    }
    assert set(report["mean_hit_lift"]) == set(report["controls"])


@pytest.mark.parametrize(
    "mode",
    ["province_labels", "draw_order", "head_unit_association"],
)
def test_permutation_controls_are_deterministic_and_change_history(mode: str) -> None:
    rows, _, _ = _history(count=5)

    first = permute_rows(rows, mode, seed=42)
    second = permute_rows(reversed(rows), mode, seed=42)

    key = lambda row: (
        int(row["id"]),
        str(row["draw_date"]),
        str(row["province"]),
        int(row["tail_2d"]),
    )
    assert sorted(first, key=key) == sorted(second, key=key)
    assert sorted(first, key=key) != sorted(rows, key=key)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"top_unit_digits": 11},
        {"candidates_per_unit": 0},
        {"coupling_prior_strength": float("nan")},
    ],
)
def test_config_rejects_invalid_values(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        DigitTransitionConfig(**kwargs)
