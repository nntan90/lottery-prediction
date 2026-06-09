"""
Preflight guard for the daily crawl workflow.

Scheduled GitHub Actions can be delayed or missed. The workflow is scheduled
multiple times as a fallback, and this guard prevents duplicate full runs once
the operational date has already been crawled and verified.
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.utils.operational_date import resolve_operational_date, vietnam_now


def _write_output(name: str, value: str) -> None:
    """Write a GitHub Actions output, or print it when running locally."""
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"{name}={value}")


def _resolve_target_date() -> date:
    target = os.getenv("TARGET_DATE", "").strip()
    if target:
        return date.fromisoformat(target)
    return resolve_operational_date(vietnam_now())


def _has_successful_crawl(db, target: date, region: str) -> bool:
    rows = (
        db.supabase.table("crawler_logs")
        .select("id")
        .eq("crawl_date", target.isoformat())
        .eq("region", region)
        .eq("status", "success")
        .limit(1)
        .execute()
        .data
    )
    return bool(rows)


def _has_verified_predictions(db, target: date) -> bool:
    rows = (
        db.supabase.table("prediction_results")
        .select("id,verified_at")
        .eq("prediction_date", target.isoformat())
        .execute()
        .data
    )
    if not rows:
        return False
    return all(row.get("verified_at") for row in rows)


def main() -> None:
    target = _resolve_target_date()
    force_run = os.getenv("FORCE_RUN", "").lower() in {"1", "true", "yes"}

    _write_output("target_date", target.isoformat())

    if force_run:
        _write_output("should_run", "true")
        _write_output("reason", "manual_dispatch")
        print(f"Manual dispatch: forcing daily crawl for {target}")
        return

    from src.database.supabase_client import LotteryDB

    db = LotteryDB()
    xsmb_done = _has_successful_crawl(db, target, "XSMB")
    xsmn_done = _has_successful_crawl(db, target, "XSMN")
    verified_done = _has_verified_predictions(db, target)
    complete = xsmb_done and xsmn_done and verified_done

    _write_output("should_run", "false" if complete else "true")
    reason = (
        "already_complete"
        if complete
        else f"pending:xsmb={xsmb_done},xsmn={xsmn_done},verified={verified_done}"
    )
    _write_output("reason", reason)

    print(f"Daily crawl preflight for {target}: {reason}")


if __name__ == "__main__":
    main()
