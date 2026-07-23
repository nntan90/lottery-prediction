"""Pure two-province probability merge for PDA/DDT shadow forecasts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import FrozenSet, Iterable, Protocol, Sequence, Tuple


class ProvinceForecastLike(Protocol):
    province: str
    unit_share: Tuple[float, ...]
    pair_hit_likelihoods: Tuple[float, ...]
    score_semantics: str


@dataclass(frozen=True)
class CoupledDrawObservation:
    """Same scheduled draw evidence for one fixed province pair."""

    pairs_a: FrozenSet[int]
    pairs_b: FrozenSet[int]

    def __post_init__(self) -> None:
        if any(not 0 <= int(pair) <= 99 for pair in self.pairs_a | self.pairs_b):
            raise ValueError("historical pairs must be between 0 and 99")


@dataclass(frozen=True)
class CouplingMergeConfig:
    """Hierarchical support shrinkage and numerical stability settings."""

    exact_pair_shrinkage: float = 8.0
    suffix_shrinkage: float = 16.0
    province_pair_shrinkage: float = 24.0
    beta_pseudocount: float = 0.5
    minimum_lift: float = 0.10
    maximum_lift: float = 10.0

    def __post_init__(self) -> None:
        numeric = (
            self.exact_pair_shrinkage,
            self.suffix_shrinkage,
            self.province_pair_shrinkage,
            self.beta_pseudocount,
            self.minimum_lift,
            self.maximum_lift,
        )
        if any(not math.isfinite(value) or value <= 0.0 for value in numeric):
            raise ValueError("merge configuration values must be finite and positive")
        if self.minimum_lift > self.maximum_lift:
            raise ValueError("minimum_lift cannot exceed maximum_lift")


@dataclass(frozen=True)
class PairMergeDecomposition:
    pair: int
    likelihood_a: float
    likelihood_b: float
    xsmn_lift: float
    global_lift: float
    suffix_lift: float
    exact_pair_lift: float
    coupling_lift: float
    suffix_support: int
    exact_pair_support: int
    frechet_lower: float
    frechet_upper: float
    joint_likelihood: float
    union_likelihood: float


@dataclass(frozen=True)
class MergedProvinceForecast:
    """Merged forecast; pair likelihoods already include province union logic."""

    provinces: Tuple[str, str]
    score_semantics: str
    unit_share: Tuple[float, ...]
    pair_union_likelihoods: Tuple[float, ...]
    pair_joint_likelihoods: Tuple[float, ...]
    coupling_lifts: Tuple[float, ...]
    decomposition: Tuple[PairMergeDecomposition, ...]
    ranking_tiebreak_likelihoods: Tuple[float, ...]

    @property
    def pair_hit_likelihoods(self) -> Tuple[float, ...]:
        """Selector-facing alias; it must not be unit-weighted again."""
        return self.pair_union_likelihoods


def _validate_distribution(values: Sequence[float], size: int, name: str) -> Tuple[float, ...]:
    if len(values) != size:
        raise ValueError(f"{name} must contain {size} values")
    result = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in result):
        raise ValueError(f"{name} values must be finite and in [0, 1]")
    return result


def _smoothed_lift(count_a: int, count_b: int, count_joint: int, trials: int, pseudo: float) -> float:
    denominator = trials + 2.0 * pseudo
    rate_a = (count_a + pseudo) / denominator
    rate_b = (count_b + pseudo) / denominator
    independent_rate = rate_a * rate_b
    rate_joint = (count_joint + 2.0 * pseudo * independent_rate) / denominator
    return rate_joint / max(independent_rate, 1e-15)


def _log_shrink(local_lift: float, parent_lift: float, support: int, strength: float) -> float:
    local_fraction = support / (support + strength)
    return math.exp(
        local_fraction * math.log(max(local_lift, 1e-15))
        + (1.0 - local_fraction) * math.log(max(parent_lift, 1e-15))
    )


def _history_counts(
    history: tuple[CoupledDrawObservation, ...],
) -> tuple[list[int], list[int], list[int], list[int], list[int], list[int]]:
    pair_a = [0] * 100
    pair_b = [0] * 100
    pair_joint = [0] * 100
    suffix_a = [0] * 10
    suffix_b = [0] * 10
    suffix_joint = [0] * 10
    for observation in history:
        present_a = {int(pair) for pair in observation.pairs_a}
        present_b = {int(pair) for pair in observation.pairs_b}
        units_a = {pair % 10 for pair in present_a}
        units_b = {pair % 10 for pair in present_b}
        for pair in present_a:
            pair_a[pair] += 1
        for pair in present_b:
            pair_b[pair] += 1
        for pair in present_a & present_b:
            pair_joint[pair] += 1
        for digit in units_a:
            suffix_a[digit] += 1
        for digit in units_b:
            suffix_b[digit] += 1
        for digit in units_a & units_b:
            suffix_joint[digit] += 1
    return pair_a, pair_b, pair_joint, suffix_a, suffix_b, suffix_joint


def _global_lift(
    pair_a: Sequence[int],
    pair_b: Sequence[int],
    pair_joint: Sequence[int],
    trials: int,
    pseudo: float,
) -> float:
    if trials == 0:
        return 1.0
    denominator = trials + 2.0 * pseudo
    expected_rates = [
        ((left + pseudo) / denominator) * ((right + pseudo) / denominator)
        for left, right in zip(pair_a, pair_b)
    ]
    observed = sum(
        (joint + 2.0 * pseudo * expected) / denominator
        for joint, expected in zip(pair_joint, expected_rates)
    ) / 100.0
    expected = sum(expected_rates) / 100.0
    return observed / max(expected, 1e-15)


def merge_province_forecasts(
    forecast_a: ProvinceForecastLike,
    forecast_b: ProvinceForecastLike,
    coupling_history: Iterable[CoupledDrawObservation] = (),
    config: CouplingMergeConfig | None = None,
    *,
    regional_coupling_history: Iterable[CoupledDrawObservation] = (),
) -> MergedProvinceForecast:
    """Merge independently estimated provinces with hierarchical coupling lift."""
    config = config or CouplingMergeConfig()
    if str(forecast_a.province) == str(forecast_b.province):
        raise ValueError("merge requires two distinct independently estimated provinces")
    unit_a = _validate_distribution(forecast_a.unit_share, 10, "forecast_a.unit_share")
    unit_b = _validate_distribution(forecast_b.unit_share, 10, "forecast_b.unit_share")
    if not math.isclose(sum(unit_a), 1.0, abs_tol=1e-9) or not math.isclose(sum(unit_b), 1.0, abs_tol=1e-9):
        raise ValueError("each province unit_share must sum to 1")
    likelihood_a = _validate_distribution(
        forecast_a.pair_hit_likelihoods, 100, "forecast_a.pair_hit_likelihoods"
    )
    likelihood_b = _validate_distribution(
        forecast_b.pair_hit_likelihoods, 100, "forecast_b.pair_hit_likelihoods"
    )
    history = tuple(coupling_history)
    regional_history = tuple(regional_coupling_history)
    counts = _history_counts(history)
    pair_a, pair_b, pair_joint, suffix_a, suffix_b, suffix_joint = counts
    raw_global_lift = _global_lift(
        pair_a, pair_b, pair_joint, len(history), config.beta_pseudocount
    )
    regional_counts = _history_counts(regional_history)
    xsmn_lift = _global_lift(
        regional_counts[0],
        regional_counts[1],
        regional_counts[2],
        len(regional_history),
        config.beta_pseudocount,
    )
    global_lift = _log_shrink(
        raw_global_lift,
        xsmn_lift,
        len(history),
        config.province_pair_shrinkage,
    )

    suffix_lifts: list[float] = []
    suffix_supports: list[int] = []
    for digit in range(10):
        if history:
            raw = _smoothed_lift(
                suffix_a[digit],
                suffix_b[digit],
                suffix_joint[digit],
                len(history),
                config.beta_pseudocount,
            )
            support = min(suffix_a[digit], suffix_b[digit])
            suffix_lift = _log_shrink(raw, global_lift, support, config.suffix_shrinkage)
        else:
            support = 0
            suffix_lift = 1.0
        suffix_lifts.append(suffix_lift)
        suffix_supports.append(support)

    unit_share_values = [(left + right) / 2.0 for left, right in zip(unit_a, unit_b)]
    unit_share_values[-1] += 1.0 - sum(unit_share_values)
    unit_share = tuple(unit_share_values)
    unions: list[float] = []
    joints: list[float] = []
    lifts: list[float] = []
    decomposition: list[PairMergeDecomposition] = []
    for pair in range(100):
        if history:
            raw_exact_lift = _smoothed_lift(
                pair_a[pair],
                pair_b[pair],
                pair_joint[pair],
                len(history),
                config.beta_pseudocount,
            )
            exact_support = min(pair_a[pair], pair_b[pair])
            exact_lift = _log_shrink(
                raw_exact_lift,
                suffix_lifts[pair % 10],
                exact_support,
                config.exact_pair_shrinkage,
            )
            coupling_lift = min(config.maximum_lift, max(config.minimum_lift, exact_lift))
        else:
            exact_support = 0
            exact_lift = 1.0
            coupling_lift = 1.0

        q_a, q_b = likelihood_a[pair], likelihood_b[pair]
        lower = max(0.0, q_a + q_b - 1.0)
        upper = min(q_a, q_b)
        independent_adjusted = q_a * q_b * coupling_lift
        joint = min(upper, max(lower, independent_adjusted))
        union = min(1.0, max(0.0, q_a + q_b - joint))
        unions.append(union)
        joints.append(joint)
        lifts.append(coupling_lift)
        decomposition.append(
            PairMergeDecomposition(
                pair=pair,
                likelihood_a=q_a,
                likelihood_b=q_b,
                xsmn_lift=xsmn_lift,
                global_lift=global_lift,
                suffix_lift=suffix_lifts[pair % 10],
                exact_pair_lift=exact_lift,
                coupling_lift=coupling_lift,
                suffix_support=suffix_supports[pair % 10],
                exact_pair_support=exact_support,
                frechet_lower=lower,
                frechet_upper=upper,
                joint_likelihood=joint,
                union_likelihood=union,
            )
        )

    return MergedProvinceForecast(
        provinces=(str(forecast_a.province), str(forecast_b.province)),
        # The nonlinear coupling layer requires its own OOF calibrator. Province
        # calibration alone is insufficient to call this union a probability.
        score_semantics="merged_pair_hit_likelihood_uncalibrated",
        unit_share=unit_share,
        pair_union_likelihoods=tuple(unions),
        pair_joint_likelihoods=tuple(joints),
        coupling_lifts=tuple(lifts),
        decomposition=tuple(decomposition),
        ranking_tiebreak_likelihoods=tuple(unions),
    )


merge_forecasts = merge_province_forecasts
