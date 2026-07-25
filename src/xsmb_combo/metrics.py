"""Canonical metrics for XSMB three-pair combo evaluation."""

from __future__ import annotations

import math
from typing import Collection, Iterable, Tuple

from src.xsmb_combo.domain import ComboEvaluation, PAIR_COUNT


DEFAULT_COMBO_COST = 328_000
DEFAULT_REVENUE_PER_CIRCLE = 1_100_000


def _unique_valid_pairs(values: Iterable[int]) -> Tuple[int, ...]:
    pairs: list[int] = []
    for value in values:
        pair = int(value)
        if pair < 0 or pair >= PAIR_COUNT:
            raise ValueError(f"pair must be between 0 and 99: {pair}")
        if pair not in pairs:
            pairs.append(pair)
    return tuple(pairs)


def evaluate_combo(
    predicted_pairs: Iterable[int],
    actual_tails: Collection[int],
    *,
    cost: int = DEFAULT_COMBO_COST,
    revenue_per_circle: int = DEFAULT_REVENUE_PER_CIRCLE,
) -> ComboEvaluation:
    """Evaluate a three-pair selection using the production ``>=2/3`` rule."""
    pairs = _unique_valid_pairs(predicted_pairs)
    if len(pairs) != 3:
        raise ValueError("predicted_pairs must contain exactly three unique pairs")

    actual = frozenset(_unique_valid_pairs(actual_tails))
    matched = tuple(pair for pair in pairs if pair in actual)
    hit_count = len(matched)
    winning_circles = math.comb(hit_count, 2) if hit_count >= 2 else 0
    revenue = winning_circles * int(revenue_per_circle)

    return ComboEvaluation(
        predicted_pairs=(pairs[0], pairs[1], pairs[2]),
        actual_tails=actual,
        matched_pairs=matched,
        hit_count=hit_count,
        combo_hit=hit_count >= 2,
        winning_circles=winning_circles,
        cost=int(cost),
        revenue=revenue,
        profit=revenue - int(cost),
    )


def random_combo_hit_probability(
    tail_count: int,
    *,
    picks: int = 3,
    minimum_hits: int = 2,
) -> float:
    """Hypergeometric probability that random unique picks hit the combo KPI."""
    tail_count = max(0, min(int(tail_count), PAIR_COUNT))
    picks = max(0, min(int(picks), PAIR_COUNT))
    minimum_hits = max(0, int(minimum_hits))
    if minimum_hits == 0:
        return 1.0
    if picks == 0 or tail_count == 0 or minimum_hits > min(picks, tail_count):
        return 0.0

    denominator = math.comb(PAIR_COUNT, picks)
    probability = 0.0
    for hits in range(minimum_hits, min(picks, tail_count) + 1):
        misses = picks - hits
        if misses <= PAIR_COUNT - tail_count:
            probability += (
                math.comb(tail_count, hits)
                * math.comb(PAIR_COUNT - tail_count, misses)
            ) / denominator
    return probability


def random_expected_winning_circles(
    tail_count: int,
    *,
    picks: int = 3,
) -> float:
    """Expected number of winning xiên-2 circles for random unique picks."""
    tail_count = max(0, min(int(tail_count), PAIR_COUNT))
    picks = max(0, min(int(picks), PAIR_COUNT))
    if tail_count < 2 or picks < 2:
        return 0.0
    return (
        math.comb(picks, 2)
        * math.comb(tail_count, 2)
        / math.comb(PAIR_COUNT, 2)
    )


def combo_probability_from_joint(
    pair_probabilities: Iterable[float],
    triple_probability: float,
) -> float:
    """Return ``P(at least two)`` from three pairwise and one triple event."""
    pair_probs = tuple(float(value) for value in pair_probabilities)
    if len(pair_probs) != 3:
        raise ValueError("exactly three pair probabilities are required")
    bounded_pairs = tuple(min(max(value, 0.0), 1.0) for value in pair_probs)
    bounded_triple = min(
        max(float(triple_probability), 0.0),
        min(bounded_pairs),
    )
    return min(max(sum(bounded_pairs) - 2.0 * bounded_triple, 0.0), 1.0)
