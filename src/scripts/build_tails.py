"""
build_tails.py
Trích xuất 2 số cuối từ lottery_draws và lưu vào bảng tails_2d.
Chạy: nightly sau crawl (bước cuối trong 01-daily-crawl.yml)
      hoặc thủ công để backfill toàn bộ lịch sử.

Usage:
  python src/scripts/build_tails.py            # xử lý ngày hôm nay (nightly)
  python src/scripts/build_tails.py --backfill  # toàn bộ lịch sử
  python src/scripts/build_tails.py --date 2026-02-19
"""

import argparse
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.features.tail_extractor import extract_tails_from_draw
from src.utils.operational_date import resolve_operational_date


def get_existing_draw_ids(db: LotteryDB, draw_ids: list) -> set:
    """Lấy các draw_id đã có trong tails_2d để skip."""
    if not draw_ids:
        return set()
    result = db.supabase.table("tails_2d")\
        .select("draw_id")\
        .in_("draw_id", draw_ids)\
        .execute()
    return {r["draw_id"] for r in result.data}


def build_tails_for_date(db: LotteryDB, target_date: date) -> int:
    """Xử lý tất cả bản ghi lottery_draws cho ngày target_date."""
    draws = db.supabase.table("lottery_draws")\
        .select("*")\
        .eq("draw_date", target_date.isoformat())\
        .execute().data

    if not draws:
        return 0

    draw_ids = [d["id"] for d in draws]
    already_done = get_existing_draw_ids(db, draw_ids)

    inserted = 0
    label = draws[0].get("region", "?")

    for draw in draws:
        if draw["id"] in already_done:
            continue  # skip nếu đã có

        tails = extract_tails_from_draw(draw)
        if not tails:
            continue

        try:
            db.supabase.table("tails_2d").insert(tails).execute()
            inserted += len(tails)
        except Exception as e:
            print(f"  ❌ Draw {draw['id']}: {e}")

    if inserted > 0:
        print(f"  ✅ {target_date} | {label} | {len(draws)} draws | {inserted} tails")
    return inserted



def main():
    parser = argparse.ArgumentParser(description="Build tails_2d from lottery_draws")
    parser.add_argument("--backfill", action="store_true", help="Backfill toàn bộ lịch sử")
    parser.add_argument("--date", type=str, help="Xử lý ngày cụ thể (YYYY-MM-DD)")
    args = parser.parse_args()

    db = LotteryDB()
    total = 0

    if args.date:
        target = date.fromisoformat(args.date)
        print(f"📅 Building tails for {target}...")
        total = build_tails_for_date(db, target)

    elif args.backfill:
        print("🔄 Backfilling all tails_2d from lottery_draws...")

        # Lấy set draw_ids đã có trong tails_2d
        done_ids_result = db.supabase.table("tails_2d").select("draw_id").execute()
        done_ids = {r["draw_id"] for r in done_ids_result.data}
        print(f"  tails_2d hiện có: {len(done_ids)} draw_ids đã xử lý")

        # Lấy tất cả draws chưa có trong tails_2d (theo batches 1000)
        all_draws = []
        offset = 0
        while True:
            batch = db.supabase.table("lottery_draws")\
                .select("id,draw_date,region")\
                .order("draw_date")\
                .range(offset, offset + 999)\
                .execute().data
            if not batch:
                break
            all_draws.extend(batch)
            offset += 1000
            if len(batch) < 1000:
                break

        pending = [d for d in all_draws if d["id"] not in done_ids]
        print(f"  lottery_draws total: {len(all_draws)} | Cần xử lý: {len(pending)}")

        # Nhóm theo date để insert cùng lúc
        from itertools import groupby
        for draw_date, group in groupby(pending, key=lambda d: d["draw_date"]):
            total += build_tails_for_date(db, date.fromisoformat(draw_date))

    else:
        # Nightly: xử lý ngày vận hành, chống job schedule bị delay qua nửa đêm VN.
        target = resolve_operational_date()
        print(f"🌙 Nightly build tails for {target}...")
        total = build_tails_for_date(db, target)

    print(f"\n✅ Done. Total tails inserted/updated: {total}")



if __name__ == "__main__":
    main()
