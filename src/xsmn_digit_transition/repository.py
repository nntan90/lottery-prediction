"""Read-only keyset repository for XSMN PDA/DDT historical tails."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .domain import ProvinceCollection, validate_provinces


_SELECT_COLUMNS = "id,region,draw_date,province,prize_code,tail_2d"


def _row_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError(f"invalid draw_date from tails_2d: {value!r}")


def load_tail_history(
    db: Any,
    provinces: ProvinceCollection,
    target_date: date,
    page_size: int = 1000,
) -> list[dict]:
    """Read every pre-target XSMN tail row for the exact province scope.

    Pagination uses the immutable primary key instead of offsets, preventing the
    PostgREST row limit from truncating province history. No write operation is
    exposed by this adapter.
    """
    province_scope = validate_provinces(provinces)
    if isinstance(target_date, datetime) or not isinstance(target_date, date):
        raise TypeError("target_date must be a date")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be a positive integer")

    requested = set(province_scope)
    cutoff = target_date.isoformat()
    loaded: list[dict] = []
    last_id = 0

    while True:
        response = (
            db.supabase.table("tails_2d")
            .select(_SELECT_COLUMNS)
            .eq("region", "XSMN")
            .in_("province", list(province_scope))
            .lt("draw_date", cutoff)
            .gt("id", last_id)
            .order("id", desc=False)
            .limit(page_size)
            .execute()
        )
        page = list(response.data or [])
        if not page:
            break

        ordered_page = sorted(page, key=lambda row: int(row["id"]))
        page_ids = [int(row["id"]) for row in ordered_page]
        if page_ids[0] <= last_id or len(page_ids) != len(set(page_ids)):
            raise RuntimeError("tails_2d keyset pagination did not advance uniquely")

        for row in ordered_page:
            province = row.get("province")
            if province not in requested:
                continue
            if _row_date(row.get("draw_date")) >= target_date:
                continue
            loaded.append(dict(row))

        next_id = page_ids[-1]
        if next_id <= last_id:
            raise RuntimeError("tails_2d keyset pagination did not advance")
        last_id = next_id
        if len(page) < page_size:
            break

    return loaded


def load_regional_tail_history(
    db: Any,
    target_date: date,
    page_size: int = 1000,
) -> list[dict]:
    """Read all pre-target XSMN rows for hierarchical regional priors.

    This is intentionally a separate adapter from the exact-province loader so
    callers cannot accidentally replace province-local evidence with pooled
    history. Province identity remains present on every returned row.
    """
    if isinstance(target_date, datetime) or not isinstance(target_date, date):
        raise TypeError("target_date must be a date")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be a positive integer")

    cutoff = target_date.isoformat()
    loaded: list[dict] = []
    last_id = 0
    while True:
        response = (
            db.supabase.table("tails_2d")
            .select(_SELECT_COLUMNS)
            .eq("region", "XSMN")
            .lt("draw_date", cutoff)
            .gt("id", last_id)
            .order("id", desc=False)
            .limit(page_size)
            .execute()
        )
        page = list(response.data or [])
        if not page:
            break
        ordered_page = sorted(page, key=lambda row: int(row["id"]))
        page_ids = [int(row["id"]) for row in ordered_page]
        if page_ids[0] <= last_id or len(page_ids) != len(set(page_ids)):
            raise RuntimeError("tails_2d regional pagination did not advance uniquely")
        for row in ordered_page:
            if not row.get("province"):
                continue
            if _row_date(row.get("draw_date")) < target_date:
                loaded.append(dict(row))
        last_id = page_ids[-1]
        if len(page) < page_size:
            break
    return loaded
