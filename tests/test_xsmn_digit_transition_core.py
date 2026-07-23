from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math

import pytest

from src.xsmn_digit_transition.calibration import (
    CalibrationObservation,
    ReliabilityCalibrationConfig,
    expanding_walk_forward_calibrate,
)
from src.xsmn_digit_transition.estimator import (
    TransitionEstimatorConfig,
    estimate_transition,
)
from src.xsmn_digit_transition.merge import (
    CoupledDrawObservation,
    CouplingMergeConfig,
    merge_province_forecasts,
)
from src.xsmn_digit_transition.selector import select_top_three


@dataclass(frozen=True)
class FixtureDigitState:
    province: str
    draw_date: date
    pair_counts: tuple[int, ...]
    head_counts: tuple[int, ...]
    unit_counts: tuple[int, ...]
    head_share: tuple[float, ...]
    unit_share: tuple[float, ...]
    head_coverage: tuple[int, ...]
    unit_coverage: tuple[int, ...]
    dominant_heads: tuple[int, ...]
    dominant_units: tuple[int, ...]
    head_max_count: int
    unit_max_count: int
    head_margin: int
    unit_margin: int
    head_entropy: float
    unit_entropy: float
    route: str


@dataclass(frozen=True)
class FixtureForecast:
    province: str
    unit_share: tuple[float, ...]
    pair_hit_likelihoods: tuple[float, ...]


def _state(
    province: str,
    draw_date: date,
    dominant_unit: int,
    dominant_head: int = 4,
    route: str = "weekly",
) -> FixtureDigitState:
    pairs = [dominant_head * 10 + dominant_unit] * 6
    pairs.extend(((dominant_head + index) % 10) * 10 + ((dominant_unit + index + 1) % 10) for index in range(12))
    pair_counts = [0] * 100
    head_counts = [0] * 10
    unit_counts = [0] * 10
    for pair in pairs:
        pair_counts[pair] += 1
        head_counts[pair // 10] += 1
        unit_counts[pair % 10] += 1

    def share(counts: list[int]) -> tuple[float, ...]:
        return tuple(count / 18.0 for count in counts)

    def leaders(counts: list[int]) -> tuple[int, ...]:
        maximum = max(counts)
        return tuple(index for index, count in enumerate(counts) if count == maximum)

    def margin(counts: list[int]) -> int:
        ordered = sorted(counts, reverse=True)
        return ordered[0] - ordered[1]

    def entropy(counts: list[int]) -> float:
        return -sum((count / 18.0) * math.log(count / 18.0) for count in counts if count)

    return FixtureDigitState(
        province=province,
        draw_date=draw_date,
        pair_counts=tuple(pair_counts),
        head_counts=tuple(head_counts),
        unit_counts=tuple(unit_counts),
        head_share=share(head_counts),
        unit_share=share(unit_counts),
        head_coverage=tuple(int(count > 0) for count in head_counts),
        unit_coverage=tuple(int(count > 0) for count in unit_counts),
        dominant_heads=leaders(head_counts),
        dominant_units=leaders(unit_counts),
        head_max_count=max(head_counts),
        unit_max_count=max(unit_counts),
        head_margin=margin(head_counts),
        unit_margin=margin(unit_counts),
        head_entropy=entropy(head_counts),
        unit_entropy=entropy(unit_counts),
        route=route,
    )


def _history(province: str, leader: int, count: int, start: date) -> tuple[FixtureDigitState, ...]:
    return tuple(
        _state(province, start + timedelta(days=7 * index), leader)
        for index in range(count)
    )


def _forecast(province: str, pair: int, likelihood: float, unit_digit: int = 3) -> FixtureForecast:
    pairs = [0.01] * 100
    pairs[pair] = likelihood
    units = [0.05] * 10
    units[unit_digit] = 0.55
    units[0] += 1.0 - sum(units)
    return FixtureForecast(province, tuple(units), tuple(pairs))


def test_estimator_outputs_complete_normalized_distributions_and_decomposition() -> None:
    local = _history("dong-nai", 3, 8, date(2026, 1, 7))
    prior = _history("can-tho", 7, 10, date(2025, 10, 1))

    forecast = estimate_transition(local, prior)

    assert sum(forecast.head_share) == pytest.approx(1.0)
    assert sum(forecast.unit_share) == pytest.approx(1.0)
    assert sum(forecast.head_leader_likelihoods) == pytest.approx(1.0)
    assert sum(forecast.unit_leader_likelihoods) == pytest.approx(1.0)
    assert len(forecast.pair_hit_likelihoods) == 100
    assert all(0.0 <= value <= 1.0 for value in forecast.pair_hit_likelihoods)
    assert forecast.score_semantics.endswith("uncalibrated")
    assert forecast.decomposition.local_transition_count == 7
    assert 0.0 <= forecast.confidence <= 1.0


def test_sparse_local_evidence_shrinks_toward_hierarchical_prior() -> None:
    local = _history("dong-nai", 3, 2, date(2026, 1, 7))
    prior = _history("can-tho", 7, 12, date(2025, 8, 6))

    weak_shrink = estimate_transition(
        local,
        prior,
        TransitionEstimatorConfig(prior_strength=0.2),
    )
    strong_shrink = estimate_transition(
        local,
        prior,
        TransitionEstimatorConfig(prior_strength=30.0),
    )

    prior_digit_7 = strong_shrink.decomposition.prior_unit_share[7]
    assert abs(strong_shrink.unit_share[7] - prior_digit_7) < abs(
        weak_shrink.unit_share[7] - prior_digit_7
    )
    assert weak_shrink.unit_share[3] > strong_shrink.unit_share[3]


def test_estimator_does_not_force_anti_repeat_and_is_deterministic() -> None:
    local = _history("dong-nai", 3, 9, date(2026, 1, 7))
    prior = _history("can-tho", 3, 12, date(2025, 8, 6))

    first = estimate_transition(local, prior)
    second = estimate_transition(reversed(local), reversed(prior))

    assert first == second
    assert first.unit_share[3] == max(first.unit_share)
    assert first.unit_leader_likelihoods[3] == max(first.unit_leader_likelihoods)


def test_tphcm_estimator_uses_matching_route_transitions() -> None:
    start = date(2026, 6, 6)
    local = (
        _state("tp-hcm", start, 1, route="Sat->Mon"),
        _state("tp-hcm", start + timedelta(days=2), 8, route="Mon->Sat"),
        _state("tp-hcm", start + timedelta(days=7), 2, route="Sat->Mon"),
        _state("tp-hcm", start + timedelta(days=9), 8, route="Mon->Sat"),
        _state("tp-hcm", start + timedelta(days=14), 2, route="Sat->Mon"),
    )

    forecast = estimate_transition(
        local,
        local,
        TransitionEstimatorConfig(prior_strength=1.0),
    )

    assert forecast.unit_share[8] > forecast.unit_share[2]
    assert forecast.decomposition.route_matched_weight == pytest.approx(
        forecast.decomposition.local_weight
    )


def test_calibration_uses_strict_cutoff_and_requires_minimum_folds() -> None:
    start = date(2026, 1, 1)
    observations = tuple(
        CalibrationObservation(
            observed_at=start + timedelta(days=index),
            likelihood=0.8 if index % 2 else 0.2,
            outcome=index % 2,
        )
        for index in range(12)
    )
    cutoff = start + timedelta(days=10)
    gated = expanding_walk_forward_calibrate(
        observations,
        cutoff=cutoff,
        config=ReliabilityCalibrationConfig(minimum_training_samples=4, minimum_folds=7),
    )
    promoted = expanding_walk_forward_calibrate(
        observations,
        cutoff=cutoff,
        config=ReliabilityCalibrationConfig(minimum_training_samples=4, minimum_folds=6),
    )

    assert gated.observations_used == 10
    assert gated.fold_count == 6
    assert gated.status == "uncalibrated"
    assert gated.calibrated_probabilities is None
    assert gated.uncalibrated_likelihoods == gated.raw_likelihoods
    assert promoted.status == "calibrated"
    assert promoted.calibrated_probabilities is not None
    assert promoted.brier.raw_brier is not None
    assert promoted.brier.calibrated_brier is not None
    assert all(
        fold.observed_at < cutoff and fold.training_size == 4 + index
        for index, fold in enumerate(promoted.folds)
    )


def test_calibration_never_uses_outcomes_from_the_same_timestamp() -> None:
    first = date(2026, 1, 1)
    second = date(2026, 1, 8)
    observations = (
        CalibrationObservation(first, 0.1, 0),
        CalibrationObservation(first, 0.9, 1),
        CalibrationObservation(second, 0.2, 0),
        CalibrationObservation(second, 0.8, 1),
    )

    result = expanding_walk_forward_calibrate(
        observations,
        config=ReliabilityCalibrationConfig(
            minimum_training_samples=2,
            minimum_folds=2,
        ),
    )

    assert result.status == "calibrated"
    assert len(result.folds) == 2
    assert all(fold.observed_at == second for fold in result.folds)
    assert all(fold.training_size == 2 for fold in result.folds)


def test_coupling_lift_distinguishes_positive_and_negative_history_and_obeys_bounds() -> None:
    positive = tuple(
        CoupledDrawObservation(frozenset({23}), frozenset({23}))
        for _ in range(20)
    )
    negative = tuple(
        CoupledDrawObservation(
            frozenset({23}) if index < 10 else frozenset(),
            frozenset() if index < 10 else frozenset({23}),
        )
        for index in range(20)
    )
    forecast_a = _forecast("dong-nai", 23, 0.90)
    forecast_b = _forecast("can-tho", 23, 0.80)
    config = CouplingMergeConfig(exact_pair_shrinkage=1.0, suffix_shrinkage=1.0)

    positive_result = merge_province_forecasts(forecast_a, forecast_b, positive, config)
    negative_result = merge_province_forecasts(forecast_a, forecast_b, negative, config)
    positive_item = positive_result.decomposition[23]
    negative_item = negative_result.decomposition[23]

    assert positive_item.coupling_lift > 1.0
    assert negative_item.coupling_lift < 1.0
    assert positive_item.coupling_lift > negative_item.coupling_lift
    for item in (positive_item, negative_item):
        assert item.frechet_lower <= item.joint_likelihood <= item.frechet_upper
        assert item.union_likelihood == pytest.approx(
            item.likelihood_a + item.likelihood_b - item.joint_likelihood
        )
    independent_joint = 0.90 * 0.80
    assert positive_item.joint_likelihood > independent_joint
    assert negative_item.joint_likelihood < independent_joint


def test_merge_averages_unit_mass_and_does_not_weight_pair_union_by_unit_again() -> None:
    forecast_a = _forecast("dong-nai", 43, 0.22, unit_digit=3)
    forecast_b = _forecast("can-tho", 43, 0.15, unit_digit=7)

    result = merge_province_forecasts(forecast_a, forecast_b)

    assert sum(result.unit_share) == pytest.approx(1.0)
    assert result.unit_share == pytest.approx(
        tuple((a + b) / 2.0 for a, b in zip(forecast_a.unit_share, forecast_b.unit_share))
    )
    assert result.score_semantics.endswith("uncalibrated")
    assert result.pair_union_likelihoods[43] == pytest.approx(0.22 + 0.15 - 0.22 * 0.15)


def _unit_shares() -> tuple[float, ...]:
    values = [0.04] * 10
    values[3] = 0.30
    values[2] = 0.20
    values[1] = 0.18
    values[0] += 1.0 - sum(values)
    return tuple(values)


def test_selector_requires_top_unit_and_caps_same_suffix_at_two() -> None:
    scores = {13: 0.10, 23: 0.09, 12: 0.99, 22: 0.98, 11: 0.97}

    result = select_top_three(scores, _unit_shares())

    assert result.status == "success"
    assert len(set(result.selected_pairs)) == 3
    assert any(pair % 10 == 3 for pair in result.selected_pairs)
    assert max(
        sum(selected % 10 == digit for selected in result.selected_pairs)
        for digit in range(10)
    ) <= 2


def test_selector_compares_two_plus_one_against_one_plus_one_plus_one() -> None:
    two_plus_one = select_top_three(
        {13: 0.90, 23: 0.85, 12: 0.80, 11: 0.10},
        _unit_shares(),
    )
    one_each = select_top_three(
        {13: 0.90, 23: 0.20, 12: 0.85, 11: 0.84},
        _unit_shares(),
    )

    assert two_plus_one.configuration == "2+1"
    assert two_plus_one.selected_pairs == (13, 23, 12)
    assert one_each.configuration == "1+1+1"
    assert one_each.selected_pairs == (13, 12, 11)


def test_selector_does_not_double_weight_units_and_returns_insufficient_without_padding() -> None:
    scores = {13: 0.70, 23: 0.69, 12: 0.68, 11: 0.67}
    shares_a = _unit_shares()
    shares_b = list(shares_a)
    shares_b[3], shares_b[2], shares_b[1] = 0.45, 0.14, 0.09
    shares_b[0] += 1.0 - sum(shares_b)

    first = select_top_three(scores, shares_a)
    second = select_top_three(dict(reversed(tuple(scores.items()))), tuple(shares_b))
    insufficient = select_top_three({13: 0.9, 12: 0.8}, shares_a)

    assert first == second
    assert first.selected_pairs == (13, 23, 12)
    assert insufficient.status == "insufficient"
    assert insufficient.selected_pairs == ()
