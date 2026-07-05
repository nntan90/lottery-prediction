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
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler
from src.utils.operational_date import resolve_operational_date
from src.xsmn_ensemble.resolve_provinces import get_target_provinces

# Constants for Profit Calculation (Đá vòng 3 số)
COST_DA_VONG = 328000
REVENUE_PER_VONG = 1100000


def _format_prediction_list(pairs: list[int], limit: int) -> tuple[str, list[int]]:
    """Return display text and displayed pairs for a prediction list."""
    display_pairs = pairs[:limit]
    pairs_str = "[" + ", ".join(f"{p:02d}" for p in display_pairs) + "]"
    return pairs_str, display_pairs


def _format_matched_pairs(matched: list[int], display_pairs: list[int]) -> tuple[str, list[int]]:
    """Filter matched pairs to the pairs actually shown in Telegram."""
    display_set = set(display_pairs)
    visible_matched = [p for p in matched if p in display_set]
    match_str = ", ".join(f"{p:02d}" for p in visible_matched) if visible_matched else "—"
    return match_str, visible_matched

def calculate_station_profit(region, pairs, tail_rows):
    """
    Calculate cost, revenue, profit for 'Đá vòng 3 con' (Xiên vòng 3).
    Dùng chung cho cả XSMN và XSMB theo yêu cầu của user.
    """
    tails_set = {r["tail_2d"] for r in tail_rows}
    
    # Lấy các số dự đoán (bỏ None và trùng lặp nếu có)
    valid_preds = set(p for p in pairs if p is not None)
    
    # Đếm xem có bao nhiêu số trong dự đoán xuất hiện trong kết quả
    matched_count = 0
    for p in valid_preds:
        if p in tails_set:
            matched_count += 1
            
    # Tính số vòng trúng (Xiên 2)
    # Đá 3 con -> 3 vòng xiên 2.
    # Trúng 2 con -> ăn 1 vòng
    # Trúng 3 con -> ăn 3 vòng
    if matched_count < 2:
        vong_trung = 0
    elif matched_count == 2:
        vong_trung = 1
    elif matched_count == 3:
        vong_trung = 3
    else:
        vong_trung = 0
        
    cost = COST_DA_VONG
    revenue = vong_trung * REVENUE_PER_VONG
    profit = revenue - cost
    
    # Trả về 1 record đại diện cho cả combo (dùng pair = -1)
    return [{
        "pair": -1,
        "hit_count": matched_count,
        "cost": cost,
        "revenue": revenue,
        "profit": profit
    }]





async def verify_date(db: LotteryDB, notifier: LotteryNotifier, target_date: date):
    """Verify tất cả dự đoán cho target_date."""
    date_str = target_date.strftime("%d/%m/%Y")
    print(f"\n🔍 Verifying predictions for {target_date}...")

    preds = db.supabase.table("prediction_results")\
        .select("*")\
        .eq("prediction_date", target_date.isoformat())\
        .execute().data

    if not preds:
        msg = (
            f"⚠️ <b>VERIFY PREDICTION SKIPPED</b>\n"
            f"📅 {date_str}\n"
            f"Không có prediction nào cần verify."
        )
        print(f"  ⚠️  Không có prediction nào cần verify cho {target_date}")
        await notifier.send_message(msg, config_key="verify_summary")
        return

    results_summary = []
    skipped_no_result = []
    tail_set_cache = {}  # Cache tail_set để dùng cho việc verify sub-models

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
        elif region.upper() == "XSMN":
            # Nếu province là "all" (Ensemble) -> Chỉ verify trên các đài mục tiêu của XSMN hôm nay
            # để tránh bị tính trúng sai nếu số đó ra ở đài không đánh (ví dụ Cà Mau)
            target_provs = get_target_provinces(target_date)
            if target_provs:
                tail_query = tail_query.in_("province", target_provs)

        tail_rows = tail_query.execute().data
        if not tail_rows:
            print(f"  ⚠️  {label}: không có KQXS để verify (holiday?)")
            skipped_no_result.append(label)
            continue

        tail_set = {r["tail_2d"] for r in tail_rows}
        tail_set_cache[(region, province)] = tail_set
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
                target_provs = get_target_provinces(target_date)
                if province in target_provs:
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
            "model_version": pred.get("model_version", "")
        })

    # === VERIFY SUB-MODELS in model_predictions ===
    sub_model_stats = {}
    sub_preds = None
    try:
        sub_preds = db.supabase.table("model_predictions")\
            .select("*")\
            .eq("prediction_date", target_date.isoformat())\
            .execute().data
    except Exception as e:
        error_str = str(e)
        if "PGRST205" in error_str or "model_predictions" in error_str:
            print(f"  ⚠️  Skipping sub-model verification: model_predictions table missing (run migration 06 & 08). Error: {e}")
        else:
            raise
            
    if sub_preds:
        for pred in sub_preds:
            region = pred["region"]
            province = pred["province"]
            label = f"{region}/{province or 'all'}"
            model_name = pred["model_name"]
            
            # Lấy tail_set từ cache (nếu đã lấy cho prediction_results)
            # Hoặc query bổ sung nếu chưa có (ví dụ prediction_results thiếu đài nhưng model_predictions có)
            if (region, province) not in tail_set_cache:
                t_query = db.supabase.table("tails_2d").select("tail_2d").eq("region", region).eq("draw_date", target_date.isoformat())
                if province and province != "all":
                    t_query = t_query.eq("province", province)
                elif region.upper() == "XSMN":
                    target_provs = get_target_provinces(target_date)
                    if target_provs:
                        t_query = t_query.in_("province", target_provs)
                t_rows = t_query.execute().data
                if t_rows:
                    tail_set_cache[(region, province)] = {r["tail_2d"] for r in t_rows}
                else:
                    tail_set_cache[(region, province)] = set()
            
            tail_set = tail_set_cache.get((region, province), set())
            if not tail_set:
                continue
                
            # Lấy top 5 pairs của sub-model
            pairs = [p for p in [pred.get("pair_1"), pred.get("pair_2"), pred.get("pair_3"), pred.get("pair_4"), pred.get("pair_5")] if p is not None]
            matched = [p for p in pairs if p in tail_set]
            hit = len(matched) > 0
            
            # Cập nhật db (bảng model_predictions)
            db.supabase.table("model_predictions")\
                .update({
                    "hit": hit,
                    "matched_pairs": matched
                })\
                .eq("id", pred["id"])\
                .execute()
                
            if label not in sub_model_stats:
                sub_model_stats[label] = []
            
            sub_model_stats[label].append({
                "model_name": model_name,
                "hit": hit,
                "matched": matched,
                "pairs": pairs
            })

    # Gửi Telegram report tổng hợp
    if not results_summary:
        skipped = "\n".join(f"• {label}" for label in skipped_no_result) or "• Không rõ đài"
        msg = (
            f"⚠️ <b>VERIFY PREDICTION SKIPPED</b>\n"
            f"📅 {date_str}\n"
            f"Có prediction nhưng chưa có KQXS/tails_2d để verify:\n{skipped}"
        )
        await notifier.send_message(msg, config_key="verify_summary")
        return

    total = len(results_summary)
    hits = sum(1 for r in results_summary if r["hit"])
    hit_rate = hits / total * 100 if total > 0 else 0

    province_map = XSMNCrawler().PROVINCE_MAP
    
    # Nhóm dữ liệu theo Region
    grouped_data = {}
    for r in results_summary:
        reg = r["region"].upper()
        if reg not in grouped_data:
            grouped_data[reg] = []
        grouped_data[reg].append(r)

    msg = f"📊 <b>KẾT QUẢ DỰ ĐOÁN — {date_str}</b>\n\n"

    for region, records in grouped_data.items():
        msg += f"📍 <b>{region}</b>\n"
        
        # 1. Single Model [XGBoost v3]
        msg += f"   └ 🤖 Single Model [XGBoost v3]\n"
        for r in records:
            province = r["province"]
            prov_name = province_map.get(province, province) if province else "All"
            
            # Nếu là XSMN, Single Model không có record tổng 'All' nên ta bỏ qua
            if region == "XSMN" and prov_name.lower() == "all":
                continue
                
            # Nếu là XSMB (chỉ có 'all'), ẩn chữ 'All' đi cho đẹp
            if prov_name.lower() == "all": 
                prov_display_sm = "Top 3" 
            else:
                prov_display_sm = prov_name.title()
            
            # Tìm XGBoost single/core trong model_predictions
            xgb_sm = next((sm for sm in sub_model_stats.get(r["label"], []) if sm["model_name"] in ["xgboost_single", "xgboost_core"]), None)
            
            if xgb_sm:
                display_pairs = xgb_sm["pairs"][:3] # Single model hiển thị top 3
                matched_pairs = [p for p in xgb_sm["matched"] if p in display_pairs]
                icon = "🟢" if matched_pairs else "🔴"
                pairs_str = " | ".join(f"{p:02d}" for p in display_pairs)
                match_str = " ".join(f"{p:02d}" for p in matched_pairs) if matched_pairs else "—"
                msg += f"          └ {icon} {prov_display_sm}: {pairs_str} → {match_str}\n"
            else:
                continue # Bỏ qua nếu không có dữ liệu
                
        msg += "\n"
        
        # 2. Multi-Model
        msg += f"   └ 🤖 Multi-Model\n"
        
        # 2.a) Tìm record ensemble (All)
        ensemble_record = next((r for r in records if (r["province"] == "all" or r["province"] is None) and str(r.get("model_version", "")).startswith("ensemble")), None)
        
        # Fallback cho XSMB nếu không có 'ensemble' trong model_version
        if not ensemble_record and region == "XSMB":
            ensemble_record = next((r for r in records if r["province"] is None), None)

        if ensemble_record:
            pairs_str = " | ".join(f"{p:02d}" for p in ensemble_record["pairs"])
            match_str = " ".join(f"{p:02d}" for p in ensemble_record["matched"]) if ensemble_record["matched"] else "—"
            msg += f"          └ Đồng thuận: {pairs_str} → {match_str}\n"

        # 2.b) In lịch sử các sub-models
        region_labels = [label for label in sub_model_stats.keys() if label.startswith(region + "/")]
        
        if region == "XSMB":
            label = region + "/all"
            if label in sub_model_stats:
                valid_sms = [sm for sm in sub_model_stats[label] if sm['model_name'] != "xgboost_single"]
                for sm in valid_sms:
                    model_name = sm['model_name']
                    short_map = {"frequency": "Freq", "gap_overdue": "Gap", "markov": "Markov", "xgboost_core": "XGB", "xgboost_single": "XGB", "lstm_gru": "LSTM", "lstm": "LSTM", "bayesian": "Bayes", "cyclic": "Cyclic", "stats_freq_gap": "StatsFG", "chisquare_gof": "ChiGOF", "chisquare_independence": "ChiInd"}
                    disp_name = short_map.get(model_name, model_name)
                    sm_pairs_str, display_pairs = _format_prediction_list(sm["pairs"], 3)
                    sm_match, visible_matched = _format_matched_pairs(sm["matched"], display_pairs)
                    sm_icon = "🟢" if visible_matched else "🔴"
                    msg += f"             └ {sm_icon} {disp_name}: {sm_pairs_str} → {sm_match}\n"
        else:
            # XSMN in sub-models theo từng đài
            for label in region_labels:
                prov = label.split("/", 1)[1]
                if prov.lower() == "all": continue
                
                valid_sms = [sm for sm in sub_model_stats[label] if sm['model_name'] != "xgboost_single"]
                if not valid_sms: continue
                
                prov_name = province_map.get(prov, prov).title()
                msg += f"          └ 📍 {prov_name}:\n"
                for sm in valid_sms:
                    model_name = sm['model_name']
                    short_map = {"frequency": "Freq", "gap_overdue": "Gap", "markov": "Markov", "xgboost_core": "XGB", "xgboost_single": "XGB", "lstm_gru": "LSTM", "lstm": "LSTM", "cdm": "CDM"}
                    disp_name = short_map.get(model_name, model_name)
                    sm_pairs_str, display_pairs = _format_prediction_list(sm["pairs"], 5)
                    sm_match, visible_matched = _format_matched_pairs(sm["matched"], display_pairs)
                    sm_icon = "🟢" if visible_matched else "🔴"
                    msg += f"             └ {sm_icon} {disp_name}: {sm_pairs_str} → {sm_match}\n"
                    
        msg += "\n"

    msg += f"📈 <b>Tỉ lệ: {hits}/{total} dự đoán chính xác ({hit_rate:.0f}%)</b>"

    await notifier.send_message(msg)
    print(f"\n📊 Verify done: {hits}/{total} hit ({hit_rate:.0f}%)")

    print("\n🤖 Retrain evaluation is handled by workflow 04 at 22:37 VN.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="Ngày verify (YYYY-MM-DD). Mặc định = ngày vận hành")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else resolve_operational_date()

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="verify_summary")
    await verify_date(db, notifier, target_date)


if __name__ == "__main__":
    asyncio.run(main())
