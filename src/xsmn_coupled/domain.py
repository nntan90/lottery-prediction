"""Domain contracts and validation for Coupled Motif Retrieval."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import math
from types import MappingProxyType
from typing import Iterable, Mapping, Optional


PRIZE_CODES = ("DB", "1", "2", "3", "4", "5", "6", "7", "8")
EXPECTED_PRIZE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"DB": 1, "1": 1, "2": 1, "3": 2, "4": 7, "5": 1, "6": 3, "7": 1, "8": 1}
)


@dataclass(frozen=True)
class DrawSnapshot:
    """One complete province draw, retaining prize-level tail multiplicity."""

    province: str
    draw_date: date
    prizes: Mapping[str, tuple[int, ...]]

    def __post_init__(self) -> None:
        normalized = {
            code: tuple(sorted(int(value) for value in self.prizes.get(code, ())))
            for code in PRIZE_CODES
        }
        object.__setattr__(self, "prizes", MappingProxyType(normalized))

    @property
    def is_complete(self) -> bool:
        return all(
            len(self.prizes[code]) == EXPECTED_PRIZE_COUNTS[code]
            for code in PRIZE_CODES
        )

    @property
    def tails(self) -> frozenset[int]:
        return frozenset(value for values in self.prizes.values() for value in values)


@dataclass(frozen=True)
class CMRConfig:
    """Deterministic retrieval and Bayesian shrinkage settings."""

    top_k: int = 25
    min_neighbors: int = 8
    shrinkage_alpha: float = 5.0
    context_weight: float = 0.10
    evidence_cases: int = 5

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ValueError("top_k must be positive")
        if self.min_neighbors < 1 or self.min_neighbors > self.top_k:
            raise ValueError("min_neighbors must be between 1 and top_k")
        if not math.isfinite(self.shrinkage_alpha) or self.shrinkage_alpha <= 0:
            raise ValueError("shrinkage_alpha must be positive")
        if not math.isfinite(self.context_weight) or not 0.0 <= self.context_weight < 1.0:
            raise ValueError("context_weight must be in [0, 1)")
        if self.evidence_cases < 1:
            raise ValueError("evidence_cases must be positive")


def normalize_tail_rows(
    rows: Iterable[Mapping[str, object]],
    provinces: tuple[str, str],
    before_date: Optional[date] = None,
) -> dict[str, tuple[DrawSnapshot, ...]]:
    """Convert raw ``tails_2d`` rows into sorted, complete draw snapshots.

    Rows outside the two-province scope or at/after ``before_date`` are ignored.
    A draw is admitted only when every XSMN prize has its expected row count.
    """
    allowed = set(provinces)
    grouped: dict[tuple[str, date], dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for row in rows:
        province = str(row.get("province") or "")
        if province not in allowed:
            continue
        raw_date = row.get("draw_date")
        draw_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        if before_date is not None and draw_date >= before_date:
            continue
        prize_code = str(row.get("prize_code") or "")
        if prize_code not in EXPECTED_PRIZE_COUNTS:
            continue
        tail = int(row["tail_2d"])
        if not 0 <= tail <= 99:
            raise ValueError(f"tail_2d out of range: {tail}")
        grouped[(province, draw_date)][prize_code].append(tail)

    by_province: dict[str, list[DrawSnapshot]] = {province: [] for province in provinces}
    for (province, draw_date), prizes in grouped.items():
        snapshot = DrawSnapshot(province=province, draw_date=draw_date, prizes=prizes)
        if snapshot.is_complete:
            by_province[province].append(snapshot)

    return {
        province: tuple(sorted(draws, key=lambda draw: draw.draw_date))
        for province, draws in by_province.items()
    }
