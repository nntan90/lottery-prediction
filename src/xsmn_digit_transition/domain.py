"""Immutable draw-level contracts for the isolated XSMN PDA/DDT model."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from numbers import Integral
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Sequence, Union


PRIZE_CODES = ("DB", "1", "2", "3", "4", "5", "6", "7", "8")
EXPECTED_PRIZE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"DB": 1, "1": 1, "2": 1, "3": 2, "4": 7, "5": 1, "6": 3, "7": 1, "8": 1}
)
TAILS_PER_DRAW = sum(EXPECTED_PRIZE_COUNTS.values())

ProvinceCollection = Union[tuple[str, ...], list[str]]


def validate_provinces(provinces: ProvinceCollection) -> tuple[str, ...]:
    """Validate an exact, ordered province scope without changing its values."""
    if not isinstance(provinces, (tuple, list)):
        raise TypeError("provinces must be a nonempty tuple or list")
    if not provinces:
        raise ValueError("provinces must not be empty")
    if any(not isinstance(province, str) or not province.strip() for province in provinces):
        raise ValueError("each province must be a nonempty string")
    normalized = tuple(provinces)
    if len(set(normalized)) != len(normalized):
        raise ValueError("provinces must be distinct")
    return normalized


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"invalid draw_date: {value!r}") from exc
    raise ValueError(f"invalid draw_date: {value!r}")


def _parse_tail(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"invalid tail_2d: {value!r}")
    if isinstance(value, Integral):
        tail = int(value)
    elif isinstance(value, str):
        raw = value.strip()
        digits = raw[1:] if raw.startswith("+") else raw
        if not digits or any(character not in "0123456789" for character in digits):
            raise ValueError(f"invalid tail_2d: {value!r}")
        tail = int(raw)
    else:
        raise ValueError(f"invalid tail_2d: {value!r}")
    if not 0 <= tail <= 99:
        raise ValueError(f"tail_2d out of range: {tail}")
    return tail


@dataclass(frozen=True)
class DrawSnapshot:
    """One province draw retaining all prize-level and exact-pair multiplicities."""

    province: str
    draw_date: date
    prizes: Mapping[str, tuple[int, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.province, str) or not self.province.strip():
            raise ValueError("province must be a nonempty string")
        if isinstance(self.draw_date, datetime) or not isinstance(self.draw_date, date):
            raise ValueError("draw_date must be a date")
        unknown_codes = set(self.prizes) - set(PRIZE_CODES)
        if unknown_codes:
            raise ValueError(f"unknown prize codes: {sorted(unknown_codes)}")
        normalized = {
            code: tuple(sorted(_parse_tail(value) for value in self.prizes.get(code, ())))
            for code in PRIZE_CODES
        }
        object.__setattr__(self, "prizes", MappingProxyType(normalized))

    @property
    def is_complete(self) -> bool:
        """Whether every prize block has the exact XSMN row count."""
        return all(
            len(self.prizes[code]) == EXPECTED_PRIZE_COUNTS[code]
            for code in PRIZE_CODES
        )

    @property
    def tails(self) -> tuple[int, ...]:
        """All 18 tails in stable prize order, including duplicate values."""
        return tuple(
            tail
            for prize_code in PRIZE_CODES
            for tail in self.prizes[prize_code]
        )

    @property
    def exact_pair_counts(self) -> tuple[int, ...]:
        """A dense 00-99 multiplicity vector for the draw."""
        counts = [0] * 100
        for tail in self.tails:
            counts[tail] += 1
        return tuple(counts)

    @property
    def pair_multiplicity(self) -> tuple[int, ...]:
        """Alias emphasizing that repeated exact pairs are retained."""
        return self.exact_pair_counts


def normalize_tail_rows(
    rows: Iterable[Mapping[str, object]],
    provinces: ProvinceCollection,
    before_date: Optional[date] = None,
) -> dict[str, tuple[DrawSnapshot, ...]]:
    """Normalize raw tails into complete, chronologically sorted province draws.

    Only requested provinces are considered. ``before_date`` is strict, so rows
    on or after the cutoff cannot enter a historical snapshot. Invalid in-scope
    tails fail fast; malformed prize blocks remain incomplete and are omitted.
    """
    province_scope = validate_provinces(provinces)
    if before_date is not None and (
        isinstance(before_date, datetime) or not isinstance(before_date, date)
    ):
        raise TypeError("before_date must be a date or None")

    allowed = set(province_scope)
    grouped: dict[tuple[str, date], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        if str(row.get("region") or "").upper() != "XSMN":
            continue
        province_value = row.get("province")
        province = province_value if isinstance(province_value, str) else ""
        if province not in allowed:
            continue
        draw_date = _parse_date(row.get("draw_date"))
        if before_date is not None and draw_date >= before_date:
            continue
        prize_code = str(row.get("prize_code") or "")
        if prize_code not in EXPECTED_PRIZE_COUNTS:
            continue
        grouped[(province, draw_date)][prize_code].append(
            _parse_tail(row.get("tail_2d"))
        )

    result: dict[str, list[DrawSnapshot]] = {
        province: [] for province in province_scope
    }
    for (province, draw_date), prizes in sorted(
        grouped.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        snapshot = DrawSnapshot(province, draw_date, prizes)
        if snapshot.is_complete and len(snapshot.tails) == TAILS_PER_DRAW:
            result[province].append(snapshot)

    return {
        province: tuple(sorted(result[province], key=lambda draw: draw.draw_date))
        for province in province_scope
    }
