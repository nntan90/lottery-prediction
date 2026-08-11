"""Application service for the province-first XSMN PDA/DDT shadow model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

from .calibration import CalibrationObservation, ReliabilityModel, fit_reliability_model
from .config import DigitTransitionConfig
from .domain import (
    DrawSnapshot,
    build_freshness_manifest,
    fingerprint_draw_history,
    normalize_tail_rows,
    validate_provinces,
)
from .estimator import (
    ProvinceTransitionForecast,
    TransitionEstimatorConfig,
    estimate_transition,
)
from .merge import (
    CoupledDrawObservation,
    CouplingMergeConfig,
    MergedProvinceForecast,
    merge_province_forecasts,
)
from .repository import load_boundary_sources, load_regional_tail_history
from .selector import select_from_merged_forecast
from .state import DrawDigitState, build_state_sequences
from src.xsmn_ensemble.resolve_provinces import (
    XSMN_ENSEMBLE_SCHEDULE,
    get_previous_scheduled_date,
    get_scheduled_provinces,
)


@dataclass(frozen=True)
class _ForecastForMerge:
    province: str
    unit_share: tuple[float, ...]
    pair_hit_likelihoods: tuple[float, ...]
    score_semantics: str


def _estimator_config(config: DigitTransitionConfig) -> TransitionEstimatorConfig:
    return TransitionEstimatorConfig(
        prior_strength=config.province_prior_strength,
        minimum_effective_samples=float(config.min_transitions),
        top_k_states=config.top_k_states,
        interaction_prior_strength=config.interaction_prior_strength,
        pair_prior_strength=config.pair_prior_strength,
        hierarchical_transitions_per_province=(
            config.regional_prior_transitions_per_province
        ),
    )


def _coupling_config(config: DigitTransitionConfig) -> CouplingMergeConfig:
    return CouplingMergeConfig(
        exact_pair_shrinkage=config.coupling_prior_strength,
        suffix_shrinkage=config.coupling_prior_strength * 2.0,
        province_pair_shrinkage=config.coupling_prior_strength * 3.0,
    )


def _oof_forecasts(
    states: tuple[DrawDigitState, ...],
    hierarchical_states: tuple[DrawDigitState, ...],
    config: DigitTransitionConfig,
    anchor_dates: Optional[frozenset[date]] = None,
) -> dict[date, ProvinceTransitionForecast]:
    """Create bounded OOF forecasts while retaining the full prior history."""
    forecasts: dict[date, ProvinceTransitionForecast] = {}
    estimator_config = _estimator_config(config)
    start = max(config.min_transitions + 1, 3)
    for target_index in range(start, len(states)):
        training = states[:target_index]
        actual = states[target_index]
        if anchor_dates is not None and actual.draw_date not in anchor_dates:
            continue
        hierarchical_training = tuple(
            state
            for state in hierarchical_states
            if state.draw_date < actual.draw_date
        )
        forecast = estimate_transition(
            training,
            hierarchical_training,
            estimator_config,
        )
        forecasts[actual.draw_date] = forecast
    return forecasts


def _bounded_oof_anchor_dates(
    states_by_province: Mapping[str, tuple[DrawDigitState, ...]],
    config: DigitTransitionConfig,
) -> Mapping[str, frozenset[date]]:
    """Select a deterministic union of recent local and shared OOF anchors."""
    eligible: dict[str, tuple[date, ...]] = {}
    start = max(config.min_transitions + 1, 3)
    for province, states in states_by_province.items():
        eligible[province] = tuple(state.draw_date for state in states[start:])

    common = set.intersection(*(set(values) for values in eligible.values()))
    common_recent = frozenset(sorted(common)[-config.oof_recent_common_anchors :])
    return {
        province: frozenset(
            set(values[-config.oof_recent_anchors_per_province :]) | common_recent
        )
        for province, values in eligible.items()
    }


def _oof_observations(
    states: tuple[DrawDigitState, ...],
    forecasts: Mapping[date, ProvinceTransitionForecast],
) -> tuple[CalibrationObservation, ...]:
    """Flatten strict OOF forecasts into pair-level reliability outcomes."""
    actual_by_date = {state.draw_date: state for state in states}
    observations: list[CalibrationObservation] = []
    for observed_at in sorted(forecasts):
        forecast = forecasts[observed_at]
        actual = actual_by_date[observed_at]
        for pair, likelihood in enumerate(forecast.pair_hit_likelihoods):
            observations.append(
                CalibrationObservation(
                    observed_at=actual.draw_date,
                    likelihood=likelihood,
                    outcome=int(actual.exact_pair_counts[pair] > 0),
                )
            )
    return tuple(observations)


def _calibrate_forecast(
    forecast: ProvinceTransitionForecast,
    states: tuple[DrawDigitState, ...],
    oof_forecasts: Mapping[date, ProvinceTransitionForecast],
    target_date: date,
    config: DigitTransitionConfig,
) -> tuple[_ForecastForMerge, ReliabilityModel]:
    observations = _oof_observations(states, oof_forecasts)
    model = fit_reliability_model(
        observations,
        cutoff=target_date,
        bins=config.calibration_bins,
        minimum_draws=config.calibration_min_folds,
    )
    values = model.apply(forecast.pair_hit_likelihoods)
    semantics = (
        "pair_hit_probability_calibrated"
        if model.status == "calibrated"
        else "estimated_hit_likelihood_uncalibrated"
    )
    return (
        _ForecastForMerge(
            province=forecast.province,
            unit_share=forecast.unit_share,
            pair_hit_likelihoods=values,
            score_semantics=semantics,
        ),
        model,
    )


def _dated_coupling_history(
    draws_by_province: Mapping[str, tuple[DrawSnapshot, ...]],
    provinces: tuple[str, str],
) -> tuple[tuple[date, CoupledDrawObservation], ...]:
    by_date_a = {draw.draw_date: draw for draw in draws_by_province[provinces[0]]}
    by_date_b = {draw.draw_date: draw for draw in draws_by_province[provinces[1]]}
    return tuple(
        (
            draw_date,
            CoupledDrawObservation(
                pairs_a=frozenset(by_date_a[draw_date].tails),
                pairs_b=frozenset(by_date_b[draw_date].tails),
            ),
        )
        for draw_date in sorted(set(by_date_a) & set(by_date_b))
    )


def _regional_coupling_history(
    draws_by_province: Mapping[str, tuple[DrawSnapshot, ...]],
) -> tuple[tuple[date, CoupledDrawObservation], ...]:
    """Pool same-date XSMN province pairs as the top coupling prior."""
    draws_by_date: dict[date, list[DrawSnapshot]] = {}
    for province in sorted(draws_by_province):
        for draw in draws_by_province[province]:
            draws_by_date.setdefault(draw.draw_date, []).append(draw)
    observations: list[tuple[date, CoupledDrawObservation]] = []
    for draw_date in sorted(draws_by_date):
        ordered = sorted(draws_by_date[draw_date], key=lambda draw: draw.province)
        for left, right in combinations(ordered, 2):
            observations.append(
                (
                    draw_date,
                    CoupledDrawObservation(
                        pairs_a=frozenset(left.tails),
                        pairs_b=frozenset(right.tails),
                    ),
                )
            )
    return tuple(observations)


def _merged_oof_observations(
    province_pair: tuple[str, str],
    forecasts: Mapping[str, Mapping[date, ProvinceTransitionForecast]],
    states_by_province: Mapping[str, tuple[DrawDigitState, ...]],
    dated_pair_history: tuple[tuple[date, CoupledDrawObservation], ...],
    dated_regional_history: tuple[tuple[date, CoupledDrawObservation], ...],
    config: DigitTransitionConfig,
) -> tuple[CalibrationObservation, ...]:
    """Generate raw post-coupling scores and union outcomes for each OOF draw."""
    common_dates = sorted(
        set(forecasts[province_pair[0]]) & set(forecasts[province_pair[1]])
    )
    actual = {
        province: {state.draw_date: state for state in states_by_province[province]}
        for province in province_pair
    }
    observations: list[CalibrationObservation] = []
    for observed_at in common_dates:
        pair_history = tuple(
            observation
            for draw_date, observation in dated_pair_history
            if draw_date < observed_at
        )
        regional_history = tuple(
            observation
            for draw_date, observation in dated_regional_history
            if draw_date < observed_at
        )
        merged = merge_province_forecasts(
            forecasts[province_pair[0]][observed_at],
            forecasts[province_pair[1]][observed_at],
            pair_history,
            _coupling_config(config),
            regional_coupling_history=regional_history,
        )
        actual_a = actual[province_pair[0]][observed_at]
        actual_b = actual[province_pair[1]][observed_at]
        for pair, likelihood in enumerate(merged.pair_union_likelihoods):
            observations.append(
                CalibrationObservation(
                    observed_at=observed_at,
                    likelihood=likelihood,
                    outcome=int(
                        actual_a.exact_pair_counts[pair] > 0
                        or actual_b.exact_pair_counts[pair] > 0
                    ),
                )
            )
    return tuple(observations)


def _calibrate_merged_forecast(
    merged: MergedProvinceForecast,
    observations: tuple[CalibrationObservation, ...],
    target_date: date,
    config: DigitTransitionConfig,
) -> tuple[MergedProvinceForecast, ReliabilityModel]:
    model = fit_reliability_model(
        observations,
        cutoff=target_date,
        bins=config.calibration_bins,
        minimum_draws=config.calibration_min_folds,
    )
    values = model.apply(merged.pair_union_likelihoods)
    semantics = (
        "merged_pair_hit_probability_calibrated"
        if model.status == "calibrated"
        else "merged_pair_hit_likelihood_uncalibrated"
    )
    return (
        replace(
            merged,
            score_semantics=semantics,
            pair_union_likelihoods=values,
        ),
        model,
    )


def _calibration_payload(model: ReliabilityModel) -> dict:
    return {
        "status": model.status,
        "oof_observations": model.observation_count,
        "oof_draws": model.draw_count,
        "validation_draws": model.validation_draw_count,
        "raw_brier": model.raw_brier,
        "calibrated_brier": model.calibrated_brier,
        "bin_support": list(model.bin_support),
    }


def _province_payload(
    forecast: ProvinceTransitionForecast,
    calibrated: _ForecastForMerge,
    calibration: ReliabilityModel,
) -> dict:
    score_key = (
        "probability"
        if calibrated.score_semantics.endswith("calibrated")
        and not calibrated.score_semantics.endswith("uncalibrated")
        else "estimated_likelihood_uncalibrated"
    )
    ranked = sorted(
        range(100),
        key=lambda pair: (-calibrated.pair_hit_likelihoods[pair], pair),
    )
    return {
        "province": forecast.province,
        "anchor_date": str(forecast.current_draw_date),
        "score_semantics": calibrated.score_semantics,
        "unit_share": [
            {
                "digit": digit,
                "share": forecast.unit_share[digit],
                "leader_likelihood": forecast.unit_leader_likelihoods[digit],
            }
            for digit in range(10)
        ],
        "head_share": list(forecast.head_share),
        "confidence": forecast.confidence,
        "effective_sample_size": forecast.effective_sample_size,
        "local_transition_count": forecast.decomposition.local_transition_count,
        "route_matched_weight": forecast.decomposition.route_matched_weight,
        "calibration": _calibration_payload(calibration),
        "top_pairs": [
            {"pair": pair, score_key: calibrated.pair_hit_likelihoods[pair]}
            for pair in ranked[:10]
        ],
    }


def _insufficient(
    target_date: date,
    provinces: tuple[str, str],
    reason: str,
    **details: object,
) -> dict:
    return {
        "model_name": "provincial_digit_transition_v1",
        "mode": "shadow",
        "status": "insufficient_evidence",
        "reason": reason,
        "target_date": target_date.isoformat(),
        "data_cutoff": target_date.isoformat(),
        "provinces": list(provinces),
        "top_3": [],
        **details,
    }


def _freshness_dimensions(
    provinces: Sequence[str],
    target_date: date,
) -> tuple[tuple[str, str], dict[str, date], date, tuple[str, ...]]:
    """Resolve the approved target anchors and full regional D-1 boundary."""
    province_scope = validate_provinces(list(provinces))
    if len(province_scope) != 2:
        raise ValueError("PDA/DDT requires exactly two distinct XSMN provinces")
    scheduled = tuple(XSMN_ENSEMBLE_SCHEDULE.get(target_date.weekday(), ()))
    if province_scope != scheduled:
        raise ValueError("PDA/DDT provinces must match the target-date schedule")
    province_pair = (province_scope[0], province_scope[1])
    expected_anchors = {
        province: get_previous_scheduled_date(target_date, province)
        for province in province_pair
    }
    regional_boundary_date = target_date - timedelta(days=1)
    regional_provinces = tuple(get_scheduled_provinces(regional_boundary_date))
    if not regional_provinces:
        raise ValueError("XSMN regional boundary schedule is empty")
    return (
        province_pair,
        expected_anchors,
        regional_boundary_date,
        regional_provinces,
    )


def load_current_freshness_manifest(
    db: Any,
    provinces: Sequence[str],
    target_date: date,
) -> dict:
    """Query and certify the current leakage-safe DDT boundary input."""
    (
        province_pair,
        expected_anchors,
        regional_boundary_date,
        regional_provinces,
    ) = _freshness_dimensions(provinces, target_date)
    required_draws = [
        (province, expected_anchors[province])
        for province in province_pair
    ]
    required_draws.extend(
        (province, regional_boundary_date)
        for province in regional_provinces
    )
    raw_rows, tail_rows = load_boundary_sources(db, required_draws)
    return build_freshness_manifest(
        target_date=target_date,
        target_provinces=province_pair,
        expected_anchors=expected_anchors,
        regional_boundary_date=regional_boundary_date,
        regional_provinces=regional_provinces,
        raw_rows=raw_rows,
        tail_rows=tail_rows,
    )


def _with_run_audit(
    result: dict,
    manifest: Mapping[str, object],
    *,
    checked_at: str,
    postcheck_manifest: Optional[Mapping[str, object]] = None,
) -> dict:
    """Attach the certified input contract without changing model output."""
    audited = dict(result)
    existing = audited.get("run_metadata")
    run_metadata = dict(existing) if isinstance(existing, dict) else {}
    run_metadata.update(
        {
            "input_manifest": dict(manifest),
            "input_checked_at": checked_at,
            "postcheck_boundary_watermark": (
                postcheck_manifest.get("boundary_watermark")
                if isinstance(postcheck_manifest, Mapping)
                else None
            ),
            "postcheck_status": (
                postcheck_manifest.get("status")
                if isinstance(postcheck_manifest, Mapping)
                else None
            ),
        }
    )
    if postcheck_manifest is not None and postcheck_manifest.get("status") != "certified":
        run_metadata["postcheck_manifest"] = dict(postcheck_manifest)
    audited["run_metadata"] = run_metadata
    return audited


def _operational_failure(
    target_date: date,
    provinces: tuple[str, str],
    reason: str,
    manifest: Mapping[str, object],
    *,
    checked_at: str,
    failure_stage: str,
) -> dict:
    """Return a stable, credential-free operational failure with input audit."""
    return _with_run_audit(
        {
            "model_name": "provincial_digit_transition_v1",
            "mode": "shadow",
            "status": "error",
            "reason": reason,
            "failure_stage": failure_stage,
            "target_date": target_date.isoformat(),
            "data_cutoff": target_date.isoformat(),
            "provinces": list(provinces),
            "top_3": [],
        },
        manifest,
        checked_at=checked_at,
    )


def predict_digit_transition(
    rows: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date,
    config: Optional[DigitTransitionConfig] = None,
    *,
    regional_rows: Optional[Iterable[Mapping[str, object]]] = None,
) -> dict:
    """Return an audit-ready two-province PDA/DDT shadow prediction.

    Each province is estimated and calibrated independently. Only those two
    forecasts are merged, and the production ensemble is never read or used as
    a feature.
    """
    province_scope = validate_provinces(list(provinces))
    if len(province_scope) != 2:
        raise ValueError("PDA/DDT requires exactly two distinct XSMN provinces")
    province_pair = (province_scope[0], province_scope[1])
    config = config or DigitTransitionConfig()
    local_rows = tuple(rows)
    draws = normalize_tail_rows(local_rows, list(province_pair), before_date=target_date)
    missing = [province for province in province_pair if not draws[province]]
    if missing:
        return _insufficient(
            target_date,
            province_pair,
            "missing_complete_anchor",
            missing_provinces=missing,
        )

    states_by_province = build_state_sequences(draws, target_date)
    transition_counts = {
        province: max(0, len(states_by_province[province]) - 1)
        for province in province_pair
    }
    weak = [
        province
        for province, count in transition_counts.items()
        if count < config.min_transitions
    ]
    if weak:
        return _insufficient(
            target_date,
            province_pair,
            "not_enough_province_transitions",
            transition_counts=transition_counts,
            required_transitions=config.min_transitions,
        )

    prior_source = tuple(regional_rows) if regional_rows is not None else local_rows
    regional_provinces = tuple(
        sorted(
            {
                str(row.get("province"))
                for row in prior_source
                if row.get("province")
            }
        )
    )
    regional_draws = (
        normalize_tail_rows(prior_source, list(regional_provinces), before_date=target_date)
        if regional_provinces
        else draws
    )
    regional_states = build_state_sequences(regional_draws, target_date)
    hierarchical_states = tuple(
        state
        for province in sorted(regional_states)
        for state in regional_states[province]
    )

    raw_forecasts: dict[str, ProvinceTransitionForecast] = {}
    merge_forecasts: dict[str, _ForecastForMerge] = {}
    calibrations: dict[str, ReliabilityModel] = {}
    oof_forecasts: dict[str, dict[date, ProvinceTransitionForecast]] = {}
    estimator_config = _estimator_config(config)
    oof_anchor_dates = _bounded_oof_anchor_dates(states_by_province, config)
    for province in province_pair:
        province_oof = _oof_forecasts(
            states_by_province[province],
            hierarchical_states,
            config,
            oof_anchor_dates[province],
        )
        forecast = estimate_transition(
            states_by_province[province],
            hierarchical_states,
            estimator_config,
        )
        calibrated, calibration = _calibrate_forecast(
            forecast,
            states_by_province[province],
            province_oof,
            target_date,
            config,
        )
        raw_forecasts[province] = forecast
        merge_forecasts[province] = calibrated
        calibrations[province] = calibration
        oof_forecasts[province] = province_oof

    dated_coupling = _dated_coupling_history(draws, province_pair)
    dated_regional_coupling = _regional_coupling_history(regional_draws)
    coupling_history = tuple(item for _, item in dated_coupling)
    regional_coupling_history = tuple(item for _, item in dated_regional_coupling)
    raw_merged = merge_province_forecasts(
        raw_forecasts[province_pair[0]],
        raw_forecasts[province_pair[1]],
        coupling_history,
        _coupling_config(config),
        regional_coupling_history=regional_coupling_history,
    )
    merged_observations = _merged_oof_observations(
        province_pair,
        oof_forecasts,
        states_by_province,
        dated_coupling,
        dated_regional_coupling,
        config,
    )
    merged, merged_calibration = _calibrate_merged_forecast(
        raw_merged,
        merged_observations,
        target_date,
        config,
    )
    selection = select_from_merged_forecast(
        merged,
        top_unit_count=config.top_unit_digits,
        candidates_per_unit=config.candidates_per_unit,
    )
    if selection.status != "success":
        return _insufficient(
            target_date,
            province_pair,
            selection.reason or "selector_failed",
            candidate_count=selection.candidate_count,
            top_unit_digits=list(selection.top_unit_digits),
        )

    calibrated_output = merged.score_semantics.endswith("calibrated") and not merged.score_semantics.endswith(
        "uncalibrated"
    )
    score_key = "probability" if calibrated_output else "estimated_likelihood_uncalibrated"
    ranked_pairs = sorted(
        range(100),
        key=lambda pair: (
            -merged.pair_hit_likelihoods[pair],
            -raw_merged.pair_hit_likelihoods[pair],
            pair,
        ),
    )
    audit = []
    for rank, pair in enumerate(ranked_pairs, start=1):
        item = merged.decomposition[pair]
        audit.append(
            {
                "rank": rank,
                "pair": pair,
                score_key: merged.pair_hit_likelihoods[pair],
                "province_a_likelihood": item.likelihood_a,
                "province_b_likelihood": item.likelihood_b,
                "province_a_interaction_lift": raw_forecasts[
                    province_pair[0]
                ].decomposition.interaction_lifts[pair],
                "province_b_interaction_lift": raw_forecasts[
                    province_pair[1]
                ].decomposition.interaction_lifts[pair],
                "coupling_lift": item.coupling_lift,
                "joint_likelihood": item.joint_likelihood,
                "raw_merged_likelihood": item.union_likelihood,
                "suffix_share": merged.unit_share[pair % 10],
                "exact_pair_support": item.exact_pair_support,
                "suffix_support": item.suffix_support,
            }
        )
    by_pair = {item["pair"]: item for item in audit}
    selected_evidence = [
        {"selection_rank": rank, **by_pair[pair]}
        for rank, pair in enumerate(selection.selected_pairs, start=1)
    ]
    return {
        "model_name": "provincial_digit_transition_v1",
        "mode": "shadow",
        "status": "success" if calibrated_output else "uncalibrated",
        "reason": None if calibrated_output else "merged_calibration_gate_not_met",
        "target_date": target_date.isoformat(),
        "data_cutoff": target_date.isoformat(),
        "provinces": list(province_pair),
        "score_semantics": merged.score_semantics,
        "prior_scope": "xsmn" if regional_rows is not None else "target_provinces",
        "transition_counts": transition_counts,
        "merged_calibration": _calibration_payload(merged_calibration),
        "per_province": {
            province: _province_payload(
                raw_forecasts[province],
                merge_forecasts[province],
                calibrations[province],
            )
            for province in province_pair
        },
        "merged_unit_share": [
            {"digit": digit, "share": merged.unit_share[digit]}
            for digit in range(10)
        ],
        "top_unit_digits": list(selection.top_unit_digits),
        "allocation": selection.configuration,
        "coupling_draw_count": len(coupling_history),
        "top_3": list(selection.selected_pairs),
        "selected_evidence": selected_evidence,
        "top_candidates": audit[:10],
        "top_100_audit": audit,
    }


def generate_shadow_prediction(
    db: Any,
    provinces: Sequence[str],
    target_date: date,
    config: Optional[DigitTransitionConfig] = None,
) -> dict:
    """Load certified current data and run PDA/DDT without persistence.

    Boundary input is queried before scoring and again afterwards.  A result is
    never returned as successful when required source data is incomplete or
    changes while the model is running.
    """
    province_scope = validate_provinces(list(provinces))
    if len(province_scope) != 2:
        raise ValueError("PDA/DDT requires exactly two distinct XSMN provinces")
    province_pair = (province_scope[0], province_scope[1])
    checked_at = datetime.now(timezone.utc).isoformat()
    manifest = load_current_freshness_manifest(db, province_pair, target_date)
    if manifest.get("status") != "certified":
        return _with_run_audit(
            _insufficient(
                target_date,
                province_pair,
                "input_not_fresh",
                input_issues=list(manifest.get("issues") or ()),
            ),
            manifest,
            checked_at=checked_at,
        )

    try:
        regional_rows = load_regional_tail_history(db, target_date)
    except Exception:
        return _operational_failure(
            target_date,
            province_pair,
            "history_load_failed",
            manifest,
            checked_at=checked_at,
            failure_stage="history_load",
        )
    try:
        province_set = set(province_scope)
        rows = tuple(
            row
            for row in regional_rows
            if str(row.get("province")) in province_set
        )
        regional_provinces = tuple(
            sorted(
                {
                    str(row.get("province"))
                    for row in regional_rows
                    if row.get("province")
                }
            )
        )
        regional_draws = (
            normalize_tail_rows(
                regional_rows,
                list(regional_provinces),
                before_date=target_date,
            )
            if regional_provinces
            else {}
        )
        consumed_anchors = {
            province: (
                regional_draws[province][-1].draw_date.isoformat()
                if regional_draws.get(province)
                else None
            )
            for province in province_pair
        }
        audited_manifest = dict(manifest)
        audited_manifest.update(
            {
                "consumed_anchors": consumed_anchors,
                "full_history_hash": fingerprint_draw_history(
                    regional_draws,
                    target_date=target_date,
                    target_provinces=province_pair,
                ),
                "full_history_draw_count": sum(
                    len(draws) for draws in regional_draws.values()
                ),
                "full_history_tail_count": sum(
                    len(draw.tails)
                    for draws in regional_draws.values()
                    for draw in draws
                ),
            }
        )
    except Exception:
        return _operational_failure(
            target_date,
            province_pair,
            "history_normalization_failed",
            manifest,
            checked_at=checked_at,
            failure_stage="history_normalization",
        )

    expected_anchors = audited_manifest.get("expected_anchors")
    if consumed_anchors != expected_anchors:
        result = _insufficient(
            target_date,
            province_pair,
            "consumed_anchor_mismatch",
            expected_anchors=expected_anchors,
            consumed_anchors=consumed_anchors,
        )
    else:
        try:
            result = predict_digit_transition(
                rows,
                province_scope,
                target_date,
                config,
                regional_rows=regional_rows,
            )
        except Exception:
            return _operational_failure(
                target_date,
                province_pair,
                "scoring_failed",
                audited_manifest,
                checked_at=checked_at,
                failure_stage="scoring",
            )
    try:
        postcheck = load_current_freshness_manifest(db, province_pair, target_date)
    except Exception:
        return _operational_failure(
            target_date,
            province_pair,
            "freshness_postcheck_failed",
            audited_manifest,
            checked_at=checked_at,
            failure_stage="freshness_postcheck",
        )
    if (
        postcheck.get("status") != "certified"
        or postcheck.get("boundary_watermark")
        != manifest.get("boundary_watermark")
    ):
        result = _insufficient(
            target_date,
            province_pair,
            "input_changed_during_run",
            input_issues=list(postcheck.get("issues") or ()),
        )
    return _with_run_audit(
        result,
        audited_manifest,
        checked_at=checked_at,
        postcheck_manifest=postcheck,
    )
