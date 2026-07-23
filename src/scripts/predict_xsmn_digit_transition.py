"""Run the XSMN Provincial Digit Transition predictor in shadow mode."""

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
from src.xsmn_digit_transition import DigitTransitionConfig, generate_shadow_prediction
from src.xsmn_ensemble.resolve_provinces import get_target_provinces


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run province-first XSMN Dynamic Digit Transition shadow prediction"
    )
    parser.add_argument("--date", dest="target_date", help="Target date YYYY-MM-DD")
    parser.add_argument("--provinces", help="Scheduled comma-separated province slugs")
    parser.add_argument("--output", help="Optional JSON audit output path")
    parser.add_argument("--min-transitions", type=int, default=12)
    parser.add_argument("--top-k-states", type=int, default=32)
    return parser.parse_args()


def _resolve_provinces(target_date: date, override: str | None) -> tuple[str, str]:
    scheduled = tuple(get_target_provinces(target_date))
    if len(scheduled) != 2 or len(set(scheduled)) != 2:
        raise SystemExit("DDT schedule must resolve exactly two distinct XSMN provinces")
    if override:
        requested = tuple(value.strip() for value in override.split(",") if value.strip())
        if requested != scheduled:
            raise SystemExit(
                "DDT province override must match the schedule: " + ",".join(scheduled)
            )
    return scheduled[0], scheduled[1]


def main() -> int:
    args = _parse_args()
    target_date = (
        date.fromisoformat(args.target_date)
        if args.target_date
        else datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    )
    provinces = _resolve_provinces(target_date, args.provinces)
    config = DigitTransitionConfig(
        min_transitions=args.min_transitions,
        top_k_states=args.top_k_states,
    )
    result = generate_shadow_prediction(LotteryDB(), provinces, target_date, config)
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    print(payload)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] in {"success", "uncalibrated"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
