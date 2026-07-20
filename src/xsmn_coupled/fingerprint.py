"""Prize-balanced relational fingerprints for coupled province anchors."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Mapping

from .domain import DrawSnapshot, PRIZE_CODES


@dataclass(frozen=True)
class CoupledFingerprint:
    province_pair: tuple[str, str]
    target_weekday: int
    anchor_ages: tuple[int, int]
    blocks: Mapping[str, tuple[float, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocks", MappingProxyType(dict(self.blocks)))


@dataclass(frozen=True)
class SimilarityBreakdown:
    score: float
    relation_score: float
    age_score: float
    prize_scores: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "prize_scores", MappingProxyType(dict(self.prize_scores)))


def _relation_block(values_a: tuple[int, ...], values_b: tuple[int, ...]) -> tuple[float, ...]:
    pair_count = len(values_a) * len(values_b)
    if pair_count == 0:
        raise ValueError("prize relation block cannot be empty")

    delta_units = [0.0] * 10
    delta_tens = [0.0] * 10
    exact = same_head = same_tail = reversal = complement = 0.0

    for value_a in values_a:
        for value_b in values_b:
            delta_units[(value_b % 10 - value_a % 10) % 10] += 1.0
            delta_tens[(value_b // 10 - value_a // 10) % 10] += 1.0
            exact += float(value_a == value_b)
            same_head += float(value_a // 10 == value_b // 10)
            same_tail += float(value_a % 10 == value_b % 10)
            reversal += float(value_b == (value_a % 10) * 10 + value_a // 10)
            complement += float(value_a + value_b == 99)

    scale = float(pair_count)
    return tuple(
        [value / scale for value in delta_units]
        + [value / scale for value in delta_tens]
        + [exact / scale, same_head / scale, same_tail / scale, reversal / scale, complement / scale]
    )


def build_fingerprint(
    anchor_a: DrawSnapshot,
    anchor_b: DrawSnapshot,
    target_date: date,
) -> CoupledFingerprint:
    """Build a permutation-invariant fingerprint from two pre-target anchors."""
    if anchor_a.draw_date >= target_date or anchor_b.draw_date >= target_date:
        raise ValueError("anchors must be strictly before target_date")
    if not anchor_a.is_complete or not anchor_b.is_complete:
        raise ValueError("anchors must contain all XSMN prize rows")

    blocks = {
        code: _relation_block(anchor_a.prizes[code], anchor_b.prizes[code])
        for code in PRIZE_CODES
    }
    return CoupledFingerprint(
        province_pair=(anchor_a.province, anchor_b.province),
        target_weekday=target_date.weekday(),
        anchor_ages=(
            (target_date - anchor_a.draw_date).days,
            (target_date - anchor_b.draw_date).days,
        ),
        blocks=blocks,
    )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


def coupled_similarity(
    current: CoupledFingerprint,
    historical: CoupledFingerprint,
    context_weight: float = 0.10,
) -> SimilarityBreakdown:
    """Compare fingerprints with equal total weight for every prize block."""
    if not math.isfinite(context_weight) or not 0.0 <= context_weight < 1.0:
        raise ValueError("context_weight must be finite and in [0, 1)")
    if current.province_pair != historical.province_pair:
        raise ValueError("fingerprints must use the same ordered province pair")
    if current.target_weekday != historical.target_weekday:
        return SimilarityBreakdown(0.0, 0.0, 0.0, {})

    prize_scores = {
        code: _cosine(current.blocks[code], historical.blocks[code])
        for code in PRIZE_CODES
    }
    relation_score = sum(prize_scores.values()) / len(PRIZE_CODES)
    age_distance = sum(
        abs(current_age - historical_age)
        for current_age, historical_age in zip(current.anchor_ages, historical.anchor_ages)
    ) / 2.0
    age_score = 1.0 / (1.0 + age_distance)
    score = (1.0 - context_weight) * relation_score + context_weight * age_score
    return SimilarityBreakdown(
        score=score,
        relation_score=relation_score,
        age_score=age_score,
        prize_scores=prize_scores,
    )
