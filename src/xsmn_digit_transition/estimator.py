"""Pure statistical estimator for province-specific XSMN digit transitions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Protocol, Sequence, Tuple


class DigitStateLike(Protocol):
    """Structural contract consumed from ``state.DigitDrawState``."""

    province: str
    draw_date: object
    pair_counts: Tuple[int, ...]
    head_counts: Tuple[int, ...]
    unit_counts: Tuple[int, ...]
    head_share: Tuple[float, ...]
    unit_share: Tuple[float, ...]
    dominant_heads: Tuple[int, ...]
    dominant_units: Tuple[int, ...]
    head_max_count: int
    unit_max_count: int
    head_margin: int
    unit_margin: int
    head_entropy: float
    unit_entropy: float
    route: str


@dataclass(frozen=True)
class TransitionEstimatorConfig:
    """Controls similarity, recency weighting and hierarchical shrinkage."""

    histogram_weight: float = 0.48
    dominant_weight: float = 0.14
    strength_weight: float = 0.12
    margin_weight: float = 0.08
    entropy_weight: float = 0.08
    route_weight: float = 0.10
    recency_decay: float = 0.035
    prior_strength: float = 8.0
    minimum_effective_samples: float = 6.0
    minimum_similarity: float = 1e-9
    top_k_states: int = 32
    interaction_prior_strength: float = 16.0
    pair_prior_strength: float = 12.0
    hierarchical_transitions_per_province: int = 52

    def __post_init__(self) -> None:
        weights = (
            self.histogram_weight,
            self.dominant_weight,
            self.strength_weight,
            self.margin_weight,
            self.entropy_weight,
            self.route_weight,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in weights):
            raise ValueError("similarity weights must be finite and non-negative")
        if sum(weights) <= 0.0:
            raise ValueError("at least one similarity weight must be positive")
        if not math.isfinite(self.recency_decay) or self.recency_decay < 0.0:
            raise ValueError("recency_decay must be finite and non-negative")
        if not math.isfinite(self.prior_strength) or self.prior_strength <= 0.0:
            raise ValueError("prior_strength must be finite and positive")
        if not math.isfinite(self.minimum_effective_samples) or self.minimum_effective_samples <= 0.0:
            raise ValueError("minimum_effective_samples must be finite and positive")
        if not math.isfinite(self.minimum_similarity) or self.minimum_similarity < 0.0:
            raise ValueError("minimum_similarity must be finite and non-negative")
        if self.top_k_states < 1:
            raise ValueError("top_k_states must be positive")
        if self.hierarchical_transitions_per_province < 1:
            raise ValueError("hierarchical_transitions_per_province must be positive")
        if (
            not math.isfinite(self.interaction_prior_strength)
            or self.interaction_prior_strength <= 0.0
            or not math.isfinite(self.pair_prior_strength)
            or self.pair_prior_strength <= 0.0
        ):
            raise ValueError("pair and interaction prior strengths must be positive")


@dataclass(frozen=True)
class TransitionSample:
    """A leakage-safe consecutive transition within one province."""

    current: DigitStateLike
    next_state: DigitStateLike


@dataclass(frozen=True)
class TransitionDecomposition:
    """Audit components before and after province/prior blending."""

    local_head_share: Tuple[float, ...]
    prior_head_share: Tuple[float, ...]
    local_unit_share: Tuple[float, ...]
    prior_unit_share: Tuple[float, ...]
    local_head_leader_likelihoods: Tuple[float, ...]
    prior_head_leader_likelihoods: Tuple[float, ...]
    local_unit_leader_likelihoods: Tuple[float, ...]
    prior_unit_leader_likelihoods: Tuple[float, ...]
    local_pair_hit_likelihoods: Tuple[float, ...]
    prior_pair_hit_likelihoods: Tuple[float, ...]
    local_weight: float
    prior_weight: float
    local_transition_count: int
    prior_transition_count: int
    route_matched_weight: float
    local_pair_mass: Tuple[float, ...]
    prior_pair_mass: Tuple[float, ...]
    interaction_lifts: Tuple[float, ...]


@dataclass(frozen=True)
class ProvinceTransitionForecast:
    """Uncalibrated next-draw forecast for one independently estimated province."""

    province: str
    current_draw_date: object
    score_semantics: str
    head_share: Tuple[float, ...]
    unit_share: Tuple[float, ...]
    head_leader_likelihoods: Tuple[float, ...]
    unit_leader_likelihoods: Tuple[float, ...]
    pair_hit_likelihoods: Tuple[float, ...]
    confidence: float
    effective_sample_size: float
    decomposition: TransitionDecomposition

    @property
    def pair_estimated_hit_likelihoods(self) -> Tuple[float, ...]:
        """Explicit alias used by audit/report layers."""
        return self.pair_hit_likelihoods


def _date_key(state: DigitStateLike) -> str:
    value = state.draw_date
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _attribute(state: DigitStateLike, *names: str) -> object:
    for name in names:
        if hasattr(state, name):
            return getattr(state, name)
    raise AttributeError(f"digit state is missing required attribute aliases: {names}")


def _pair_counts(state: DigitStateLike) -> Sequence[int]:
    return _attribute(state, "pair_counts", "exact_pair_counts", "pair_multiplicity")  # type: ignore[return-value]


def _dominant_digits(state: DigitStateLike, axis: str) -> Sequence[int]:
    return _attribute(state, f"dominant_{axis}s", f"{axis}_dominant_digits")  # type: ignore[return-value]


def _route(state: DigitStateLike) -> str:
    return str(_attribute(state, "route", "route_label"))


def _normalized(values: Sequence[float], expected: int) -> Tuple[float, ...]:
    if len(values) != expected:
        raise ValueError(f"expected {expected} values, got {len(values)}")
    numeric = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or value < -1e-12 for value in numeric):
        raise ValueError("distribution inputs must be finite and non-negative")
    numeric = tuple(max(0.0, value) for value in numeric)
    total = sum(numeric)
    if total <= 0.0:
        return tuple(1.0 / expected for _ in range(expected))
    normalized = [value / total for value in numeric]
    normalized[-1] += 1.0 - sum(normalized)
    return tuple(normalized)


def _histogram(state: DigitStateLike, attribute: str) -> Tuple[float, ...]:
    return _normalized(getattr(state, attribute), 10)


def _jaccard(left: Sequence[int], right: Sequence[int]) -> float:
    left_set, right_set = set(left), set(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / len(left_set | right_set)


def _bounded_similarity(left: float, right: float, scale: float) -> float:
    return max(0.0, 1.0 - abs(float(left) - float(right)) / max(scale, 1e-12))


def state_similarity(
    query: DigitStateLike,
    candidate: DigitStateLike,
    config: TransitionEstimatorConfig | None = None,
) -> tuple[float, Mapping[str, float]]:
    """Compare complete dynamic digit states without suppressing repeats."""
    config = config or TransitionEstimatorConfig()
    query_head = _histogram(query, "head_counts")
    query_unit = _histogram(query, "unit_counts")
    candidate_head = _histogram(candidate, "head_counts")
    candidate_unit = _histogram(candidate, "unit_counts")

    histogram = 1.0 - (
        sum(abs(a - b) for a, b in zip(query_head, candidate_head))
        + sum(abs(a - b) for a, b in zip(query_unit, candidate_unit))
    ) / 4.0
    dominant = (
        _jaccard(_dominant_digits(query, "head"), _dominant_digits(candidate, "head"))
        + _jaccard(_dominant_digits(query, "unit"), _dominant_digits(candidate, "unit"))
    ) / 2.0
    query_mass = max(sum(query.head_counts), sum(query.unit_counts), 1)
    candidate_mass = max(sum(candidate.head_counts), sum(candidate.unit_counts), 1)
    strength = (
        _bounded_similarity(
            query.head_max_count / query_mass,
            candidate.head_max_count / candidate_mass,
            1.0,
        )
        + _bounded_similarity(
            query.unit_max_count / query_mass,
            candidate.unit_max_count / candidate_mass,
            1.0,
        )
    ) / 2.0
    margin = (
        _bounded_similarity(query.head_margin / query_mass, candidate.head_margin / candidate_mass, 1.0)
        + _bounded_similarity(query.unit_margin / query_mass, candidate.unit_margin / candidate_mass, 1.0)
    ) / 2.0
    entropy_scale = (
        1.0
        if max(query.head_entropy, candidate.head_entropy, query.unit_entropy, candidate.unit_entropy) <= 1.0
        else math.log(10.0)
    )
    entropy = (
        _bounded_similarity(query.head_entropy, candidate.head_entropy, entropy_scale)
        + _bounded_similarity(query.unit_entropy, candidate.unit_entropy, entropy_scale)
    ) / 2.0
    route = 1.0 if _route(query) == _route(candidate) else 0.0

    components = {
        "histogram": max(0.0, histogram),
        "dominant": dominant,
        "strength": strength,
        "margin": margin,
        "entropy": entropy,
        "route": route,
    }
    weighted = (
        config.histogram_weight * components["histogram"]
        + config.dominant_weight * dominant
        + config.strength_weight * strength
        + config.margin_weight * margin
        + config.entropy_weight * entropy
        + config.route_weight * route
    )
    total_weight = (
        config.histogram_weight
        + config.dominant_weight
        + config.strength_weight
        + config.margin_weight
        + config.entropy_weight
        + config.route_weight
    )
    return weighted / total_weight, components


def build_transition_samples(states: Iterable[DigitStateLike]) -> tuple[TransitionSample, ...]:
    """Build only consecutive transitions after grouping by province."""
    grouped: dict[str, list[DigitStateLike]] = defaultdict(list)
    for state in states:
        grouped[str(state.province)].append(state)

    samples: list[TransitionSample] = []
    for province in sorted(grouped):
        ordered = sorted(grouped[province], key=_date_key)
        if len({_date_key(state) for state in ordered}) != len(ordered):
            raise ValueError(f"duplicate draw_date for province {province}")
        samples.extend(
            TransitionSample(current=current, next_state=next_state)
            for current, next_state in zip(ordered, ordered[1:])
        )
    return tuple(samples)


def _leader_distribution(states: Sequence[DigitStateLike], axis: str) -> Tuple[float, ...]:
    totals = [0.0] * 10
    if not states:
        return tuple(0.1 for _ in range(10))
    for state in states:
        leaders = tuple(int(value) for value in _dominant_digits(state, axis))
        if not leaders:
            continue
        share = 1.0 / len(leaders)
        for digit in leaders:
            if not 0 <= digit <= 9:
                raise ValueError("dominant digit must be between 0 and 9")
            totals[digit] += share
    return _normalized(totals, 10)


def _weighted_average(
    rows: Sequence[Sequence[float]],
    weights: Sequence[float],
    size: int,
    fallback: Sequence[float],
) -> Tuple[float, ...]:
    total = sum(weights)
    if not rows or total <= 0.0:
        return tuple(float(value) for value in fallback)
    values = [sum(weight * float(row[index]) for row, weight in zip(rows, weights)) / total for index in range(size)]
    return _normalized(values, size)


def _weighted_pair_hits(
    states: Sequence[DigitStateLike],
    weights: Sequence[float],
    fallback: Sequence[float],
) -> Tuple[float, ...]:
    total = sum(weights)
    if not states or total <= 0.0:
        return tuple(float(value) for value in fallback)
    values = []
    for pair in range(100):
        values.append(
            sum(weight for state, weight in zip(states, weights) if int(_pair_counts(state)[pair]) > 0) / total
        )
    return tuple(values)


def _weighted_pair_mass(
    states: Sequence[DigitStateLike],
    weights: Sequence[float],
    fallback: Sequence[float],
) -> Tuple[float, ...]:
    total = sum(weights)
    if not states or total <= 0.0:
        return tuple(float(value) for value in fallback)
    draw_mass = max(sum(_pair_counts(states[0])), 1)
    return tuple(
        sum(
            weight * float(_pair_counts(state)[pair]) / draw_mass
            for state, weight in zip(states, weights)
        )
        / total
        for pair in range(100)
    )


def _blend(local: Sequence[float], prior: Sequence[float], local_weight: float, prior_weight: float) -> Tuple[float, ...]:
    denominator = local_weight + prior_weight
    return tuple(
        (local_weight * float(local_value) + prior_weight * float(prior_value)) / denominator
        for local_value, prior_value in zip(local, prior)
    )


def _state_prior(
    states: Sequence[DigitStateLike],
    current: DigitStateLike,
) -> tuple[Tuple[float, ...], Tuple[float, ...], Tuple[float, ...], Tuple[float, ...], Tuple[float, ...], Tuple[float, ...]]:
    """Summarize next-state evidence at one hierarchy level."""
    if not states:
        pair_hit = 1.0 - (1.0 - 0.01) ** max(sum(_pair_counts(current)), 18)
        return (
            (0.1,) * 10,
            (0.1,) * 10,
            (0.1,) * 10,
            (0.1,) * 10,
            (pair_hit,) * 100,
            (0.01,) * 100,
        )
    head = _normalized(
        [sum(state.head_share[digit] for state in states) for digit in range(10)], 10
    )
    unit = _normalized(
        [sum(state.unit_share[digit] for state in states) for digit in range(10)], 10
    )
    pair_support = [0] * 100
    pair_totals = [0.0] * 100
    for state in states:
        counts = _pair_counts(state)
        for pair, count in enumerate(counts):
            pair_support[pair] += int(count > 0)
            pair_totals[pair] += float(count)
    pair_hit = tuple(support / len(states) for support in pair_support)
    draw_mass = max(sum(_pair_counts(states[0])), 1)
    pair_mass = tuple(total / (len(states) * draw_mass) for total in pair_totals)
    return (
        head,
        unit,
        _leader_distribution(states, "head"),
        _leader_distribution(states, "unit"),
        pair_hit,
        pair_mass,
    )


def _hierarchical_blend(
    child: Sequence[float],
    parent: Sequence[float],
    support: int,
    strength: float,
    *,
    normalized: bool,
) -> Tuple[float, ...]:
    values = _blend(child, parent, float(support), strength)
    return _normalized(values, len(values)) if normalized else values


def estimate_transition(
    states: Iterable[DigitStateLike],
    hierarchical_states: Iterable[DigitStateLike] = (),
    config: TransitionEstimatorConfig | None = None,
) -> ProvinceTransitionForecast:
    """Estimate the next digit state from one province's latest known state.

    Local evidence is made from consecutive transitions in ``states``. The
    hierarchical prior may contain multiple provinces, but transitions after
    the local current draw are excluded to preserve the forecast cutoff.
    """
    config = config or TransitionEstimatorConfig()
    local_states = tuple(states)
    if not local_states:
        raise ValueError("at least one province state is required")
    provinces = {str(state.province) for state in local_states}
    if len(provinces) != 1:
        raise ValueError("local states must belong to exactly one province")
    province = next(iter(provinces))
    ordered = tuple(sorted(local_states, key=_date_key))
    current = ordered[-1]
    local_samples = build_transition_samples(ordered)

    all_xsmn_samples = tuple(
        sample
        for sample in build_transition_samples(tuple(hierarchical_states))
        if _date_key(sample.next_state) <= _date_key(current)
    )
    xsmn_by_province: dict[str, list[TransitionSample]] = defaultdict(list)
    for sample in all_xsmn_samples:
        xsmn_by_province[str(sample.current.province)].append(sample)
    xsmn_samples = tuple(
        sample
        for source_province in sorted(xsmn_by_province)
        for sample in xsmn_by_province[source_province][
            -config.hierarchical_transitions_per_province :
        ]
    )
    global_next = tuple(sample.next_state for sample in xsmn_samples)
    province_next = tuple(sample.next_state for sample in local_samples)
    route_samples = tuple(
        sample for sample in local_samples if _route(sample.current) == _route(current)
    )
    route_next = tuple(sample.next_state for sample in route_samples)

    global_prior = _state_prior(global_next, current)
    province_empirical = _state_prior(province_next, current)
    province_prior = tuple(
        _hierarchical_blend(
            child,
            parent,
            len(province_next),
            config.prior_strength,
            normalized=index < 4 or index == 5,
        )
        for index, (child, parent) in enumerate(zip(province_empirical, global_prior))
    )
    route_empirical = _state_prior(route_next, current)
    route_prior = tuple(
        _hierarchical_blend(
            child,
            parent,
            len(route_next),
            config.prior_strength,
            normalized=index < 4 or index == 5,
        )
        for index, (child, parent) in enumerate(zip(route_empirical, province_prior))
    )
    (
        prior_head,
        prior_unit,
        prior_head_leader,
        prior_unit_leader,
        prior_pair,
        prior_pair_mass,
    ) = route_prior

    weighted_samples: list[tuple[TransitionSample, float, Mapping[str, float]]] = []
    candidate_samples = route_samples or local_samples
    sample_count = len(candidate_samples)
    for index, sample in enumerate(candidate_samples):
        similarity, components = state_similarity(current, sample.current, config)
        age = sample_count - index - 1
        weight = similarity * math.exp(-config.recency_decay * age)
        if weight >= config.minimum_similarity:
            weighted_samples.append((sample, weight, components))
    weighted_samples.sort(
        key=lambda item: (-item[1], -int(_date_key(item[0].current).replace("-", "")[:8]))
    )
    weighted_samples = weighted_samples[: config.top_k_states]

    next_states = tuple(item[0].next_state for item in weighted_samples)
    weights = tuple(item[1] for item in weighted_samples)
    local_weight = sum(weights)
    local_head = _weighted_average(
        [state.head_share for state in next_states], weights, 10, prior_head
    )
    local_unit = _weighted_average(
        [state.unit_share for state in next_states], weights, 10, prior_unit
    )

    leader_head_rows = []
    leader_unit_rows = []
    for state in next_states:
        head = [0.0] * 10
        unit = [0.0] * 10
        dominant_heads = tuple(_dominant_digits(state, "head"))
        dominant_units = tuple(_dominant_digits(state, "unit"))
        for digit in dominant_heads:
            head[int(digit)] = 1.0 / len(dominant_heads)
        for digit in dominant_units:
            unit[int(digit)] = 1.0 / len(dominant_units)
        leader_head_rows.append(head)
        leader_unit_rows.append(unit)
    local_head_leader = _weighted_average(leader_head_rows, weights, 10, prior_head_leader)
    local_unit_leader = _weighted_average(leader_unit_rows, weights, 10, prior_unit_leader)
    local_pair = _weighted_pair_hits(next_states, weights, prior_pair)
    local_pair_mass = _weighted_pair_mass(next_states, weights, prior_pair_mass)

    head_share = _normalized(_blend(local_head, prior_head, local_weight, config.prior_strength), 10)
    unit_share = _normalized(_blend(local_unit, prior_unit, local_weight, config.prior_strength), 10)
    head_leader = _normalized(
        _blend(local_head_leader, prior_head_leader, local_weight, config.prior_strength), 10
    )
    unit_leader = _normalized(
        _blend(local_unit_leader, prior_unit_leader, local_weight, config.prior_strength), 10
    )
    interaction_fraction = local_weight / (
        local_weight + config.interaction_prior_strength
    )
    interaction_lifts: list[float] = []
    digit_pair_hits: list[float] = []
    epsilon = 1e-9
    for pair in range(100):
        head, unit = divmod(pair, 10)
        local_expected = max(local_head[head] * local_unit[unit], epsilon)
        prior_expected = max(prior_head[head] * prior_unit[unit], epsilon)
        local_lift = max(local_pair_mass[pair], epsilon) / local_expected
        prior_lift = max(prior_pair_mass[pair], epsilon) / prior_expected
        lift = math.exp(
            interaction_fraction * math.log(local_lift)
            + (1.0 - interaction_fraction) * math.log(prior_lift)
        )
        predicted_mass = min(1.0, max(0.0, head_share[head] * unit_share[unit] * lift))
        interaction_lifts.append(lift)
        digit_pair_hits.append(1.0 - (1.0 - predicted_mass) ** 18)
    pair_hits = tuple(
        min(1.0, max(0.0, value))
        for value in _blend(
            digit_pair_hits,
            _blend(local_pair, prior_pair, local_weight, config.pair_prior_strength),
            local_weight,
            config.pair_prior_strength,
        )
    )

    squared_weight = sum(weight * weight for weight in weights)
    effective_sample_size = local_weight * local_weight / squared_weight if squared_weight else 0.0
    evidence_fraction = local_weight / (local_weight + config.prior_strength)
    sample_confidence = effective_sample_size / (
        effective_sample_size + config.minimum_effective_samples
    )
    confidence = evidence_fraction * sample_confidence
    route_matched_weight = sum(
        weight for _, weight, components in weighted_samples if components["route"] == 1.0
    )

    return ProvinceTransitionForecast(
        province=province,
        current_draw_date=current.draw_date,
        score_semantics="estimated_hit_likelihood_uncalibrated",
        head_share=head_share,
        unit_share=unit_share,
        head_leader_likelihoods=head_leader,
        unit_leader_likelihoods=unit_leader,
        pair_hit_likelihoods=pair_hits,
        confidence=confidence,
        effective_sample_size=effective_sample_size,
        decomposition=TransitionDecomposition(
            local_head_share=local_head,
            prior_head_share=prior_head,
            local_unit_share=local_unit,
            prior_unit_share=prior_unit,
            local_head_leader_likelihoods=local_head_leader,
            prior_head_leader_likelihoods=prior_head_leader,
            local_unit_leader_likelihoods=local_unit_leader,
            prior_unit_leader_likelihoods=prior_unit_leader,
            local_pair_hit_likelihoods=local_pair,
            prior_pair_hit_likelihoods=prior_pair,
            local_weight=local_weight,
            prior_weight=config.prior_strength,
            local_transition_count=len(weighted_samples),
            prior_transition_count=len(xsmn_samples),
            route_matched_weight=route_matched_weight,
            local_pair_mass=local_pair_mass,
            prior_pair_mass=prior_pair_mass,
            interaction_lifts=tuple(interaction_lifts),
        ),
    )


estimate_province_transition = estimate_transition
