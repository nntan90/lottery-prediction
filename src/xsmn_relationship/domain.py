"""Typed contracts and matched-draw normalization for ``relationship``."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
import math
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


EXPECTED_PRIZE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"DB": 1, "1": 1, "2": 1, "3": 2, "4": 7, "5": 1, "6": 3, "7": 1, "8": 1}
)


def validate_provinces(provinces: Sequence[str]) -> tuple[str, str]:
    """Return the exact two-province XSMN scope in caller order."""
    if isinstance(provinces, (str, bytes)):
        raise TypeError("provinces must be a two-item sequence")
    normalized = tuple(
        str(value).strip()
        for value in provinces
        if value is not None and str(value).strip()
    )
    if len(normalized) != 2 or len(set(normalized)) != 2:
        raise ValueError("relationship requires exactly two distinct provinces")
    return normalized[0], normalized[1]


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class RelationshipConfig:
    """V1 ranking hypotheses; every score remains explicitly uncalibrated."""

    top_k_per_source: int = 5
    min_active_model_families: int = 4
    min_anchor_vote_ratio: float = 0.50
    recent_anchor_lookback: int = 2
    reject_anchor_if_hits: int = 2
    history_lookback_occurrences: int = 104
    min_history_occurrences: int = 52
    prior_strength: float = 20.0
    min_pair_support_count: int = 3
    require_distinct_unit_digits: bool = True
    node_component_weight: float = 1.0
    edge_component_weight: float = 1.0
    combo_component_weight: float = 1.0

    def __post_init__(self) -> None:
        integer_fields = {
            "top_k_per_source": self.top_k_per_source,
            "min_active_model_families": self.min_active_model_families,
            "recent_anchor_lookback": self.recent_anchor_lookback,
            "reject_anchor_if_hits": self.reject_anchor_if_hits,
            "history_lookback_occurrences": self.history_lookback_occurrences,
            "min_history_occurrences": self.min_history_occurrences,
            "min_pair_support_count": self.min_pair_support_count,
        }
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in integer_fields.values()
        ):
            raise ValueError("relationship integer config values must be positive")
        if self.top_k_per_source > 100:
            raise ValueError("top_k_per_source cannot exceed 100")
        if self.min_history_occurrences > self.history_lookback_occurrences:
            raise ValueError("min_history_occurrences cannot exceed history lookback")
        if self.reject_anchor_if_hits > self.recent_anchor_lookback:
            raise ValueError("anchor rejection hits cannot exceed recent lookback")
        if (
            not math.isfinite(self.min_anchor_vote_ratio)
            or not 0.0 <= self.min_anchor_vote_ratio <= 1.0
        ):
            raise ValueError("min_anchor_vote_ratio must be within [0, 1]")
        if not math.isfinite(self.prior_strength) or self.prior_strength <= 0:
            raise ValueError("prior_strength must be positive")
        component_weights = (
            self.node_component_weight,
            self.edge_component_weight,
            self.combo_component_weight,
        )
        if any(not math.isfinite(value) or value <= 0 for value in component_weights):
            raise ValueError("relationship component weights must be positive")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe deterministic config snapshot."""
        return asdict(self)


@dataclass(frozen=True)
class MatchedOccasion:
    """One date on which every province in the exact target scope completed."""

    draw_date: date
    tails_by_province: Mapping[str, frozenset[int]]

    def __post_init__(self) -> None:
        normalized: dict[str, frozenset[int]] = {}
        for province in sorted(self.tails_by_province):
            tails = frozenset(int(value) for value in self.tails_by_province[province])
            if any(value < 0 or value > 99 for value in tails):
                raise ValueError("tail_2d must be within 00..99")
            normalized[str(province)] = tails
        object.__setattr__(self, "draw_date", _as_date(self.draw_date))
        object.__setattr__(self, "tails_by_province", MappingProxyType(normalized))

    @property
    def merged_tails(self) -> frozenset[int]:
        merged: set[int] = set()
        for tails in self.tails_by_province.values():
            merged.update(tails)
        return frozenset(merged)


def build_matched_occasions(
    rows: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date,
    *,
    limit: int,
) -> tuple[MatchedOccasion, ...]:
    """Build complete same-date occasions using an exclusive target cutoff.

    Lookback is applied after matching complete province draws, so it measures
    draw occurrences rather than calendar days.
    """
    province_scope = validate_provinces(provinces)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    allowed = set(province_scope)
    prize_counts: dict[tuple[date, str], Counter[str]] = defaultdict(Counter)
    tails: dict[tuple[date, str], set[int]] = defaultdict(set)
    for row in rows:
        region = str(row.get("region") or "XSMN").upper()
        if region != "XSMN":
            continue
        province = str(row.get("province") or "")
        if province not in allowed:
            continue
        draw_date = _as_date(row.get("draw_date"))
        if draw_date >= target_date:
            continue
        prize_code = str(row.get("prize_code") or "")
        if prize_code not in EXPECTED_PRIZE_COUNTS:
            continue
        tail = int(row["tail_2d"])
        if not 0 <= tail <= 99:
            raise ValueError(f"tail_2d out of range: {tail}")
        prize_counts[(draw_date, province)][prize_code] += 1
        tails[(draw_date, province)].add(tail)

    complete_dates: dict[str, set[date]] = {province: set() for province in province_scope}
    for draw_date, province in sorted(prize_counts):
        counts = prize_counts[(draw_date, province)]
        if all(counts[code] == expected for code, expected in EXPECTED_PRIZE_COUNTS.items()):
            complete_dates[province].add(draw_date)

    matched_dates = sorted(set.intersection(*(complete_dates[p] for p in province_scope)))
    matched = tuple(
        MatchedOccasion(
            draw_date=draw_date,
            tails_by_province={
                province: frozenset(tails[(draw_date, province)])
                for province in province_scope
            },
        )
        for draw_date in matched_dates[-limit:]
    )
    return matched
