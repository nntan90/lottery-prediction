"""Deterministic draw-state construction for provincial digit transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable, Mapping, Optional, Sequence

from .domain import DrawSnapshot, TAILS_PER_DRAW


_WEEKDAY_LABELS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class DigitDistribution:
    """Observed 0-9 histogram and concentration statistics for one axis."""

    counts: tuple[int, ...]
    shares: tuple[float, ...]
    coverage: tuple[bool, ...]
    dominant_digits: tuple[int, ...]
    unique_dominant_digit: Optional[int]
    max_count: int
    margin: int
    normalized_entropy: float

    @property
    def is_tied(self) -> bool:
        return len(self.dominant_digits) > 1


@dataclass(frozen=True)
class DrawDigitState:
    """PDA/DDT state for one complete draw and its following route."""

    province: str
    draw_date: date
    next_date: date
    route_label: str
    head: DigitDistribution
    unit: DigitDistribution
    exact_pair_counts: tuple[int, ...]

    @property
    def next_draw_date(self) -> date:
        return self.next_date

    @property
    def head_counts(self) -> tuple[int, ...]:
        return self.head.counts

    @property
    def unit_counts(self) -> tuple[int, ...]:
        return self.unit.counts

    @property
    def head_shares(self) -> tuple[float, ...]:
        return self.head.shares

    @property
    def head_share(self) -> tuple[float, ...]:
        return self.head.shares

    @property
    def unit_shares(self) -> tuple[float, ...]:
        return self.unit.shares

    @property
    def unit_share(self) -> tuple[float, ...]:
        return self.unit.shares

    @property
    def head_coverage(self) -> tuple[bool, ...]:
        return self.head.coverage

    @property
    def unit_coverage(self) -> tuple[bool, ...]:
        return self.unit.coverage

    @property
    def head_dominant_digits(self) -> tuple[int, ...]:
        return self.head.dominant_digits

    @property
    def unit_dominant_digits(self) -> tuple[int, ...]:
        return self.unit.dominant_digits

    @property
    def head_unique_dominant_digit(self) -> Optional[int]:
        return self.head.unique_dominant_digit

    @property
    def unit_unique_dominant_digit(self) -> Optional[int]:
        return self.unit.unique_dominant_digit

    @property
    def head_max_count(self) -> int:
        return self.head.max_count

    @property
    def unit_max_count(self) -> int:
        return self.unit.max_count

    @property
    def head_margin(self) -> int:
        return self.head.margin

    @property
    def unit_margin(self) -> int:
        return self.unit.margin

    @property
    def head_normalized_entropy(self) -> float:
        return self.head.normalized_entropy

    @property
    def head_entropy(self) -> float:
        return self.head.normalized_entropy

    @property
    def unit_normalized_entropy(self) -> float:
        return self.unit.normalized_entropy

    @property
    def unit_entropy(self) -> float:
        return self.unit.normalized_entropy

    @property
    def head_is_tied(self) -> bool:
        return self.head.is_tied

    @property
    def unit_is_tied(self) -> bool:
        return self.unit.is_tied

    @property
    def pair_multiplicity(self) -> tuple[int, ...]:
        return self.exact_pair_counts


DigitTransitionState = DrawDigitState


def route_label(current_date: date, next_date: date) -> str:
    """Return a stable weekday transition label such as ``Sat->Mon``."""
    if any(
        isinstance(value, datetime) or not isinstance(value, date)
        for value in (current_date, next_date)
    ):
        raise TypeError("route dates must be date values")
    if next_date <= current_date:
        raise ValueError("next_date must be after current_date")
    return (
        f"{_WEEKDAY_LABELS[current_date.weekday()]}"
        f"->{_WEEKDAY_LABELS[next_date.weekday()]}"
    )


def _build_distribution(counts: Sequence[int]) -> DigitDistribution:
    if len(counts) != 10 or sum(counts) != TAILS_PER_DRAW:
        raise ValueError("digit counts must contain 10 bins totaling 18")
    immutable_counts = tuple(int(count) for count in counts)
    shares = tuple(count / float(TAILS_PER_DRAW) for count in immutable_counts)
    max_count = max(immutable_counts)
    dominant_digits = tuple(
        digit for digit, count in enumerate(immutable_counts) if count == max_count
    )
    sorted_counts = sorted(immutable_counts, reverse=True)
    margin = sorted_counts[0] - sorted_counts[1]
    entropy = -sum(share * math.log(share) for share in shares if share > 0.0)
    return DigitDistribution(
        counts=immutable_counts,
        shares=shares,
        coverage=tuple(count > 0 for count in immutable_counts),
        dominant_digits=dominant_digits,
        unique_dominant_digit=(
            dominant_digits[0] if len(dominant_digits) == 1 else None
        ),
        max_count=max_count,
        margin=margin,
        normalized_entropy=entropy / math.log(10.0),
    )


def build_draw_state(snapshot: DrawSnapshot, next_date: date) -> DrawDigitState:
    """Build head, unit and exact-pair state for one complete draw."""
    if not snapshot.is_complete or len(snapshot.tails) != TAILS_PER_DRAW:
        raise ValueError("state requires a complete 18-tail XSMN draw")

    head_counts = [0] * 10
    unit_counts = [0] * 10
    for tail in snapshot.tails:
        head_counts[tail // 10] += 1
        unit_counts[tail % 10] += 1

    return DrawDigitState(
        province=snapshot.province,
        draw_date=snapshot.draw_date,
        next_date=next_date,
        route_label=route_label(snapshot.draw_date, next_date),
        head=_build_distribution(head_counts),
        unit=_build_distribution(unit_counts),
        exact_pair_counts=snapshot.exact_pair_counts,
    )


def build_state_sequence(
    draws: Iterable[DrawSnapshot], target_date: date
) -> tuple[DrawDigitState, ...]:
    """Build one province's historical states, routing the latest to target."""
    if isinstance(target_date, datetime) or not isinstance(target_date, date):
        raise TypeError("target_date must be a date")
    ordered = sorted(
        (draw for draw in draws if draw.draw_date < target_date),
        key=lambda draw: draw.draw_date,
    )
    if not ordered:
        return ()
    provinces = {draw.province for draw in ordered}
    if len(provinces) != 1:
        raise ValueError("a state sequence must contain exactly one province")
    draw_dates = [draw.draw_date for draw in ordered]
    if len(draw_dates) != len(set(draw_dates)):
        raise ValueError("a province state sequence cannot repeat draw dates")

    states = []
    for index, draw in enumerate(ordered):
        next_date = (
            ordered[index + 1].draw_date
            if index + 1 < len(ordered)
            else target_date
        )
        states.append(build_draw_state(draw, next_date))
    return tuple(states)


def build_state_sequences(
    draws_by_province: Mapping[str, Sequence[DrawSnapshot]], target_date: date
) -> dict[str, tuple[DrawDigitState, ...]]:
    """Build an isolated chronological state sequence for every province."""
    result: dict[str, tuple[DrawDigitState, ...]] = {}
    for province, draws in draws_by_province.items():
        if any(draw.province != province for draw in draws):
            raise ValueError("draw province does not match its sequence key")
        result[province] = build_state_sequence(draws, target_date)
    return result


def latest_state_before_target(
    states: Iterable[DrawDigitState], target_date: date
) -> Optional[DrawDigitState]:
    """Return the latest state strictly before target, or ``None``."""
    if isinstance(target_date, datetime) or not isinstance(target_date, date):
        raise TypeError("target_date must be a date")
    eligible = [state for state in states if state.draw_date < target_date]
    if not eligible:
        return None
    return max(eligible, key=lambda state: (state.draw_date, state.province))
