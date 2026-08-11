"""Immutable draw-level contracts for the isolated XSMN PDA/DDT model."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from numbers import Integral
from types import MappingProxyType
from typing import Iterable, Mapping, Optional, Sequence, Union


PRIZE_CODES = ("DB", "1", "2", "3", "4", "5", "6", "7", "8")
EXPECTED_PRIZE_COUNTS: Mapping[str, int] = MappingProxyType(
    {"DB": 1, "1": 1, "2": 1, "3": 2, "4": 7, "5": 1, "6": 3, "7": 1, "8": 1}
)
TAILS_PER_DRAW = sum(EXPECTED_PRIZE_COUNTS.values())
FRESHNESS_MANIFEST_VERSION = "ddt_input_v1"
RAW_PRIZE_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "DB": "special_prize",
        "1": "first_prize",
        "2": "second_prize",
        "3": "third_prize",
        "4": "fourth_prize",
        "5": "fifth_prize",
        "6": "sixth_prize",
        "7": "seventh_prize",
        "8": "eighth_prize",
    }
)

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


def _raw_prize_tail(value: object) -> int:
    """Extract one two-digit tail from a raw prize without exposing its value."""
    if not isinstance(value, str):
        raise ValueError("raw prize must be a digit string")
    digits = value.strip()
    if len(digits) < 2 or any(character not in "0123456789" for character in digits):
        raise ValueError("raw prize is not a valid numeric result")
    return int(digits[-2:])


def raw_draw_snapshot(row: Mapping[str, object]) -> DrawSnapshot:
    """Convert one ``lottery_draws`` row to the canonical 18-tail contract."""
    province = row.get("province")
    if not isinstance(province, str) or not province:
        raise ValueError("raw draw province is missing")
    prizes: dict[str, tuple[int, ...]] = {}
    for prize_code, field_name in RAW_PRIZE_FIELDS.items():
        raw = row.get(field_name)
        values = raw if isinstance(raw, (list, tuple)) else ([] if raw is None else [raw])
        try:
            prizes[prize_code] = tuple(_raw_prize_tail(value) for value in values)
        except ValueError:
            prizes[prize_code] = ()
    return DrawSnapshot(
        province=province,
        draw_date=_parse_date(row.get("draw_date")),
        prizes=prizes,
    )


def canonical_draw_content(draw: DrawSnapshot) -> dict[str, object]:
    """Return ID-free, order-independent content for hashing and parity checks."""
    return {
        "province": draw.province,
        "draw_date": draw.draw_date.isoformat(),
        "prizes": {
            code: list(draw.prizes[code])
            for code in PRIZE_CODES
        },
    }


def _sha256_payload(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def fingerprint_draw_history(
    draws_by_province: Mapping[str, Sequence[DrawSnapshot]],
    *,
    target_date: date,
    target_provinces: Sequence[str],
) -> str:
    """Fingerprint the exact complete, normalized history consumed by DDT."""
    draws = sorted(
        (
            draw
            for province in draws_by_province
            for draw in draws_by_province[province]
        ),
        key=lambda draw: (draw.draw_date, draw.province),
    )
    return _sha256_payload(
        {
            "manifest_version": FRESHNESS_MANIFEST_VERSION,
            "target_date": target_date.isoformat(),
            "target_provinces": list(target_provinces),
            "draws": [canonical_draw_content(draw) for draw in draws],
        }
    )


def build_freshness_manifest(
    *,
    target_date: date,
    target_provinces: Sequence[str],
    expected_anchors: Mapping[str, date],
    regional_boundary_date: date,
    regional_provinces: Sequence[str],
    raw_rows: Iterable[Mapping[str, object]],
    tail_rows: Iterable[Mapping[str, object]],
) -> dict:
    """Certify required boundary draws against raw rows and extracted tails.

    Certification fails closed.  Issues contain only province/date/code labels;
    raw prize values and database identifiers never enter the returned manifest
    or either deterministic fingerprint.
    """
    province_scope = validate_provinces(list(target_provinces))
    if len(province_scope) != 2:
        raise ValueError("DDT freshness requires exactly two target provinces")
    regional_scope = validate_provinces(list(regional_provinces))
    expected_anchor_map = {
        province: expected_anchors[province]
        for province in province_scope
    }
    required_roles: dict[tuple[str, date], set[str]] = defaultdict(set)
    for province in province_scope:
        required_roles[(province, expected_anchor_map[province])].add("target_anchor")
    for province in regional_scope:
        required_roles[(province, regional_boundary_date)].add("regional_d_minus_1")

    raw_by_key: dict[tuple[str, date], list[Mapping[str, object]]] = defaultdict(list)
    for row in raw_rows:
        if str(row.get("region") or "").upper() != "XSMN":
            continue
        province = row.get("province")
        if not isinstance(province, str):
            continue
        try:
            key = (province, _parse_date(row.get("draw_date")))
        except ValueError:
            continue
        if key in required_roles:
            raw_by_key[key].append(row)

    tails_by_key: dict[tuple[str, date], list[Mapping[str, object]]] = defaultdict(list)
    for row in tail_rows:
        if str(row.get("region") or "").upper() != "XSMN":
            continue
        province = row.get("province")
        if not isinstance(province, str):
            continue
        try:
            key = (province, _parse_date(row.get("draw_date")))
        except ValueError:
            continue
        if key in required_roles:
            tails_by_key[key].append(row)

    issues: list[dict[str, str]] = []
    certified: dict[tuple[str, date], DrawSnapshot] = {}
    coverage: list[dict[str, object]] = []
    for province, draw_date in sorted(required_roles, key=lambda item: (item[1], item[0])):
        key = (province, draw_date)
        raw_candidates = raw_by_key.get(key, [])
        boundary_tails = tails_by_key.get(key, [])
        entry: dict[str, object] = {
            "province": province,
            "draw_date": draw_date.isoformat(),
            "roles": sorted(required_roles[key]),
            "raw_draw_count": len(raw_candidates),
            "tail_row_count": len(boundary_tails),
            "certified": False,
        }

        def add_issue(code: str) -> None:
            issue = {
                "code": code,
                "province": province,
                "draw_date": draw_date.isoformat(),
            }
            issues.append(issue)

        if len(raw_candidates) != 1:
            add_issue("missing_raw_draw" if not raw_candidates else "duplicate_raw_draw")
            coverage.append(entry)
            continue
        try:
            raw_snapshot = raw_draw_snapshot(raw_candidates[0])
        except (TypeError, ValueError):
            add_issue("invalid_raw_draw")
            coverage.append(entry)
            continue
        if not raw_snapshot.is_complete or len(raw_snapshot.tails) != TAILS_PER_DRAW:
            add_issue("raw_draw_incomplete")
            coverage.append(entry)
            continue
        raw_id = raw_candidates[0].get("id")
        if (
            isinstance(raw_id, bool)
            or not isinstance(raw_id, Integral)
            or int(raw_id) < 1
        ):
            add_issue("invalid_raw_draw_id")
            coverage.append(entry)
            continue
        if len(boundary_tails) != TAILS_PER_DRAW:
            add_issue("tail_row_count_mismatch")
            coverage.append(entry)
            continue
        if any(
            not isinstance(row.get("prize_code"), str)
            or row.get("prize_code") not in EXPECTED_PRIZE_COUNTS
            for row in boundary_tails
        ):
            add_issue("unknown_tail_prize_code")
            coverage.append(entry)
            continue
        if any(
            isinstance(row.get("draw_id"), bool)
            or not isinstance(row.get("draw_id"), Integral)
            or int(row["draw_id"]) != int(raw_id)
            for row in boundary_tails
        ):
            add_issue("tail_draw_link_mismatch")
            coverage.append(entry)
            continue

        try:
            normalized = normalize_tail_rows(
                boundary_tails,
                [province],
                before_date=target_date,
            )[province]
        except (TypeError, ValueError):
            normalized = ()
            add_issue("invalid_tail_content")
        tail_snapshot = next(
            (draw for draw in normalized if draw.draw_date == draw_date),
            None,
        )
        if tail_snapshot is None:
            has_invalid_tail_issue = any(
                issue["code"] == "invalid_tail_content"
                and issue["province"] == province
                and issue["draw_date"] == draw_date.isoformat()
                for issue in issues
            )
            if not has_invalid_tail_issue:
                add_issue("tails_incomplete")
            coverage.append(entry)
            continue
        if canonical_draw_content(raw_snapshot) != canonical_draw_content(tail_snapshot):
            add_issue("raw_tail_mismatch")
            coverage.append(entry)
            continue
        entry["certified"] = True
        certified[key] = tail_snapshot
        coverage.append(entry)

    is_certified = len(certified) == len(required_roles) and not issues
    boundary_payload = {
        "manifest_version": FRESHNESS_MANIFEST_VERSION,
        "target_date": target_date.isoformat(),
        "target_provinces": list(province_scope),
        "expected_anchors": {
            province: expected_anchor_map[province].isoformat()
            for province in province_scope
        },
        "regional_boundary_date": regional_boundary_date.isoformat(),
        "regional_provinces": list(regional_scope),
        "draws": [
            canonical_draw_content(certified[key])
            for key in sorted(certified, key=lambda item: (item[1], item[0]))
        ],
    }
    actual_anchors = {
        province: (
            expected_anchor_map[province].isoformat()
            if (province, expected_anchor_map[province]) in certified
            else None
        )
        for province in province_scope
    }
    certified_regional = [
        province
        for province in regional_scope
        if (province, regional_boundary_date) in certified
    ]
    return {
        "manifest_version": FRESHNESS_MANIFEST_VERSION,
        "status": "certified" if is_certified else "input_not_fresh",
        "target_date": target_date.isoformat(),
        "data_cutoff_rule": "draw_date < target_date",
        "target_provinces": list(province_scope),
        "expected_anchors": {
            province: expected_anchor_map[province].isoformat()
            for province in province_scope
        },
        "actual_anchors": actual_anchors,
        "regional_boundary_date": regional_boundary_date.isoformat(),
        "regional_scheduled_provinces": list(regional_scope),
        "regional_certified_provinces": certified_regional,
        "required_draw_count": len(required_roles),
        "certified_draw_count": len(certified),
        "required_tail_count": len(required_roles) * TAILS_PER_DRAW,
        "certified_tail_count": len(certified) * TAILS_PER_DRAW,
        "coverage": coverage,
        "issues": issues,
        "boundary_watermark": _sha256_payload(boundary_payload) if is_certified else None,
    }


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
