"""Read-only Supabase adapters for relationship history and replay inputs."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional, Sequence

from .domain import MatchedOccasion, build_matched_occasions, validate_provinces


TAIL_COLUMNS = "id,region,draw_date,province,prize_code,tail_2d"
MODEL_COLUMNS = (
    "id,prediction_date,region,province,model_name,model_version,status,"
    "prediction_mode,created_at,"
    "pair_1,pair_2,pair_3,pair_4,pair_5,"
    "score_1,score_2,score_3,score_4,score_5"
)


def _validate_page_size(page_size: int) -> None:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be a positive integer")


def load_tail_rows(
    db: Any,
    provinces: Sequence[str],
    target_date: date,
    *,
    page_size: int = 1000,
    draw_dates: Optional[Sequence[str]] = None,
) -> list[dict]:
    """Load every exact-scope tail row before target via keyset pagination."""
    province_scope = validate_provinces(provinces)
    _validate_page_size(page_size)
    loaded: list[dict] = []
    last_id = 0
    while True:
        query = (
            db.supabase.table("tails_2d")
            .select(TAIL_COLUMNS)
            .eq("region", "XSMN")
            .in_("province", list(province_scope))
            .lt("draw_date", target_date.isoformat())
            .gt("id", last_id)
            .order("id", desc=False)
            .limit(page_size)
        )
        if draw_dates is not None:
            if not draw_dates:
                return []
            query = query.in_("draw_date", list(draw_dates))
        page = query.execute().data or []
        if not page:
            break
        ordered = sorted((dict(row) for row in page), key=lambda row: int(row["id"]))
        ids = [int(row["id"]) for row in ordered]
        if ids[0] <= last_id or len(ids) != len(set(ids)):
            raise RuntimeError("tails_2d relationship pagination did not advance")
        loaded.extend(ordered)
        last_id = ids[-1]
    return loaded


def load_matched_history(
    db: Any,
    provinces: Sequence[str],
    target_date: date,
    *,
    limit: int,
    page_size: int = 1000,
) -> tuple[MatchedOccasion, ...]:
    """Return the latest complete matched occurrences in chronological order."""
    province_scope = validate_provinces(provinces)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    _validate_page_size(page_size)

    # ``lottery_draws`` is a lightweight date index. Expand it incrementally
    # until enough *complete* matched occasions exist, then read tails only
    # for bounded common-date batches instead of scanning all tail history.
    date_page_size = min(max(limit, 32), 1000)
    date_sets: dict[str, set[str]] = {
        province: set() for province in province_scope
    }
    date_cursors = {
        province: target_date.isoformat() for province in province_scope
    }
    exhausted = {province: False for province in province_scope}
    loaded_common_dates: set[str] = set()
    tail_rows: list[dict] = []
    matched: tuple[MatchedOccasion, ...] = ()

    while True:
        common_dates = set.intersection(
            *(date_sets[province] for province in province_scope)
        )
        available = sorted(
            common_dates - loaded_common_dates,
            reverse=True,
        )
        if available:
            remaining = max(limit - len(matched), 1)
            date_batch = available[: max(remaining * 2, 8)]
            tail_rows.extend(
                load_tail_rows(
                    db,
                    province_scope,
                    target_date,
                    page_size=page_size,
                    draw_dates=date_batch,
                )
            )
            loaded_common_dates.update(date_batch)
            matched = build_matched_occasions(
                tail_rows,
                province_scope,
                target_date,
                limit=limit,
            )
            if len(matched) >= limit:
                return matched
            continue

        if all(exhausted.values()):
            return matched

        progressed = False
        for province in province_scope:
            if exhausted[province]:
                continue
            page = (
                db.supabase.table("lottery_draws")
                .select("draw_date")
                .eq("region", "XSMN")
                .eq("province", province)
                .lt("draw_date", date_cursors[province])
                .order("draw_date", desc=True)
                .limit(date_page_size)
                .execute()
                .data
                or []
            )
            page_dates = sorted(
                {
                    str(row["draw_date"])
                    for row in page
                    if row.get("draw_date")
                },
                reverse=True,
            )
            if not page_dates:
                exhausted[province] = True
                continue
            next_cursor = page_dates[-1]
            if next_cursor >= date_cursors[province]:
                raise RuntimeError(
                    "lottery_draws relationship pagination did not advance"
                )
            date_sets[province].update(page_dates)
            date_cursors[province] = next_cursor
            progressed = True
        if not progressed and not all(exhausted.values()):
            raise RuntimeError(
                "lottery_draws relationship pagination made no progress"
            )


def load_archived_model_predictions(
    db: Any,
    provinces: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    page_size: int = 1000,
) -> list[dict]:
    """Load archived same-day Top-5 inputs for leakage-safe replay."""
    province_scope = validate_provinces(provinces)
    _validate_page_size(page_size)
    if end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    loaded: list[dict] = []
    last_id = 0
    while True:
        page = (
            db.supabase.table("model_predictions")
            .select(MODEL_COLUMNS)
            .eq("region", "XSMN")
            .in_("province", list(province_scope))
            .gte("prediction_date", start_date.isoformat())
            .lte("prediction_date", end_date.isoformat())
            .gt("id", last_id)
            .order("id", desc=False)
            .limit(page_size)
            .execute()
            .data
            or []
        )
        if not page:
            break
        ordered = sorted((dict(row) for row in page), key=lambda row: int(row["id"]))
        ids = [int(row["id"]) for row in ordered]
        if ids[0] <= last_id or len(ids) != len(set(ids)):
            raise RuntimeError("model_predictions pagination did not advance")
        loaded.extend(
            row
            for row in ordered
            if str(row.get("prediction_mode") or "").lower() != "shadow"
        )
        last_id = ids[-1]
    return loaded


def load_relationship_replay_data(
    db: Any,
    provinces: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    history_before: Optional[date] = None,
) -> tuple[list[dict], list[dict]]:
    """Convenience reader for archived model rows and all pre-end tail rows."""
    model_rows = load_archived_model_predictions(
        db,
        provinces,
        start_date,
        end_date,
    )
    tail_rows = load_tail_rows(
        db,
        provinces,
        history_before or (end_date + timedelta(days=1)),
    )
    return model_rows, tail_rows
