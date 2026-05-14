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
import time
from datetime import date, timedelta
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.features.feature_builder import (
    _extract_history,
    build_features_for_day,
)
from src.features.tail_extractor import build_tail_set
from src.utils.operational_date import resolve_operational_date

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

HISTORY_DRAWS = 240  # Lấy 240 kỳ lịch sử để giảm noise khi tính features
MAX_RETRIES  = 5    # Số lần retry khi gặp lỗi kết nối
RETRY_DELAY  = 3.0  # Giây chờ giữa mỗi retry (exponential backoff)


def _execute_with_retry(fn_factory, label: str = "", max_retries: int = MAX_RETRIES):
    """
    Gọi fn_factory(db) với retry logic + TẠO LẠI LotteryDB khi gặp lỗi kết nối.
    Mỗi lần retry tạo 1 LotteryDB() mới → fresh HTTP client, tránh dùng lại connection đứt.

    Args:
        fn_factory: callable(db: LotteryDB) → result
        label: mô tả cho log
        max_retries: số lần retry tối đa
    """
    delay = RETRY_DELAY
    for attempt in range(max_retries + 1):
        db = LotteryDB()   # ← Tạo mới hoàn toàn mỗi attempt → fresh HTTP/2 connection
        try:
            return fn_factory(db)
        except Exception as e:
            err_str = str(e)
            is_network_error = any(kw in err_str.lower() for kw in [
                "server disconnected", "connection", "timeout",
                "remoteerror", "remoteprotocol", "httpcore",
                "httpx", "network", "reset", "broken pipe",
            ])
            if not is_network_error or attempt == max_retries:
                raise
            print(f"  ⚠️  [{label}] Lỗi kết nối (attempt {attempt+1}/{max_retries}), "
                  f"tạo lại DB client sau {delay:.0f}s: {err_str[:80]}")
            time.sleep(delay)
            delay = min(delay * 2, 60)  # exponential backoff, tối đa 60s


def build_features_for_station(
    region: str,
    province: str | None,
    target_date: date,
) -> int:
    """
    Tính và upsert pair_features cho (region, province) tại target_date.
    Mỗi bước DB đều tạo LotteryDB() mới để tránh dùng connection bị đứt.
    Trả về số rows inserted.
    """
    label = f"{region}/{province or 'all'}"

    # Bước 1: Lấy lịch sử tails_2d
    def _fetch_history(db):
        query = db.supabase.table("tails_2d")\
            .select("draw_date,tail_2d")\
            .eq("region", region)\
            .lt("draw_date", target_date.isoformat())\
            .order("draw_date", desc=True)\
            .limit(HISTORY_DRAWS * 30)
        if province:
            query = query.eq("province", province)
        else:
            query = query.is_("province", "null")
        return query.execute().data

    history_rows = _execute_with_retry(_fetch_history, f"{label}/history")
    history_df = _extract_history(history_rows, max_rows=HISTORY_DRAWS)

    if len(history_df) < 10:
        print(f"  ⚠️  {label}: không đủ lịch sử ({len(history_df)} kỳ) cho {target_date}")
        return 0

    # Bước 2: Lấy TAIL_SET của target_date
    def _fetch_tail(db):
        tail_query = db.supabase.table("tails_2d")\
            .select("tail_2d")\
            .eq("region", region)\
            .eq("draw_date", target_date.isoformat())
        if province:
            tail_query = tail_query.eq("province", province)
        else:
            tail_query = tail_query.is_("province", "null")
        return tail_query.execute().data

    tail_rows = _execute_with_retry(_fetch_tail, f"{label}/tail")
    target_tail_set = frozenset(r["tail_2d"] for r in tail_rows) if tail_rows else None

    # Bước 3: Tính 100 feature rows
    feature_rows = build_features_for_day(target_date, history_df, target_tail_set)
    for row in feature_rows:
        row["region"] = region
        row["province"] = province

    # Bước 4: Upsert vào pair_features (mỗi upsert dùng DB client mới)
    def _upsert(db):
        db.supabase.table("pair_features").upsert(
            feature_rows,
            on_conflict="feature_date,region,province,pair"
        ).execute()

    try:
        _execute_with_retry(_upsert, f"{label}/upsert")
        tail_cnt = len(target_tail_set) if target_tail_set else 0
        print(f"  ✅ {label} | {target_date} | 100 pairs | history={len(history_df)}kỳ | tail_set={tail_cnt}")
        return 100
    except Exception as e:
        print(f"  ❌ {label} | {target_date}: {e}")
        return 0


def get_available_dates(region: str, province: str | None) -> List[str]:
    """Lấy danh sách ngày có tails_2d cho 1 station (có pagination + retry reconnect)."""
    all_dates = set()
    offset = 0

    while True:
        current_offset = offset  # capture for closure

        def _fetch(db, off=current_offset):
            query = db.supabase.table("tails_2d")\
                .select("draw_date")\
                .eq("region", region)\
                .order("draw_date")\
                .range(off, off + 999)
            if province:
                query = query.eq("province", province)
            else:
                query = query.is_("province", "null")
            return query.execute().data

        batch = _execute_with_retry(_fetch, f"{region}/{province or 'all'}/dates")
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
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Giây chờ giữa mỗi ngày khi backfill (default: 0.5s)")
    args = parser.parse_args()

    total = 0

    if args.date:
        target = date.fromisoformat(args.date)
        print(f"📅 Building features for {target}...")
        for region, province in STATIONS:
            total += build_features_for_station(region, province, target)

    elif args.backfill:
        print(f"🔄 Backfilling all pair_features... (delay={args.delay}s/ngày, retry={MAX_RETRIES}x, reconnect on fail)")
        for region, province in STATIONS:
            label = f"{region}/{province or 'all'}"
            available_dates = get_available_dates(region, province)
            print(f"\n📊 {label}: {len(available_dates)} ngày cần xử lý")

            for i, d_str in enumerate(available_dates):
                total += build_features_for_station(region, province, date.fromisoformat(d_str))
                if args.delay > 0 and i < len(available_dates) - 1:
                    time.sleep(args.delay)

    else:
        target = resolve_operational_date()
        print(f"🌙 Nightly build features for {target}...")
        for region, province in STATIONS:
            total += build_features_for_station(region, province, target)

    print(f"\n✅ Done. Total feature rows inserted/updated: {total}")


if __name__ == "__main__":
    main()
