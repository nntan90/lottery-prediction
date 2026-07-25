"""Draw-level empirical-Bayes joint probability estimates for XSMB."""

from __future__ import annotations

import math
from typing import Collection, Iterable, Tuple

from src.xsmb_combo.domain import PAIR_COUNT


class InsufficientHistoryError(ValueError):
    """Raised when joint evidence would be too sparse to report."""


class JointProbabilityEstimator:
    """Estimate co-appearance probabilities with a weak random-draw prior.

    Each historical draw is one observation regardless of how many times a
    tail occurred within that draw. The prior mean reflects the observed draw
    sizes under uniform selection from ``00..99``; the configured strength
    shrinks sparse empirical counts without claiming model calibration.
    """

    def __init__(
        self,
        historical_tail_sets: Iterable[Collection[int]],
        *,
        minimum_history: int = 30,
        prior_strength: float = 12.0,
    ) -> None:
        draws = tuple(self._sanitize_draw(draw) for draw in historical_tail_sets)
        if len(draws) < int(minimum_history):
            raise InsufficientHistoryError(
                f"need at least {minimum_history} draws, received {len(draws)}"
            )
        if prior_strength <= 0.0:
            raise ValueError("prior_strength must be positive")

        self.draws: Tuple[frozenset[int], ...] = draws
        self.prior_strength = float(prior_strength)
        self._pair_prior_mean = sum(
            self._uniform_inclusion_probability(len(draw), order=2)
            for draw in draws
        ) / len(draws)
        self._triple_prior_mean = sum(
            self._uniform_inclusion_probability(len(draw), order=3)
            for draw in draws
        ) / len(draws)
        self._pair_cache: dict[tuple[int, int], float] = {}
        self._triple_cache: dict[tuple[int, int, int], float] = {}

    @staticmethod
    def _sanitize_draw(draw: Collection[int]) -> frozenset[int]:
        values = frozenset(int(pair) for pair in draw)
        if any(pair < 0 or pair >= PAIR_COUNT for pair in values):
            raise ValueError("historical tails must be between 0 and 99")
        return values

    @staticmethod
    def _uniform_inclusion_probability(draw_size: int, *, order: int) -> float:
        if draw_size < order:
            return 0.0
        return math.comb(draw_size, order) / math.comb(PAIR_COUNT, order)

    def _posterior_mean(self, successes: int, prior_mean: float) -> float:
        alpha = prior_mean * self.prior_strength
        beta = (1.0 - prior_mean) * self.prior_strength
        return (successes + alpha) / (len(self.draws) + alpha + beta)

    def pair_probability(self, pair_a: int, pair_b: int) -> float:
        """Posterior mean of both pairs appearing in the same future draw."""
        key = tuple(sorted((int(pair_a), int(pair_b))))
        if key[0] == key[1]:
            raise ValueError("pair_probability requires two distinct pairs")
        if key not in self._pair_cache:
            successes = sum(
                key[0] in draw and key[1] in draw
                for draw in self.draws
            )
            self._pair_cache[key] = self._posterior_mean(
                successes, self._pair_prior_mean
            )
        return self._pair_cache[key]

    def triple_probability(self, pair_a: int, pair_b: int, pair_c: int) -> float:
        """Posterior mean of all three pairs appearing in the same draw."""
        key = tuple(sorted((int(pair_a), int(pair_b), int(pair_c))))
        if len(set(key)) != 3:
            raise ValueError("triple_probability requires three distinct pairs")
        if key not in self._triple_cache:
            successes = sum(all(pair in draw for pair in key) for draw in self.draws)
            self._triple_cache[key] = self._posterior_mean(
                successes, self._triple_prior_mean
            )
        return self._triple_cache[key]
