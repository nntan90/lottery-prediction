"""Run Coupled Motif Retrieval as an isolated XSMN shadow prediction."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.xsmn_coupled import CMRConfig, predict_coupled
from src.xsmn_coupled.repository import load_tail_history
from src.xsmn_ensemble.resolve_provinces import XSMN_ENSEMBLE_SCHEDULE


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run XSMN Coupled Motif Retrieval in shadow mode")
    parser.add_argument("--date", dest="target_date", help="Target date in YYYY-MM-DD")
    parser.add_argument("--provinces", help="Exactly two comma-separated province slugs")
    parser.add_argument("--neighbors", type=int, default=25, help="Maximum nearest cases")
    parser.add_argument("--min-neighbors", type=int, default=8, help="Minimum evidence cases")
    parser.add_argument("--alpha", type=float, default=5.0, help="Bayesian shrinkage strength")
    parser.add_argument("--context-weight", type=float, default=0.10, help="Anchor-age similarity weight")
    parser.add_argument("--output", help="Optional JSON output path; stdout is always emitted")
    return parser.parse_args()


def _resolve_provinces(target_date: date, override: str | None) -> tuple[str, str]:
    scheduled = XSMN_ENSEMBLE_SCHEDULE.get(target_date.weekday(), [])
    if len(scheduled) != 2 or len(set(scheduled)) != 2:
        raise SystemExit("CMR schedule must resolve exactly two distinct target provinces")
    if override:
        requested = [value.strip() for value in override.split(",") if value.strip()]
        if requested != scheduled:
            expected = ",".join(scheduled)
            raise SystemExit(f"CMR province override must match the schedule: {expected}")
    return scheduled[0], scheduled[1]


def main() -> int:
    args = _parse_args()
    target_date = (
        date.fromisoformat(args.target_date)
        if args.target_date
        else datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    )
    provinces = _resolve_provinces(target_date, args.provinces)

    config = CMRConfig(
        top_k=args.neighbors,
        min_neighbors=args.min_neighbors,
        shrinkage_alpha=args.alpha,
        context_weight=args.context_weight,
    )
    db = LotteryDB()
    rows = load_tail_history(db, provinces, target_date)
    result = predict_coupled(rows, provinces, target_date, config)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    print(payload)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
