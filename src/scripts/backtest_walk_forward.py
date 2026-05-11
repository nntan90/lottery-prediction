"""
Run a walk-forward backtest from saved prediction_results.

Usage:
  python src/scripts/backtest_walk_forward.py --from-date 2026-01-01 --to-date 2026-05-10
  python src/scripts/backtest_walk_forward.py --region XSMN --days 90
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.analytics.backtest import build_backtest_report, format_backtest_report, parse_iso_date
from src.database.supabase_client import LotteryDB


def _query_range(
    db: LotteryDB,
    table: str,
    date_col: str,
    start: date,
    end: date,
    region: str | None = None,
) -> list[dict]:
    q = (
        db.supabase.table(table)
        .select("*")
        .gte(date_col, start.isoformat())
        .lte(date_col, end.isoformat())
    )
    if region:
        q = q.eq("region", region)
    return q.execute().data or []


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward backtest for 2D tail predictions")
    parser.add_argument("--from-date", type=str, help="Start date, YYYY-MM-DD")
    parser.add_argument("--to-date", type=str, help="End date, YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=90, help="Lookback days if --from-date is omitted")
    parser.add_argument("--region", choices=["XSMB", "XSMN"], help="Optional region filter")
    parser.add_argument("--output-json", type=str, help="Optional report JSON path")
    args = parser.parse_args()

    end = parse_iso_date(args.to_date) if args.to_date else date.today()
    start = parse_iso_date(args.from_date) if args.from_date else end - timedelta(days=args.days)
    if start > end:
        raise SystemExit("--from-date must be <= --to-date")

    db = LotteryDB()
    predictions = _query_range(db, "prediction_results", "prediction_date", start, end, args.region)
    model_predictions = _query_range(db, "model_predictions", "prediction_date", start, end, args.region)

    profit_region = args.region.lower() if args.region else None
    profit_rows = _query_range(db, "profit_tracking", "prediction_date", start, end, profit_region)

    report = build_backtest_report(predictions, model_predictions, profit_rows)
    print(format_backtest_report(report))

    if args.output_json:
        os.makedirs(os.path.dirname(args.output_json) or ".", exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nReport JSON saved: {args.output_json}")


if __name__ == "__main__":
    main()
