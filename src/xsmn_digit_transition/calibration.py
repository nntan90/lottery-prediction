"""Leakage-safe reliability calibration for uncalibrated PDA/DDT likelihoods."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CalibrationObservation:
    """One resolved forecast outcome available at ``observed_at``."""

    observed_at: object
    likelihood: float
    outcome: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.likelihood) or not 0.0 <= self.likelihood <= 1.0:
            raise ValueError("likelihood must be finite and in [0, 1]")
        if self.outcome not in (0, 1, False, True):
            raise ValueError("outcome must be binary")


@dataclass(frozen=True)
class ReliabilityCalibrationConfig:
    """Configuration for deterministic expanding walk-forward calibration."""

    bins: int = 10
    minimum_training_samples: int = 20
    minimum_folds: int = 10
    bin_prior_strength: float = 5.0

    def __post_init__(self) -> None:
        if self.bins < 2:
            raise ValueError("bins must be at least 2")
        if self.minimum_training_samples < 1:
            raise ValueError("minimum_training_samples must be positive")
        if self.minimum_folds < 1:
            raise ValueError("minimum_folds must be positive")
        if not math.isfinite(self.bin_prior_strength) or self.bin_prior_strength <= 0.0:
            raise ValueError("bin_prior_strength must be finite and positive")


@dataclass(frozen=True)
class CalibrationFold:
    """Audit record proving each prediction used only earlier outcomes."""

    observed_at: object
    training_size: int
    raw_likelihood: float
    calibrated_probability: float
    outcome: int
    bin_index: int
    bin_support: int


@dataclass(frozen=True)
class BrierDiagnostics:
    raw_brier: Optional[float]
    calibrated_brier: Optional[float]
    improvement: Optional[float]


@dataclass(frozen=True)
class WalkForwardCalibrationResult:
    """Calibration output with an explicit promotion gate."""

    status: str
    cutoff: object | None
    observations_used: int
    fold_count: int
    raw_likelihoods: Tuple[float, ...]
    uncalibrated_likelihoods: Tuple[float, ...]
    calibrated_probabilities: Optional[Tuple[float, ...]]
    outcomes: Tuple[int, ...]
    folds: Tuple[CalibrationFold, ...]
    brier: BrierDiagnostics


@dataclass(frozen=True)
class ReliabilityModel:
    """A deterministic reliability map fitted only from OOF observations."""

    status: str
    bin_probabilities: Tuple[float, ...]
    bin_support: Tuple[int, ...]
    observation_count: int
    draw_count: int
    validation_draw_count: int
    raw_brier: Optional[float]
    calibrated_brier: Optional[float]

    def apply(self, likelihoods: Sequence[float]) -> Tuple[float, ...]:
        """Map current uncalibrated likelihoods without refitting the model."""
        values = tuple(float(value) for value in likelihoods)
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in values):
            raise ValueError("likelihoods must be finite and in [0, 1]")
        if self.status != "calibrated":
            return values
        bins = len(self.bin_probabilities)
        calibrated: list[float] = []
        for value in values:
            position = value * bins - 0.5
            if position <= 0.0:
                calibrated.append(self.bin_probabilities[0])
                continue
            if position >= bins - 1:
                calibrated.append(self.bin_probabilities[-1])
                continue
            lower = int(math.floor(position))
            fraction = position - lower
            calibrated.append(
                self.bin_probabilities[lower] * (1.0 - fraction)
                + self.bin_probabilities[lower + 1] * fraction
            )
        return tuple(calibrated)


def _time_key(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _bin_index(likelihood: float, bins: int) -> int:
    return min(int(likelihood * bins), bins - 1)


def _fit_bin(
    training: tuple[CalibrationObservation, ...],
    likelihood: float,
    config: ReliabilityCalibrationConfig,
) -> tuple[float, int, int]:
    index = _bin_index(likelihood, config.bins)
    in_bin = tuple(
        observation
        for observation in training
        if _bin_index(observation.likelihood, config.bins) == index
    )
    global_rate = sum(int(item.outcome) for item in training) / len(training)
    posterior = (
        sum(int(item.outcome) for item in in_bin)
        + config.bin_prior_strength * global_rate
    ) / (len(in_bin) + config.bin_prior_strength)
    return posterior, index, len(in_bin)


def _brier(values: tuple[float, ...], outcomes: tuple[int, ...]) -> Optional[float]:
    if not values:
        return None
    return sum((value - outcome) ** 2 for value, outcome in zip(values, outcomes)) / len(values)


def _isotonic_probabilities(
    values: Sequence[float], weights: Sequence[float]
) -> Tuple[float, ...]:
    """Pool adjacent violators so reliability cannot invert score ordering."""
    blocks: list[list[float]] = []
    for index, (value, weight) in enumerate(zip(values, weights)):
        blocks.append([float(index), float(index), float(value) * weight, float(weight)])
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            if left[2] / left[3] <= right[2] / right[3]:
                break
            blocks[-2:] = [[left[0], right[1], left[2] + right[2], left[3] + right[3]]]
    result = [0.0] * len(values)
    for start, end, weighted_sum, weight in blocks:
        mean = weighted_sum / weight
        for index in range(int(start), int(end) + 1):
            result[index] = mean
    return tuple(result)


def expanding_walk_forward_calibrate(
    observations: Iterable[CalibrationObservation],
    cutoff: object | None = None,
    config: ReliabilityCalibrationConfig | None = None,
) -> WalkForwardCalibrationResult:
    """Calibrate every eligible point using only observations before it.

    ``cutoff`` is strict: observations at or after it are not fitted or scored.
    Until ``minimum_folds`` is met, outputs remain explicitly named likelihoods
    and no field claims calibrated probabilities.
    """
    config = config or ReliabilityCalibrationConfig()
    indexed = tuple(enumerate(observations))
    ordered = tuple(
        observation
        for _, observation in sorted(indexed, key=lambda item: (_time_key(item[1].observed_at), item[0]))
        if cutoff is None or _time_key(observation.observed_at) < _time_key(cutoff)
    )

    folds: list[CalibrationFold] = []
    for validation in ordered:
        validation_key = _time_key(validation.observed_at)
        training = tuple(
            candidate
            for candidate in ordered
            if _time_key(candidate.observed_at) < validation_key
        )
        if len(training) < config.minimum_training_samples:
            continue
        calibrated, bin_index, bin_support = _fit_bin(training, validation.likelihood, config)
        folds.append(
            CalibrationFold(
                observed_at=validation.observed_at,
                training_size=len(training),
                raw_likelihood=validation.likelihood,
                calibrated_probability=calibrated,
                outcome=int(validation.outcome),
                bin_index=bin_index,
                bin_support=bin_support,
            )
        )

    raw = tuple(fold.raw_likelihood for fold in folds)
    calibrated = tuple(fold.calibrated_probability for fold in folds)
    outcomes = tuple(fold.outcome for fold in folds)
    raw_brier = _brier(raw, outcomes)
    calibrated_brier = _brier(calibrated, outcomes)
    status = "calibrated" if len(folds) >= config.minimum_folds else "uncalibrated"
    published_probabilities = calibrated if status == "calibrated" else None
    retained_likelihoods = () if status == "calibrated" else raw

    return WalkForwardCalibrationResult(
        status=status,
        cutoff=cutoff,
        observations_used=len(ordered),
        fold_count=len(folds),
        raw_likelihoods=raw,
        uncalibrated_likelihoods=retained_likelihoods,
        calibrated_probabilities=published_probabilities,
        outcomes=outcomes,
        folds=tuple(folds),
        brier=BrierDiagnostics(
            raw_brier=raw_brier,
            calibrated_brier=calibrated_brier if status == "calibrated" else None,
            improvement=(raw_brier - calibrated_brier)
            if status == "calibrated" and raw_brier is not None and calibrated_brier is not None
            else None,
        ),
    )


walk_forward_calibrate = expanding_walk_forward_calibrate


def fit_reliability_model(
    observations: Iterable[CalibrationObservation],
    *,
    cutoff: object | None = None,
    bins: int = 10,
    minimum_draws: int = 20,
    prior_strength: float = 5.0,
) -> ReliabilityModel:
    """Fit a deployable bin calibrator from already out-of-fold predictions.

    The caller is responsible for creating each raw likelihood without using
    that observation's outcome. This function then fits one final reliability
    map using only resolved observations strictly before ``cutoff``.
    """
    if bins < 2 or minimum_draws < 1:
        raise ValueError("bins must be >= 2 and minimum_draws must be positive")
    if not math.isfinite(prior_strength) or prior_strength <= 0.0:
        raise ValueError("prior_strength must be finite and positive")

    ordered = tuple(
        observation
        for _, observation in sorted(
            enumerate(observations),
            key=lambda item: (_time_key(item[1].observed_at), item[0]),
        )
        if cutoff is None or _time_key(observation.observed_at) < _time_key(cutoff)
    )
    draw_count = len({_time_key(observation.observed_at) for observation in ordered})
    if not ordered:
        return ReliabilityModel(
            status="uncalibrated",
            bin_probabilities=tuple((index + 0.5) / bins for index in range(bins)),
            bin_support=(0,) * bins,
            observation_count=0,
            draw_count=0,
            validation_draw_count=0,
            raw_brier=None,
            calibrated_brier=None,
        )

    unique_dates = sorted({_time_key(observation.observed_at) for observation in ordered})
    split_index = max(1, min(len(unique_dates) - 1, int(len(unique_dates) * 0.7)))
    training_dates = set(unique_dates[:split_index])
    validation_dates = set(unique_dates[split_index:])

    def fit_probabilities(
        source: tuple[CalibrationObservation, ...],
    ) -> tuple[Tuple[float, ...], Tuple[int, ...]]:
        global_rate = sum(int(observation.outcome) for observation in source) / len(source)
        successes = [0] * bins
        support = [0] * bins
        for observation in source:
            index = _bin_index(observation.likelihood, bins)
            support[index] += 1
            successes[index] += int(observation.outcome)
        raw_probabilities = tuple(
            (successes[index] + prior_strength * global_rate)
            / (support[index] + prior_strength)
            for index in range(bins)
        )
        probabilities = _isotonic_probabilities(
            raw_probabilities,
            tuple(value + prior_strength for value in support),
        )
        return probabilities, tuple(support)

    training = tuple(
        observation
        for observation in ordered
        if _time_key(observation.observed_at) in training_dates
    )
    validation = tuple(
        observation
        for observation in ordered
        if _time_key(observation.observed_at) in validation_dates
    )
    validation_probabilities, _ = fit_probabilities(training)
    raw = tuple(observation.likelihood for observation in validation)
    outcomes = tuple(int(observation.outcome) for observation in validation)
    validation_model = ReliabilityModel(
        status="calibrated",
        bin_probabilities=validation_probabilities,
        bin_support=(0,) * bins,
        observation_count=0,
        draw_count=0,
        validation_draw_count=0,
        raw_brier=None,
        calibrated_brier=None,
    )
    calibrated = validation_model.apply(raw)
    probabilities, support = fit_probabilities(ordered)
    return ReliabilityModel(
        status=(
            "calibrated"
            if draw_count >= minimum_draws and bool(training) and bool(validation)
            else "uncalibrated"
        ),
        bin_probabilities=probabilities,
        bin_support=support,
        observation_count=len(ordered),
        draw_count=draw_count,
        validation_draw_count=len(validation_dates),
        raw_brier=_brier(raw, outcomes),
        calibrated_brier=_brier(calibrated, outcomes),
    )
