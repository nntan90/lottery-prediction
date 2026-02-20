"""
verify_v3.py
Sau khi crawl xong kết quả thực tế, kiểm tra 3 cặp dự đoán có trúng không.

Logic: trúng nếu bất kỳ cặp nào trong [pair_1, pair_2, pair_3] ∈ TAIL_SET
TAIL_SET = tất cả 2 số cuối mọi giải của đài đó trong ngày đó

Flow:
  1. Lấy prediction_results của hôm nay (chưa verify)
  2. Với mỗi đài: build TAIL_SET từ tails_2d
  3. Check hit, ghi lại matched_pairs + tail_set
  4. Gửi Telegram: hit/miss report tổng hợp

Usage:
  python src/scripts/verify_v3.py               # hôm nay
  python src/scripts/verify_v3.py --date 2026-02-19
"""

import argparse
import asyncio
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler


async def verify_date(db: LotteryDB, notifier: LotteryNotifier, target_date: date):
    """Verify tất cả dự đoán cho target_date."""
    date_str = target_date.strftime("%d/%m/%Y")
    print(f"\n🔍 Verifying predictions for {target_date}...")

    # Lấy tất cả prediction_results chưa verify
    preds = db.supabase.table("prediction_results")\
        .select("*")\
        .eq("prediction_date", target_date.isoformat())\
        .is_("hit", "null")\
        .execute().data

    if not preds:
        print(f"  ⚠️  Không có prediction nào cần verify cho {target_date}")
        return

    results_summary = []

    for pred in preds:
        region   = pred["region"]
        province = pred["province"]
        label    = f"{region}/{province or 'all'}"

        # Build TAIL_SET từ tails_2d
        tail_query = db.supabase.table("tails_2d")\
            .select("tail_2d")\
            .eq("region", region)\
            .eq("draw_date", target_date.isoformat())

        if province:
            tail_query = tail_query.eq("province", province)
        else:
            tail_query = tail_query.is_("province", "null")

        tail_rows = tail_query.execute().data
        if not tail_rows:
            print(f"  ⚠️  {label}: không có KQXS để verify (holiday?)")
            continue

        tail_set = {r["tail_2d"] for r in tail_rows}
        pairs = [pred["pair_1"], pred["pair_2"], pred["pair_3"]]
        matched = [p for p in pairs if p in tail_set]
        hit = len(matched) > 0

        # Update DB
        db.supabase.table("prediction_results")\
            .update({
                "hit":          hit,
                "matched_pairs": matched,
                "tail_set":     list(tail_set),
                "verified_at":  "now()",
            })\
            .eq("id", pred["id"])\
            .execute()

        status = "✅ TRÚNG" if hit else "❌ Trượt"
        pairs_str = ", ".join(f"{p:02d}" for p in pairs)
        matched_str = ", ".join(f"{p:02d}" for p in matched) if matched else "—"
        print(f"  {status} | {label} | Đoán: [{pairs_str}] | Trúng: [{matched_str}] | TAIL_SET: {len(tail_set)} số")

        results_summary.append({
            "label": label,
            "hit": hit,
            "pairs": pairs,
            "matched": matched,
        })

    # Gửi Telegram report tổng hợp
    if not results_summary:
        return

    total = len(results_summary)
    hits = sum(1 for r in results_summary if r["hit"])
    hit_rate = hits / total * 100 if total > 0 else 0

    province_map = XSMNCrawler().PROVINCE_MAP
    msg = f"📊 <b>KẾT QUẢ DỰ ĐOÁN — {date_str}</b>\n\n"

    for r in results_summary:
        icon = "✅" if r["hit"] else "❌"
        pairs_str = " | ".join(f"<code>{p:02d}</code>" for p in r["pairs"])
        match_str = " ".join(f"<b>{p:02d}</b>" for p in r["matched"]) if r["matched"] else "—"
        lbl = province_map.get(r["label"].split("/")[-1], r["label"])
        msg += f"{icon} {lbl}: {pairs_str} → <code>{match_str}</code>\n"

    msg += (
        f"\n📈 <b>Tỉ lệ: {hits}/{total} đài trúng ({hit_rate:.0f}%)</b>\n"
        f"<i>Trúng = ≥ 1 cặp có trong 2 số cuối bất kỳ giải</i>"
    )

    await notifier.send_message(msg)
    print(f"\n📊 Verify done: {hits}/{total} hit ({hit_rate:.0f}%)")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="Ngày verify (YYYY-MM-DD). Mặc định = hôm nay")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()

    db = LotteryDB()
    notifier = LotteryNotifier()
    await verify_date(db, notifier, target_date)


if __name__ == "__main__":
    asyncio.run(main())
