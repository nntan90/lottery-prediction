"""Deterministic constrained Top-3 selector for merged PDA/DDT forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math
from typing import Mapping, Protocol, Sequence, Tuple


class MergedForecastLike(Protocol):
    unit_share: Tuple[float, ...]
    pair_hit_likelihoods: Tuple[float, ...]


@dataclass(frozen=True)
class TopThreeSelection:
    status: str
    selected_pairs: Tuple[int, ...]
    total_utility: float
    configuration: str | None
    top_unit_digits: Tuple[int, ...]
    candidate_count: int
    reason: str | None = None


def _pair_scores(values: Sequence[float] | Mapping[int, float]) -> dict[int, float]:
    items = values.items() if isinstance(values, Mapping) else enumerate(values)
    scores: dict[int, float] = {}
    for raw_pair, raw_score in items:
        pair = int(raw_pair)
        score = float(raw_score)
        if not 0 <= pair <= 99:
            raise ValueError("pair keys must be between 0 and 99")
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("pair likelihoods must be finite and in [0, 1]")
        scores[pair] = score
    return scores


def _unit_distribution(values: Sequence[float]) -> Tuple[float, ...]:
    if len(values) != 10:
        raise ValueError("unit_share must contain 10 values")
    result = tuple(float(value) for value in values)
    if any(not math.isfinite(value) or value < 0.0 for value in result):
        raise ValueError("unit_share values must be finite and non-negative")
    if not math.isclose(sum(result), 1.0, abs_tol=1e-9):
        raise ValueError("unit_share must sum to 1")
    return result


def select_top_three(
    pair_likelihoods: Sequence[float] | Mapping[int, float],
    unit_share: Sequence[float],
    *,
    top_unit_count: int = 3,
    candidates_per_unit: int = 10,
    tiebreak_likelihoods: Sequence[float] | Mapping[int, float] | None = None,
) -> TopThreeSelection:
    """Maximize pair utility under the agreed unit-digit portfolio rules.

    Unit shares define only the three eligible suffixes. Pair likelihoods are
    not multiplied by unit shares again, because that evidence is already
    represented in the province forecasts and merged pair probabilities.
    """
    scores = _pair_scores(pair_likelihoods)
    tiebreakers = _pair_scores(tiebreak_likelihoods or pair_likelihoods)
    if set(tiebreakers) != set(scores):
        raise ValueError("tiebreak likelihoods must cover the same pair keys")
    shares = _unit_distribution(unit_share)
    if not 1 <= top_unit_count <= 10:
        raise ValueError("top_unit_count must be between 1 and 10")
    if not 1 <= candidates_per_unit <= 10:
        raise ValueError("candidates_per_unit must be between 1 and 10")
    top_units = tuple(
        sorted(range(10), key=lambda digit: (-shares[digit], digit))[:top_unit_count]
    )
    top_unit = top_units[0]
    candidates = tuple(
        sorted(
            pair
            for digit in top_units
            for pair in sorted(
                (candidate for candidate in scores if candidate % 10 == digit),
                key=lambda candidate: (
                    -scores[candidate],
                    -tiebreakers[candidate],
                    candidate,
                ),
            )[:candidates_per_unit]
        )
    )

    feasible: list[tuple[tuple[int, int, int], float, str]] = []
    for combo in combinations(candidates, 3):
        unit_counts: dict[int, int] = {}
        for pair in combo:
            unit_counts[pair % 10] = unit_counts.get(pair % 10, 0) + 1
        if top_unit not in unit_counts or max(unit_counts.values()) > 2:
            continue
        configuration = "2+1" if sorted(unit_counts.values(), reverse=True) == [2, 1] else "1+1+1"
        feasible.append((combo, sum(scores[pair] for pair in combo), configuration))

    if not feasible:
        return TopThreeSelection(
            status="insufficient",
            selected_pairs=(),
            total_utility=0.0,
            configuration=None,
            top_unit_digits=top_units,
            candidate_count=len(candidates),
            reason="no_feasible_top_three_under_unit_constraints",
        )

    combo, utility, configuration = max(
        feasible,
        key=lambda item: (
            item[1],
            sum(tiebreakers[pair] for pair in item[0]),
            tuple(-pair for pair in item[0]),
        ),
    )
    ranked = tuple(
        sorted(
            combo,
            key=lambda pair: (-scores[pair], -tiebreakers[pair], pair),
        )
    )
    return TopThreeSelection(
        status="success",
        selected_pairs=ranked,
        total_utility=utility,
        configuration=configuration,
        top_unit_digits=top_units,
        candidate_count=len(candidates),
    )


def select_from_merged_forecast(
    forecast: MergedForecastLike,
    *,
    top_unit_count: int = 3,
    candidates_per_unit: int = 10,
) -> TopThreeSelection:
    return select_top_three(
        forecast.pair_hit_likelihoods,
        forecast.unit_share,
        top_unit_count=top_unit_count,
        candidates_per_unit=candidates_per_unit,
        tiebreak_likelihoods=getattr(
            forecast,
            "ranking_tiebreak_likelihoods",
            forecast.pair_hit_likelihoods,
        ),
    )
