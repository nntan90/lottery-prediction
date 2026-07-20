"""Read-only data adapter for the CMR shadow predictor."""

from __future__ import annotations

from datetime import date
from typing import Any


def load_tail_history(
    db: Any,
    provinces: tuple[str, str],
    target_date: date,
    page_size: int = 1000,
) -> list[dict]:
    """Load all pre-target prize tails for exactly two XSMN provinces."""
    if len(provinces) != 2 or len(set(provinces)) != 2:
        raise ValueError("CMR requires exactly two distinct provinces")
    if page_size < 1:
        raise ValueError("page_size must be positive")

    rows: list[dict] = []
    last_id = 0
    while True:
        page = (
            db.supabase.table("tails_2d")
            .select("id,draw_date,province,prize_code,tail_2d")
            .eq("region", "XSMN")
            .in_("province", list(provinces))
            .lt("draw_date", target_date.isoformat())
            .gt("id", last_id)
            .order("id", desc=False)
            .limit(page_size)
            .execute().data
            or []
        )
        if not page:
            return rows
        rows.extend(page)
        next_id = max(int(row["id"]) for row in page)
        if next_id <= last_id:
            raise RuntimeError("tails_2d keyset pagination did not advance")
        last_id = next_id
