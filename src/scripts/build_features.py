"""
build_features.py
Tính pair_features cho 100 cặp (00–99) từ dữ liệu tails_2d.
Chạy: sau build_tails.py trong pipeline nightly (01-daily-crawl.yml)
      hoặc thủ công để backfill.

Usage:
  python src/scripts/build_features.py             # ngày hôm nay
  python src/scripts/build_features.py --backfill  # toàn bộ lịch sử
  python src/scripts/build_features.py --date 2026-02-19
"""

import argparse
import sys
import os
from datetime import date, timedelta
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.features.feature_builder import (
    _extract_history,
    build_features_for_day,
)
from src.features.tail_extractor import build_tail_set


# Danh sách (region, province) cần tính feature
STATIONS = [
    ("XSMB", None),
    ("XSMN", "tp-hcm"),
    ("XSMN", "dong-thap"),
    ("XSMN", "ca-mau"),
    ("XSMN", "ben-tre"),
    ("XSMN", "vung-tau"),
    ("XSMN", "bac-lieu"),
    ("XSMN", "dong-nai"),
    ("XSMN", "can-tho"),
    ("XSMN", "soc-trang"),
    ("XSMN", "tay-ninh"),
    ("XSMN", "an-giang"),
    ("XSMN", "binh-thuan"),
    ("XSMN", "vinh-long"),
    ("XSMN", "binh-duong"),
    ("XSMN", "tra-vinh"),
    ("XSMN", "long-an"),
    ("XSMN", "binh-phuoc"),
    ("XSMN", "hau-giang"),
    ("XSMN", "tien-giang"),
    ("XSMN", "kien-giang"),
    ("XSMN", "da-lat"),
]

HISTORY_DAYS = 120  # Lấy 120 kỳ lịch sử để tính features


def build_features_for_station(
    db: LotteryDB,
    region: str,
    province: str | None,
    target_date: date,
) -> int:
    """
    Tính và upsert pair_features cho (region, province) tại target_date.
    Trả về số rows inserted.
    """
    label = f"{region}/{province or 'all'}"

    # Lấy lịch sử tails_2d (tất cả ngày TRƯỚC target_date)
    query = db.supabase.table("tails_2d")\
        .select("draw_date,tail_2d")\
        .eq("region", region)\
        .lt("draw_date", target_date.isoformat())\
        .order("draw_date", desc=True)\
        .limit(HISTORY_DAYS * 30)  # 30 tail/kỳ trung bình

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    history_rows = query.execute().data
    history_df = _extract_history(history_rows, max_rows=HISTORY_DAYS)

    if len(history_df) < 10:
        print(f"  ⚠️  {label}: không đủ lịch sử ({len(history_df)} kỳ) cho {target_date}")
        return 0

    # Lấy TAIL_SET của target_date (để tính label hit)
    tail_query = db.supabase.table("tails_2d")\
        .select("tail_2d")\
        .eq("region", region)\
        .eq("draw_date", target_date.isoformat())

    if province:
        tail_query = tail_query.eq("province", province)
    else:
        tail_query = tail_query.is_("province", "null")

    tail_rows = tail_query.execute().data
    target_tail_set = frozenset(r["tail_2d"] for r in tail_rows) if tail_rows else None

    # Tính 100 feature rows
    feature_rows = build_features_for_day(target_date, history_df, target_tail_set)

    # Thêm region/province vào mỗi row
    for row in feature_rows:
        row["region"] = region
        row["province"] = province

    # Upsert vào pair_features
    try:
        db.supabase.table("pair_features").upsert(
            feature_rows,
            on_conflict="feature_date,region,province,pair"
        ).execute()
        print(f"  ✅ {label} | {target_date} | 100 pairs | history={len(history_df)}kỳ | tail_set={len(target_tail_set) if target_tail_set else 0}")
        return 100
    except Exception as e:
        print(f"  ❌ {label} | {target_date}: {e}")
        return 0


def get_available_dates(db: LotteryDB, region: str, province: str | None) -> List[str]:
    """Lấy danh sách ngày có tails_2d cho 1 station (có pagination)."""
    all_dates = set()
    offset = 0
    while True:
        query = db.supabase.table("tails_2d")\
            .select("draw_date")\
            .eq("region", region)\
            .order("draw_date")\
            .range(offset, offset + 999)

        if province:
            query = query.eq("province", province)
        else:
            query = query.is_("province", "null")

        batch = query.execute().data
        if not batch:
            break
        for row in batch:
            all_dates.add(row["draw_date"])
        if len(batch) < 1000:
            break
        offset += 1000

    return sorted(all_dates)


def main():
    parser = argparse.ArgumentParser(description="Build pair_features from tails_2d")
    parser.add_argument("--backfill", action="store_true", help="Backfill toàn bộ lịch sử")
    parser.add_argument("--date", type=str, help="Ngày cụ thể (YYYY-MM-DD)")
    args = parser.parse_args()

    db = LotteryDB()
    total = 0

    if args.date:
        target = date.fromisoformat(args.date)
        print(f"📅 Building features for {target}...")
        for region, province in STATIONS:
            total += build_features_for_station(db, region, province, target)

    elif args.backfill:
        print("🔄 Backfilling all pair_features...")
        for region, province in STATIONS:
            label = f"{region}/{province or 'all'}"
            available_dates = get_available_dates(db, region, province)
            print(f"\n📊 {label}: {len(available_dates)} ngày cần xử lý")
            for d_str in available_dates:
                total += build_features_for_station(db, region, province, date.fromisoformat(d_str))

    else:
        target = date.today()
        print(f"🌙 Nightly build features for {target}...")
        for region, province in STATIONS:
            total += build_features_for_station(db, region, province, target)

    print(f"\n✅ Done. Total feature rows inserted/updated: {total}")


if __name__ == "__main__":
    main()
