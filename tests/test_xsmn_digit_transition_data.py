from __future__ import annotations

from datetime import date
import math
import random
from typing import Optional

import pytest

from src.xsmn_digit_transition.domain import (
    EXPECTED_PRIZE_COUNTS,
    build_freshness_manifest,
    fingerprint_draw_history,
    normalize_tail_rows,
)
from src.xsmn_digit_transition.repository import (
    load_boundary_sources,
    load_tail_history,
)
from src.xsmn_digit_transition.state import (
    build_draw_state,
    build_state_sequences,
    latest_state_before_target,
)


def _draw_rows(
    province: str,
    draw_date: date,
    tails: Optional[list[int]] = None,
    *,
    first_id: int = 1,
) -> list[dict]:
    values = tails or list(range(18))
    assert len(values) == 18
    rows: list[dict] = []
    offset = 0
    for prize_code, count in EXPECTED_PRIZE_COUNTS.items():
        for _ in range(count):
            rows.append(
                {
                    "id": first_id + offset,
                    "region": "XSMN",
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                    "prize_code": prize_code,
                    "tail_2d": values[offset],
                }
            )
            offset += 1
    return rows


def _raw_draw(
    province: str,
    draw_date: date,
    *,
    draw_id: int,
    tails: Optional[list[int]] = None,
) -> dict:
    values = tails or list(range(18))
    fields = {
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
    row = {
        "id": draw_id,
        "region": "XSMN",
        "province": province,
        "draw_date": draw_date.isoformat(),
    }
    offset = 0
    for code, count in EXPECTED_PRIZE_COUNTS.items():
        prizes = [f"10{values[offset + index]:02d}" for index in range(count)]
        row[fields[code]] = prizes if count > 1 else prizes[0]
        offset += count
    return row


def _certification_rows() -> tuple[list[dict], list[dict]]:
    requirements = (
        ("vung-tau", date(2026, 8, 4)),
        ("ben-tre", date(2026, 8, 4)),
        ("tp-hcm", date(2026, 8, 10)),
        ("dong-thap", date(2026, 8, 10)),
        ("ca-mau", date(2026, 8, 10)),
    )
    raw_rows: list[dict] = []
    tail_rows: list[dict] = []
    next_tail_id = 1
    for draw_id, (province, draw_date) in enumerate(requirements, start=10):
        values = [(draw_id + offset) % 100 for offset in range(18)]
        raw_rows.append(
            _raw_draw(
                province,
                draw_date,
                draw_id=draw_id,
                tails=values,
            )
        )
        rows = _draw_rows(
            province,
            draw_date,
            values,
            first_id=next_tail_id,
        )
        for row in rows:
            row["draw_id"] = draw_id
        tail_rows.extend(rows)
        next_tail_id += len(rows)
    return raw_rows, tail_rows


def _freshness_manifest(raw_rows: list[dict], tail_rows: list[dict]) -> dict:
    return build_freshness_manifest(
        target_date=date(2026, 8, 11),
        target_provinces=("vung-tau", "ben-tre"),
        expected_anchors={
            "vung-tau": date(2026, 8, 4),
            "ben-tre": date(2026, 8, 4),
        },
        regional_boundary_date=date(2026, 8, 10),
        regional_provinces=("tp-hcm", "dong-thap", "ca-mau"),
        raw_rows=raw_rows,
        tail_rows=tail_rows,
    )


def test_normalize_admits_only_complete_draws_and_preserves_multiplicity() -> None:
    complete_date = date(2026, 7, 15)
    incomplete_date = date(2026, 7, 22)
    repeated = [13] * 5 + [22] * 4 + list(range(9))
    rows = _draw_rows("dong-nai", complete_date, repeated)
    rows.extend(_draw_rows("dong-nai", incomplete_date)[:-1])

    normalized = normalize_tail_rows(rows, ("dong-nai",))

    assert len(normalized["dong-nai"]) == 1
    snapshot = normalized["dong-nai"][0]
    assert snapshot.draw_date == complete_date
    assert snapshot.is_complete
    assert len(snapshot.tails) == 18
    assert snapshot.exact_pair_counts[13] == 5
    assert snapshot.exact_pair_counts[22] == 4
    assert tuple(len(snapshot.prizes[code]) for code in EXPECTED_PRIZE_COUNTS) == (
        1,
        1,
        1,
        2,
        7,
        1,
        3,
        1,
        1,
    )


def test_normalize_has_strict_cutoff_and_province_isolation() -> None:
    target = date(2026, 7, 22)
    rows = _draw_rows("dong-nai", date(2026, 7, 15))
    rows.extend(_draw_rows("dong-nai", target, first_id=20))
    rows.extend(_draw_rows("can-tho", date(2026, 7, 16), first_id=40))

    normalized = normalize_tail_rows(rows, ["dong-nai"], before_date=target)

    assert tuple(normalized) == ("dong-nai",)
    assert [draw.draw_date for draw in normalized["dong-nai"]] == [date(2026, 7, 15)]


def test_normalize_rejects_non_xsmn_rows_even_when_province_matches() -> None:
    rows = _draw_rows("dong-nai", date(2026, 7, 15))
    for row in rows:
        row["region"] = "XSMB"

    normalized = normalize_tail_rows(rows, ("dong-nai",))

    assert normalized == {"dong-nai": ()}


@pytest.mark.parametrize("tail", [-1, 100, True, "not-a-tail", 3.5])
def test_normalize_rejects_invalid_in_scope_tails(tail: object) -> None:
    rows = _draw_rows("dong-nai", date(2026, 7, 15))
    rows[0]["tail_2d"] = tail

    with pytest.raises(ValueError, match="tail_2d"):
        normalize_tail_rows(rows, ("dong-nai",))


def test_state_counts_shares_coverage_tie_margin_and_entropy() -> None:
    tails = [3, 13, 23, 33, 43, 2, 12, 22, 32, 42, 1, 11, 21, 5, 15, 25, 35, 45]
    snapshot = normalize_tail_rows(
        _draw_rows("dong-nai", date(2026, 7, 15), tails),
        ("dong-nai",),
    )["dong-nai"][0]

    state = build_draw_state(snapshot, date(2026, 7, 22))

    assert sum(state.head_counts) == 18
    assert sum(state.unit_counts) == 18
    assert sum(state.head_shares) == pytest.approx(1.0)
    assert sum(state.unit_shares) == pytest.approx(1.0)
    assert state.unit_counts == (0, 3, 5, 5, 0, 5, 0, 0, 0, 0)
    assert state.unit_coverage == (
        False,
        True,
        True,
        True,
        False,
        True,
        False,
        False,
        False,
        False,
    )
    assert state.unit_dominant_digits == (2, 3, 5)
    assert state.unit_unique_dominant_digit is None
    assert state.unit_max_count == 5
    assert state.unit_margin == 0
    expected_entropy = -sum(
        (count / 18.0) * math.log(count / 18.0)
        for count in (3, 5, 5, 5)
    ) / math.log(10.0)
    assert state.unit_normalized_entropy == pytest.approx(expected_entropy)
    assert state.exact_pair_counts[3] == 1
    assert state.province == "dong-nai"
    assert state.draw_date == date(2026, 7, 15)


def test_state_unique_dominant_digit_and_margin() -> None:
    tails = [3] * 5 + [2] * 3 + [1] * 2 + [0, 4, 5, 6, 7, 8, 9, 10]
    snapshot = normalize_tail_rows(
        _draw_rows("dong-nai", date(2026, 7, 15), tails),
        ("dong-nai",),
    )["dong-nai"][0]

    state = build_draw_state(snapshot, date(2026, 7, 22))

    assert state.unit_dominant_digits == (3,)
    assert state.unit_unique_dominant_digit == 3
    assert state.unit_max_count == 5
    assert state.unit_margin == 2
    assert state.unit_is_tied is False


def test_tphcm_state_sequence_routes_saturday_to_monday_and_monday_to_saturday() -> None:
    rows = _draw_rows("tp-hcm", date(2026, 7, 11))
    rows.extend(_draw_rows("tp-hcm", date(2026, 7, 13), first_id=20))
    rows.extend(_draw_rows("tp-hcm", date(2026, 7, 18), first_id=40))
    draws = normalize_tail_rows(rows, ("tp-hcm",), before_date=date(2026, 7, 20))

    states = build_state_sequences(draws, date(2026, 7, 20))["tp-hcm"]

    assert [state.route_label for state in states] == [
        "Sat->Mon",
        "Mon->Sat",
        "Sat->Mon",
    ]
    assert latest_state_before_target(states, date(2026, 7, 20)) == states[-1]


def test_normalization_and_state_are_invariant_to_raw_row_order() -> None:
    rows = _draw_rows("dong-nai", date(2026, 7, 8), first_id=1)
    rows.extend(_draw_rows("dong-nai", date(2026, 7, 15), first_id=20))
    expected_draws = normalize_tail_rows(rows, ("dong-nai",))
    expected_states = build_state_sequences(expected_draws, date(2026, 7, 22))
    shuffled = list(rows)
    random.Random(42).shuffle(shuffled)

    actual_draws = normalize_tail_rows(shuffled, ("dong-nai",))
    actual_states = build_state_sequences(actual_draws, date(2026, 7, 22))

    assert actual_draws == expected_draws
    assert actual_states == expected_states


def test_freshness_manifest_certifies_exact_boundary_and_is_order_independent() -> None:
    raw_rows, tail_rows = _certification_rows()

    expected = _freshness_manifest(raw_rows, tail_rows)
    shuffled_raw = list(reversed(raw_rows))
    shuffled_tails = list(reversed(tail_rows))
    actual = _freshness_manifest(shuffled_raw, shuffled_tails)

    assert actual == expected
    assert actual["status"] == "certified"
    assert actual["expected_anchors"] == actual["actual_anchors"]
    assert actual["regional_boundary_date"] == "2026-08-10"
    assert actual["certified_draw_count"] == actual["required_draw_count"] == 5
    assert len(actual["boundary_watermark"]) == 64


def test_freshness_manifest_fails_closed_for_partial_or_mismatched_boundary() -> None:
    raw_rows, tail_rows = _certification_rows()
    partial = tail_rows[:-1]

    missing = _freshness_manifest(raw_rows, partial)

    assert missing["status"] == "input_not_fresh"
    assert missing["boundary_watermark"] is None
    assert missing["issues"] == [
        {
            "code": "tail_row_count_mismatch",
            "province": "ca-mau",
            "draw_date": "2026-08-10",
        }
    ]

    corrected_raw = [dict(row) for row in raw_rows]
    corrected_raw[0] = dict(corrected_raw[0], special_prize="9999")
    mismatch = _freshness_manifest(corrected_raw, tail_rows)
    assert mismatch["status"] == "input_not_fresh"
    assert mismatch["issues"][0]["code"] == "raw_tail_mismatch"


@pytest.mark.parametrize("invalid_prize", [1234, "12-34", "12abc", "１２３４"])
def test_raw_boundary_prizes_accept_only_ascii_digit_strings(
    invalid_prize: object,
) -> None:
    raw_rows, tail_rows = _certification_rows()
    raw_rows[0] = dict(raw_rows[0], special_prize=invalid_prize)

    manifest = _freshness_manifest(raw_rows, tail_rows)

    assert manifest["status"] == "input_not_fresh"
    assert manifest["issues"][0]["code"] == "raw_draw_incomplete"


def test_raw_boundary_prizes_allow_surrounding_whitespace() -> None:
    raw_rows, tail_rows = _certification_rows()
    original = str(raw_rows[0]["special_prize"])
    raw_rows[0] = dict(raw_rows[0], special_prize=f"  {original}  ")

    assert _freshness_manifest(raw_rows, tail_rows)["status"] == "certified"


@pytest.mark.parametrize(
    ("mutation", "expected_issue"),
    [
        ("extra_tail", "tail_row_count_mismatch"),
        ("unknown_prize", "unknown_tail_prize_code"),
        ("numeric_prize", "unknown_tail_prize_code"),
        ("invalid_raw_id", "invalid_raw_draw_id"),
        ("null_tail_draw_id", "tail_draw_link_mismatch"),
    ],
)
def test_boundary_requires_exact_rows_known_codes_and_strict_draw_link(
    mutation: str,
    expected_issue: str,
) -> None:
    raw_rows, tail_rows = _certification_rows()
    if mutation == "extra_tail":
        tail_rows.append(dict(tail_rows[0], id=9999))
    elif mutation == "unknown_prize":
        tail_rows[0] = dict(tail_rows[0], prize_code="UNKNOWN")
    elif mutation == "numeric_prize":
        tail_rows[0] = dict(tail_rows[0], prize_code=1)
    elif mutation == "invalid_raw_id":
        raw_rows[0] = dict(raw_rows[0], id=None)
    elif mutation == "null_tail_draw_id":
        tail_rows[0] = dict(tail_rows[0], draw_id=None)

    manifest = _freshness_manifest(raw_rows, tail_rows)

    assert manifest["status"] == "input_not_fresh"
    assert manifest["issues"][0]["code"] == expected_issue


def test_full_history_hash_changes_with_new_anchor_but_not_row_order() -> None:
    rows = _draw_rows("vung-tau", date(2026, 7, 28))
    rows.extend(_draw_rows("vung-tau", date(2026, 8, 4), first_id=20))
    draws = normalize_tail_rows(rows, ("vung-tau",), before_date=date(2026, 8, 11))
    expected = fingerprint_draw_history(
        draws,
        target_date=date(2026, 8, 11),
        target_provinces=("vung-tau", "ben-tre"),
    )
    shuffled = normalize_tail_rows(
        reversed(rows),
        ("vung-tau",),
        before_date=date(2026, 8, 11),
    )
    assert fingerprint_draw_history(
        shuffled,
        target_date=date(2026, 8, 11),
        target_provinces=("vung-tau", "ben-tre"),
    ) == expected

    old_draws = {"vung-tau": draws["vung-tau"][:-1]}
    assert fingerprint_draw_history(
        old_draws,
        target_date=date(2026, 8, 4),
        target_provinces=("vung-tau", "ben-tre"),
    ) != expected


class _FakeResponse:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, rows: list[dict], calls: list[tuple[str, object]]) -> None:
        self._rows = rows
        self._calls = calls
        self._filters: list[tuple[str, str, object]] = []
        self._limit = 1000

    def select(self, columns: str) -> "_FakeQuery":
        self._calls.append(("select", columns))
        return self

    def eq(self, column: str, value: object) -> "_FakeQuery":
        self._calls.append((f"eq:{column}", value))
        self._filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values: list[str]) -> "_FakeQuery":
        self._calls.append((f"in:{column}", values))
        self._filters.append(("in", column, values))
        return self

    def lt(self, column: str, value: object) -> "_FakeQuery":
        self._calls.append((f"lt:{column}", value))
        self._filters.append(("lt", column, value))
        return self

    def gt(self, column: str, value: object) -> "_FakeQuery":
        self._calls.append((f"gt:{column}", value))
        self._filters.append(("gt", column, value))
        return self

    def order(self, column: str, desc: bool = False) -> "_FakeQuery":
        self._calls.append((f"order:{column}", desc))
        return self

    def limit(self, value: int) -> "_FakeQuery":
        self._limit = value
        return self

    def execute(self) -> _FakeResponse:
        rows = list(self._rows)
        for operation, column, value in self._filters:
            if operation == "eq":
                rows = [row for row in rows if row[column] == value]
            elif operation == "in":
                rows = [row for row in rows if row[column] in value]
            elif operation == "lt":
                rows = [row for row in rows if row[column] < value]
            elif operation == "gt":
                rows = [row for row in rows if row[column] > value]
        return _FakeResponse(sorted(rows, key=lambda row: row["id"])[: self._limit])


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object]] = []

    def table(self, name: str) -> _FakeQuery:
        self.calls.append(("table", name))
        return _FakeQuery(self.rows, self.calls)


class _FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.supabase = _FakeSupabase(rows)


class _BoundarySupabase:
    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self.rows_by_table = rows_by_table
        self.calls: list[tuple[str, object]] = []

    def table(self, name: str) -> _FakeQuery:
        self.calls.append(("table", name))
        return _FakeQuery(self.rows_by_table[name], self.calls)


class _BoundaryDB:
    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self.supabase = _BoundarySupabase(rows_by_table)


def test_repository_is_read_only_strict_and_keyset_paginates_beyond_one_page() -> None:
    rows = _draw_rows("dong-nai", date(2026, 7, 15))
    rows.extend(_draw_rows("can-tho", date(2026, 7, 16), first_id=20))
    rows.extend(_draw_rows("tp-hcm", date(2026, 7, 18), first_id=40))
    rows.extend(_draw_rows("dong-nai", date(2026, 7, 22), first_id=60))
    db = _FakeDB(rows)

    loaded = load_tail_history(
        db,
        ["dong-nai", "can-tho"],
        date(2026, 7, 22),
        page_size=7,
    )

    assert len(loaded) == 36
    assert {row["province"] for row in loaded} == {"dong-nai", "can-tho"}
    assert all(row["draw_date"] < "2026-07-22" for row in loaded)
    assert [row["id"] for row in loaded] == sorted(row["id"] for row in loaded)
    calls = db.supabase.calls
    assert ("eq:region", "XSMN") in calls
    assert ("in:province", ["dong-nai", "can-tho"]) in calls
    assert ("lt:draw_date", "2026-07-22") in calls
    assert sum(operation == "table" for operation, _ in calls) > 1
    assert not any(
        operation.startswith(("insert", "update", "upsert", "delete"))
        for operation, _ in calls
    )


def test_boundary_repository_reads_only_exact_required_raw_and_tail_keys() -> None:
    raw_rows, tail_rows = _certification_rows()
    unrelated_raw = _raw_draw(
        "vung-tau",
        date(2026, 8, 10),
        draw_id=99,
    )
    unrelated_tails = _draw_rows(
        "vung-tau",
        date(2026, 8, 10),
        first_id=1000,
    )
    for row in unrelated_tails:
        row["draw_id"] = 99
    db = _BoundaryDB(
        {
            "lottery_draws": raw_rows + [unrelated_raw],
            "tails_2d": tail_rows + unrelated_tails,
        }
    )

    loaded_raw, loaded_tails = load_boundary_sources(
        db,
        [
            ("vung-tau", date(2026, 8, 4)),
            ("tp-hcm", date(2026, 8, 10)),
        ],
    )

    assert {(row["province"], row["draw_date"]) for row in loaded_raw} == {
        ("vung-tau", "2026-08-04"),
        ("tp-hcm", "2026-08-10"),
    }
    assert len(loaded_tails) == 36
    assert [value for operation, value in db.supabase.calls if operation == "table"] == [
        "lottery_draws",
        "tails_2d",
    ]
    assert not any(
        operation.startswith(("insert", "update", "upsert", "delete"))
        for operation, _ in db.supabase.calls
    )


@pytest.mark.parametrize(
    "provinces",
    [(), [], ("dong-nai", "dong-nai"), ["dong-nai", "dong-nai"], "dong-nai"],
)
def test_repository_rejects_empty_duplicate_or_non_sequence_scope(provinces: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        load_tail_history(_FakeDB([]), provinces, date(2026, 7, 22))  # type: ignore[arg-type]
