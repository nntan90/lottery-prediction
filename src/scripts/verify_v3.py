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
from src.agent.master_retrain_agent import run_agent

# Constants for Profit Calculation
XSMN_TIER_POINTS = [3, 2, 2]
XSMN_COST_PER_POINT = 14000
XSMN_REVENUE_PER_HIT_POINT = 70000

XSMB_TIER_POINTS = [2, 1, 1]
XSMB_COST_PER_POINT = 23000
XSMB_REVENUE_PER_HIT_POINT = 80000

def calculate_station_profit(region, pairs, tail_rows):
    """Calculate cost, revenue, profit, and hit details for a station per pair."""
    region_lower = region.lower()
    if region_lower == "xsmn":
        tie_points = XSMN_TIER_POINTS
        cost_per_pt = XSMN_COST_PER_POINT
        rev_per_pt = XSMN_REVENUE_PER_HIT_POINT
    elif region_lower == "xsmb":
        tie_points = XSMB_TIER_POINTS
        cost_per_pt = XSMB_COST_PER_POINT
        rev_per_pt = XSMB_REVENUE_PER_HIT_POINT
    else:
        return []

    results = []
    tails_list = [r["tail_2d"] for r in tail_rows]

    for idx, pair in enumerate(pairs):
        if pair is None:
            continue
            
        cost = tie_points[idx] * cost_per_pt
        occurrences = tails_list.count(pair)
        revenue = tie_points[idx] * occurrences * rev_per_pt
        profit = revenue - cost
        
        results.append({
            "pair": int(pair),
            "hit_count": occurrences,
            "cost": cost,
            "revenue": revenue,
            "profit": profit
        })

    return results



# Tỉnh hợp lệ theo ngày trong tuần của người dùng (0=Monday, 6=Sunday)
VALID_XSMN_STATIONS = {
    0: ["tphcm", "dong_thap"],    # Thứ 2
    1: ["ben_tre", "vung_tau"],   # Thứ 3
    2: ["dong_nai", "can_tho"],   # Thứ 4
    3: ["tay_ninh", "an_giang"],  # Thứ 5
    4: ["vinh_long", "binh_duong"],# Thứ 6
    5: ["tphcm", "long_an"],      # Thứ 7
    6: ["tien_giang", "kien_giang"],# Chủ nhật
}

async def verify_date(db: LotteryDB, notifier: LotteryNotifier, target_date: date):
    """Verify tất cả dự đoán cho target_date."""
    date_str = target_date.strftime("%d/%m/%Y")
    print(f"\n🔍 Verifying predictions for {target_date}...")

    preds = db.supabase.table("prediction_results")\
        .select("*")\
        .eq("prediction_date", target_date.isoformat())\
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

        if province and province != "all":
            tail_query = tail_query.eq("province", province)
        # Nếu province là None hoặc "all" -> Lấy tất cả province của region đó trong ngày

        tail_rows = tail_query.execute().data
        if not tail_rows:
            print(f"  ⚠️  {label}: không có KQXS để verify (holiday?)")
            continue

        tail_set = {r["tail_2d"] for r in tail_rows}
        pairs = [pred["pair_1"], pred["pair_2"], pred["pair_3"]]
        matched = [p for p in pairs if p in tail_set]
        hit = len(matched) > 0

        # Update DB for prediction_results
        db.supabase.table("prediction_results")\
            .update({
                "hit":          hit,
                "matched_pairs": matched,
                "tail_set":     list(tail_set),
                "verified_at":  "now()",
            })\
            .eq("id", pred["id"])\
            .execute()

        # --- Calculate Profit & Tracking ---
        # Kiểm tra xem đài này có nằm trong danh sách cần track theo ngày không
        is_tracking_enabled = False
        weekday = target_date.weekday()
        region_lower = region.lower()

        if region_lower == "xsmb":
            is_tracking_enabled = True # XSMB always tracked
        elif region_lower == "xsmn":
            if province and province != "all":
                mapped_prov = province.replace("-", "_").replace("tp_hcm", "tphcm")
                if mapped_prov in VALID_XSMN_STATIONS.get(weekday, []):
                    is_tracking_enabled = True
            else:
                is_tracking_enabled = True # Global ensemble XSMN (province=None or "all") luôn tracking

        if is_tracking_enabled:
            pair_results = calculate_station_profit(region, pairs, tail_rows)

            for p_res in pair_results:
                # Upsert profit_tracking per pair
                profit_data = {
                    "prediction_date": target_date.isoformat(),
                    "region": region.lower(),
                    "province": province if province else "all",
                    "pair": p_res["pair"],
                    "hit_count": p_res["hit_count"],
                    "cost": p_res["cost"],
                    "revenue": p_res["revenue"],
                    "profit": p_res["profit"]
                }

                existing = db.supabase.table("profit_tracking")\
                    .select("id")\
                    .eq("prediction_date", target_date.isoformat())\
                    .eq("region", region.lower())\
                    .eq("province", province if province else "all")\
                    .eq("pair", p_res["pair"])\
                    .execute().data
                
                if existing:
                    db.supabase.table("profit_tracking").update(profit_data).eq("id", existing[0]["id"]).execute()
                else:
                    db.supabase.table("profit_tracking").insert(profit_data).execute()

        status = "✅ TRÚNG" if hit else "❌ Trượt"
        pairs_str = ", ".join(f"{p:02d}" for p in pairs)
        matched_str = ", ".join(f"{p:02d}" for p in matched) if matched else "—"
        print(f"  {status} | {label} | Đoán: [{pairs_str}] | Trúng: [{matched_str}] | TAIL_SET: {len(tail_set)} số")

        results_summary.append({
            "label": label,
            "region": region,
            "province": province,
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

    # 🤖 Master Retrain Agent — chạy sau khi verify xong
    # Wrapped trong try/except: nếu agent crash, verify pipeline không bị ảnh hưởng
    try:
        await run_agent(db, notifier, results_summary, target_date)
    except Exception as agent_err:
        print(f"\n⚠️  Master Retrain Agent lỗi (không ảnh hưởng verify): {agent_err}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="Ngày verify (YYYY-MM-DD). Mặc định = hôm nay")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="verify_summary")
    await verify_date(db, notifier, target_date)


if __name__ == "__main__":
    asyncio.run(main())
