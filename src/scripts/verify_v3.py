"""
verify_v3.py
Sau khi crawl xong kết quả thực tế, kiểm tra 3 cặp dự đoán có trúng không.

Logic Multi-Model: trúng khi ít nhất 2/3 cặp duy nhất ∈ TAIL_SET.
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
from collections import Counter
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.database.prediction_repo import SHADOW_MODEL_NAMES
from src.bot.telegram_bot import LotteryNotifier
from src.bot.verification_messages import (
    format_verification_message,
    summarize_multi_results,
)
from src.crawler.xsmn_crawler import XSMNCrawler
from src.utils.operational_date import resolve_operational_date
from src.xsmb_combo.metrics import (
    DEFAULT_COMBO_COST,
    DEFAULT_REVENUE_PER_CIRCLE,
    evaluate_combo,
)
from src.xsmn_ensemble.resolve_provinces import get_target_provinces
from src.xsmn_digit_transition.domain import (
    EXPECTED_PRIZE_COUNTS as XSMN_EXPECTED_PRIZE_COUNTS,
)

# Constants for Profit Calculation (Đá vòng 3 số)
COST_DA_VONG = DEFAULT_COMBO_COST
REVENUE_PER_VONG = DEFAULT_REVENUE_PER_CIRCLE


def _prediction_scope(prediction: dict) -> str:
    """Classify stored rows without changing their persistence contract."""
    model_version = str(prediction.get("model_version") or "")
    return "ensemble" if model_version.startswith("ensemble") else "single"


def _unique_valid_pairs(values) -> list[int]:
    """Return valid pairs once, preserving prediction order."""
    pairs: list[int] = []
    for value in values:
        try:
            pair = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= pair <= 99 and pair not in pairs:
            pairs.append(pair)
    return pairs


def _evaluate_prediction_pairs(
    pairs,
    tail_set,
    *,
    model_scope: str,
) -> dict:
    """Evaluate a stored Top 3, using the canonical combo KPI for ensembles."""
    try:
        evaluation = evaluate_combo(pairs, tail_set)
        hit = evaluation.combo_hit if model_scope == "ensemble" else (
            evaluation.hit_count >= 2
        )
        return {
            "hit": hit,
            "combo_hit": evaluation.combo_hit if model_scope == "ensemble" else False,
            "hit_count": evaluation.hit_count,
            "matched": list(evaluation.matched_pairs),
            "validation_error": None,
        }
    except (TypeError, ValueError) as exc:
        valid_pairs = _unique_valid_pairs(pairs)
        matched = [pair for pair in valid_pairs if pair in tail_set]
        return {
            "hit": False,
            "combo_hit": False,
            "hit_count": len(matched),
            "matched": matched,
            "validation_error": str(exc),
        }


def calculate_station_profit(region, pairs, tail_rows):
    """
    Calculate cost, revenue, profit for 'Đá vòng 3 con' (Xiên vòng 3).
    Dùng chung cho cả XSMN và XSMB theo yêu cầu của user.
    """
    del region  # Kept in the public signature for backward compatibility.
    tail_set = {
        int(row["tail_2d"])
        for row in tail_rows
        if row.get("tail_2d") is not None
    }
    try:
        evaluation = evaluate_combo(
            pairs,
            tail_set,
            cost=COST_DA_VONG,
            revenue_per_circle=REVENUE_PER_VONG,
        )
        hit_count = evaluation.hit_count
        revenue = evaluation.revenue
        profit = evaluation.profit
    except (TypeError, ValueError) as exc:
        # Invalid or duplicate Top 3 must never create a false winning payout.
        valid_pairs = _unique_valid_pairs(pairs)
        hit_count = sum(pair in tail_set for pair in valid_pairs)
        revenue = 0
        profit = -COST_DA_VONG
        print(f"  ⚠️  Invalid combo for profit; forced miss: {exc}")

    return [{
        "pair": -1,
        "hit_count": hit_count,
        "cost": COST_DA_VONG,
        "revenue": revenue,
        "profit": profit
    }]


def _shadow_provinces(prediction: dict, target_date: date) -> list[str]:
    """Resolve the exact merged province scope stored by a shadow run."""
    metadata = prediction.get("run_metadata")
    if isinstance(metadata, dict):
        provinces = metadata.get("provinces")
        if isinstance(provinces, list):
            normalized = [str(value) for value in provinces if value]
            if len(normalized) == 2 and len(set(normalized)) == 2:
                return normalized
    return get_target_provinces(target_date)


def _is_complete_xsmn_shadow_scope(
    rows: list[dict],
    provinces: tuple[str, ...],
) -> bool:
    """Require every expected prize row for every stored shadow province."""
    counts: dict[str, Counter[str]] = {
        province: Counter() for province in provinces
    }
    for row in rows:
        province = str(row.get("province") or "")
        prize_code = str(row.get("prize_code") or "")
        if province in counts and prize_code in XSMN_EXPECTED_PRIZE_COUNTS:
            counts[province][prize_code] += 1
    return all(
        all(
            counts[province][code] == expected
            for code, expected in XSMN_EXPECTED_PRIZE_COUNTS.items()
        )
        for province in provinces
    )


def _update_shadow_verification(
    db: LotteryDB,
    prediction_id: int,
    payload: dict,
) -> None:
    """Persist shadow verification with a legacy-schema safe fallback."""
    try:
        db.supabase.table("model_predictions").update(payload) \
            .eq("id", prediction_id).execute()
    except Exception as exc:
        error = str(exc).lower()
        new_fields = ("hit_count", "combo_hit", "verified_at")
        if not any(field in error for field in new_fields):
            raise
        print(
            "  ⚠️  Shadow verification migration pending; "
            "saving legacy hit/matched fields only"
        )
        legacy = {
            "hit": payload["hit"],
            "matched_pairs": payload["matched_pairs"],
        }
        db.supabase.table("model_predictions").update(legacy) \
            .eq("id", prediction_id).execute()





async def verify_date(db: LotteryDB, notifier: LotteryNotifier, target_date: date):
    """Verify tất cả dự đoán cho target_date."""
    date_str = target_date.strftime("%d/%m/%Y")
    print(f"\n🔍 Verifying predictions for {target_date}...")

    preds = db.supabase.table("prediction_results")\
        .select("*")\
        .eq("prediction_date", target_date.isoformat())\
        .execute().data

    no_main_predictions = not preds
    if no_main_predictions:
        print(f"  ⚠️  Không có prediction nào cần verify cho {target_date}")

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

        tail_set = {int(r["tail_2d"]) for r in tail_rows}
        tail_set_cache[(region, province, ())] = tail_set
        pairs = [pred["pair_1"], pred["pair_2"], pred["pair_3"]]
        model_scope = _prediction_scope(pred)
        evaluation = _evaluate_prediction_pairs(
            pairs,
            tail_set,
            model_scope=model_scope,
        )
        matched = evaluation["matched"]
        hit = evaluation["hit"]
        if evaluation["validation_error"]:
            print(
                f"  ⚠️  {label}: invalid stored Top 3; "
                f"forced miss: {evaluation['validation_error']}"
            )

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
        pairs_str = ", ".join(
            f"{int(pair):02d}"
            if pair is not None and str(pair).lstrip("-").isdigit()
            else "—"
            for pair in pairs
        )
        matched_str = ", ".join(f"{p:02d}" for p in matched) if matched else "—"
        print(f"  {status} | {label} | Đoán: [{pairs_str}] | Trúng: [{matched_str}] | TAIL_SET: {len(tail_set)} số")

        results_summary.append({
            "label": label,
            "region": region,
            "province": province,
            "hit": hit,
            "combo_hit": evaluation["combo_hit"],
            "hit_count": evaluation["hit_count"],
            "pairs": pairs,
            "matched": matched,
            "model_version": pred.get("model_version", ""),
            "model_scope": model_scope,
            "validation_error": evaluation["validation_error"],
        })

    # === VERIFY SUB-MODELS in model_predictions ===
    sub_model_stats = {}
    shadow_results = []
    sub_preds = None
    try:
        sub_preds = db.supabase.table("model_predictions")\
            .select("*")\
            .eq("prediction_date", target_date.isoformat())\
            .execute().data
    except Exception as e:
        error_str = str(e)
        if "PGRST205" in error_str.upper() or "42P01" in error_str.upper():
            print(f"  ⚠️  Skipping sub-model verification: model_predictions table missing (run migration 06 & 08). Error: {e}")
        else:
            raise
            
    if sub_preds:
        for pred in sub_preds:
            region = pred["region"]
            province = pred["province"]
            label = f"{region}/{province or 'all'}"
            model_name = pred["model_name"]
            is_shadow = (
                pred.get("prediction_mode") == "shadow"
                or model_name in SHADOW_MODEL_NAMES
            )
            province_scope = tuple(
                _shadow_provinces(pred, target_date)
                if is_shadow and region.upper() == "XSMN"
                else ()
            )
            shadow_status = str(pred.get("status") or "error")
            if is_shadow and shadow_status not in {"success", "uncalibrated"}:
                shadow_results.append({
                    "model_name": model_name,
                    "status": shadow_status,
                    "reason": pred.get("error_message"),
                    "pairs": [
                        pred.get("pair_1"),
                        pred.get("pair_2"),
                        pred.get("pair_3"),
                    ],
                    "matched": [],
                    "hit_count": None,
                    "combo_hit": None,
                    "verification_status": "no_prediction",
                })
                continue
            cache_key = (region, province, province_scope)
            
            # Lấy tail_set từ cache (nếu đã lấy cho prediction_results)
            # Hoặc query bổ sung nếu chưa có (ví dụ prediction_results thiếu đài nhưng model_predictions có)
            if cache_key not in tail_set_cache:
                t_query = db.supabase.table("tails_2d").select("province,prize_code,tail_2d").eq("region", region).eq("draw_date", target_date.isoformat())
                if province and province != "all":
                    t_query = t_query.eq("province", province)
                elif region.upper() == "XSMN":
                    target_provs = (
                        _shadow_provinces(pred, target_date)
                        if is_shadow
                        else get_target_provinces(target_date)
                    )
                    if target_provs:
                        t_query = t_query.in_("province", target_provs)
                t_rows = t_query.execute().data
                complete_scope = True
                if (
                    is_shadow
                    and region.upper() == "XSMN"
                    and province_scope
                ):
                    complete_scope = _is_complete_xsmn_shadow_scope(
                        t_rows,
                        province_scope,
                    )
                if is_shadow and region.upper() == "XSMB":
                    # XSMB has exactly 27 prize rows.  Do not finalize a
                    # shadow verdict while the crawler is still ingesting a
                    # partial draw.
                    complete_scope = len(t_rows) == 27
                if t_rows and complete_scope:
                    tail_set_cache[cache_key] = {
                        int(r["tail_2d"]) for r in t_rows
                    }
                else:
                    tail_set_cache[cache_key] = set()
            
            tail_set = tail_set_cache.get(cache_key, set())
            if not tail_set:
                if is_shadow:
                    shadow_results.append({
                        "model_name": model_name,
                        "status": pred.get("status"),
                        "reason": pred.get("error_message"),
                        "pairs": [
                            pred.get("pair_1"),
                            pred.get("pair_2"),
                            pred.get("pair_3"),
                        ],
                        "matched": [],
                        "hit_count": None,
                        "combo_hit": None,
                        "verification_status": "pending_results",
                    })
                continue

            if is_shadow:
                pairs = [
                    pred.get("pair_1"),
                    pred.get("pair_2"),
                    pred.get("pair_3"),
                ]
                evaluation = _evaluate_prediction_pairs(
                    pairs,
                    tail_set,
                    model_scope="ensemble",
                )
                shadow_hit = evaluation["hit_count"] > 0
                _update_shadow_verification(
                    db,
                    pred["id"],
                    {
                        "hit": shadow_hit,
                        "matched_pairs": evaluation["matched"],
                        "hit_count": evaluation["hit_count"],
                        "combo_hit": evaluation["combo_hit"],
                        "verified_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                shadow_results.append({
                    "model_name": model_name,
                    "status": shadow_status,
                    "reason": pred.get("error_message"),
                    "pairs": pairs,
                    "matched": evaluation["matched"],
                    "hit_count": evaluation["hit_count"],
                    "combo_hit": evaluation["combo_hit"],
                    "validation_error": evaluation["validation_error"],
                    "verification_status": "verified",
                })
                continue
                
            # Lấy top 5 pairs của sub-model
            pairs = _unique_valid_pairs([
                pred.get("pair_1"),
                pred.get("pair_2"),
                pred.get("pair_3"),
                pred.get("pair_4"),
                pred.get("pair_5"),
            ])
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

    if sub_preds is not None:
        present_shadows = {
            str(result.get("model_name"))
            for result in shadow_results
        }
        for model_name in sorted(SHADOW_MODEL_NAMES):
            if model_name in present_shadows:
                continue
            shadow_results.append({
                "model_name": model_name,
                "status": "missing",
                "reason": "không có prediction đã lưu",
                "pairs": [],
                "matched": [],
                "hit_count": None,
                "combo_hit": None,
                "verification_status": "no_prediction",
            })

    # Gửi Telegram report tổng hợp
    if not results_summary and not shadow_results:
        if no_main_predictions:
            msg = (
                f"⚠️ <b>VERIFY PREDICTION SKIPPED</b>\n"
                f"📅 {date_str}\n"
                f"Không có prediction hoặc shadow nào cần verify."
            )
            await notifier.send_message(msg, config_key="verify_summary")
            return
        skipped = "\n".join(f"• {label}" for label in skipped_no_result) or "• Không rõ đài"
        msg = (
            f"⚠️ <b>VERIFY PREDICTION SKIPPED</b>\n"
            f"📅 {date_str}\n"
            f"Có prediction nhưng chưa có KQXS/tails_2d để verify:\n{skipped}"
        )
        await notifier.send_message(msg, config_key="verify_summary")
        return

    province_map = XSMNCrawler().PROVINCE_MAP
    hits, total, hit_rate = summarize_multi_results(results_summary)
    msg = format_verification_message(
        date_str,
        results_summary,
        sub_model_stats,
        province_map,
        get_target_provinces(target_date),
        shadow_results,
    )

    await notifier.send_message(msg)
    print(
        f"\n📊 Verify done: Multi-Model {hits}/{total} "
        f"hit >=2/3 ({hit_rate:.0f}%)"
    )

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
