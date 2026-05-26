"""
predict_ensemble.py — v3.2 (5-Model Ensemble)
Orchestration script cho Multi-Model Ensemble pipeline (XSMB & XSMN).
Chạy bởi GitHub Actions workflow: 02-predict-ensemble.yml

Models (v3.2):
  1. Frequency/Hot-Cool  → Top 5
  2. Gap/Overdue          → Top 5
  3. Markov Chain         → Top 5
  4. XGBoost              → Top 5
  5. LSTM/GRU             → Top 5
  → Ensemble (Borda + CombSUM) → Top 3

Flow mỗi ngày:
  1. Resolve TARGET_PROVINCES (2-4 đài theo thứ)
  2. Với mỗi province: chạy 5 models
  3. Global Ensemble aggregation
  4. Ghi prediction_results + model_predictions
  5. Gửi Telegram notification

Usage:
  python src/scripts/predict_ensemble.py
  python src/scripts/predict_ensemble.py --date 2026-05-07
"""

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler

from src.xsmn_ensemble.resolve_provinces import get_target_provinces, get_dow_label
from src.xsmn_ensemble.model_frequency import predict_frequency
from src.xsmn_ensemble.model_gap import predict_gap
from src.xsmn_ensemble.model_markov import predict_markov
from src.xsmn_ensemble.model_xgboost import predict_xgboost
from src.xsmn_ensemble.model_lstm import predict_lstm
from src.xsmn_ensemble.ensemble_engine import (
    compute_global_borda,
    format_ensemble_result,
    format_model_prediction_log,
)
from src.database.prediction_repo import save_prediction, save_model_prediction


TOTAL_MODELS_PER_PROVINCE = 5  # 5 models per province
RULE_MODEL_LOOKBACK_DRAWS = 180
XGB_FEATURE_LOOKBACK_DRAWS = 240
LSTM_LOOKBACK_DRAWS = 180


def get_recent_tails(db: LotteryDB, region: str, provinces: list, target_date: date, limit_per_province: int = 3) -> list:
    """Lấy lịch sử 2 số cuối trong N kỳ quay gần nhất CÙNG THỨ (cùng ngày trong tuần)."""
    tails = []
    # If no provinces (XSMB), we use [None] to iterate once
    provs_to_check = provinces if provinces else [None]
    target_weekday = target_date.weekday()

    for prov in provs_to_check:
        # Lấy 30 kỳ gần nhất để lọc ra 3 kỳ cùng thứ
        q1 = db.supabase.table("lottery_draws") \
            .select("draw_date") \
            .eq("region", region) \
            .lt("draw_date", str(target_date)) \
            .order("draw_date", desc=True) \
            .limit(30)
        q1 = q1.eq("province", prov) if prov else q1.is_("province", "null")
        draws = q1.execute()

        if not draws.data:
            continue

        # Lọc cùng thứ
        same_weekday_dates = []
        for d in draws.data:
            d_date = date.fromisoformat(d["draw_date"])
            if d_date.weekday() == target_weekday:
                same_weekday_dates.append(d["draw_date"])
                if len(same_weekday_dates) == limit_per_province:
                    break

        if not same_weekday_dates:
            continue

        # Lấy tails của các kỳ này
        q2 = db.supabase.table("tails_2d") \
            .select("tail_2d") \
            .eq("region", region) \
            .in_("draw_date", same_weekday_dates)
        q2 = q2.eq("province", prov) if prov else q2.is_("province", "null")
        t_data = q2.execute()

        if t_data.data:
            tails.extend([int(row["tail_2d"]) for row in t_data.data])

    return tails


async def run_models_for_target(
    db: LotteryDB,
    storage: LotteryStorage,
    region: str,
    province: str | None,
    target_date: date,
    tmpdir: str,
) -> list:
    """
    Chạy 5 models cho region/province và lưu logs. Trả về list model_results.
    Fault-tolerant: 1-2 model lỗi → ensemble vẫn chạy.
    """
    print(f"\n  {'='*50}")
    print(f"  📍 Region: {region} | Province: {province or 'ALL'}")
    print(f"  {'='*50}")

    model_results = []

    # ── Model 1: Frequency/Hot-Cool ──
    print(f"  🔹 Model 1 (Frequency/Hot-Cool)...")
    result_1 = predict_frequency(
        db, province, target_date, region=region,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_1)
    if result_1["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result_1["top_pairs"])
        print(f"     ✅ Top 5: [{pairs_str}] (n={result_1['n_draws_used']} kỳ, {result_1['execution_time_ms']}ms)")
    else:
        print(f"     ❌ Error: {result_1['error_message']}")

    # ── Model 2: Gap/Overdue ──
    print(f"  🔹 Model 2 (Gap/Overdue)...")
    result_2 = predict_gap(
        db, province, target_date, region=region,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_2)
    if result_2["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result_2["top_pairs"])
        print(f"     ✅ Top 5: [{pairs_str}] (n={result_2['n_draws_used']} kỳ, {result_2['execution_time_ms']}ms)")
    else:
        print(f"     ❌ Error: {result_2['error_message']}")

    # ── Model 3: Markov ──
    print(f"  🔹 Model 3 (Markov)...")
    result_3 = predict_markov(
        db, province, target_date, region=region,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_3)
    if result_3["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result_3["top_pairs"])
        print(f"     ✅ Top 5: [{pairs_str}] (n={result_3['n_draws_used']} kỳ, {result_3['execution_time_ms']}ms)")
    else:
        print(f"     ❌ Error: {result_3['error_message']}")

    # ── Model 4: XGBoost ──
    print(f"  🔹 Model 4 (XGBoost)...")
    result_4 = predict_xgboost(
        db, storage, province, target_date, region=region,
        n_draws=XGB_FEATURE_LOOKBACK_DRAWS, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result_4)
    if result_4["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result_4["top_pairs"])
        print(f"     ✅ Top 5: [{pairs_str}] ({result_4['execution_time_ms']}ms)")
    else:
        print(f"     ⚠️  XGBoost skipped: {result_4['error_message']}")

    # ── Model 5: LSTM/GRU ──
    print(f"  🔹 Model 5 (LSTM/GRU)...")
    result_5 = predict_lstm(
        db, storage=storage, province=province, target_date=target_date,
        region=region, n_draws=LSTM_LOOKBACK_DRAWS, seq_len=30, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result_5)
    if result_5["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result_5["top_pairs"])
        version_str = f" [{result_5.get('model_version', '')}]" if result_5.get('model_version') else ""
        print(f"     ✅ Top 5: [{pairs_str}]{version_str} ({result_5['execution_time_ms']}ms)")
    else:
        print(f"     ⚠️  LSTM skipped: {result_5['error_message']}")

    # ── Summary ──
    success_count = sum(1 for r in model_results if r["status"] == "success")
    print(f"\n  📊 Models Active: {success_count}/{TOTAL_MODELS_PER_PROVINCE}")

    # Save model_predictions logs
    for mr in model_results:
        log = format_model_prediction_log(region, province, mr, target_date)
        try:
            save_model_prediction(db, log)
        except Exception as e:
            print(f"     ⚠️  Log save failed ({mr['model_name']}): {e}")

    return model_results


async def run_ensemble_for_region(
    region: str,
    target_date: date,
    provinces: list,
    db: LotteryDB,
    storage: LotteryStorage,
    notifier: LotteryNotifier,
    tmpdir: str,
):
    print(f"\n{'='*60}")
    print(f"🎯 {region} MULTI-MODEL ENSEMBLE PREDICTION (v3.2 — 5 Models)")
    print(f"📅 Target date: {target_date} ({get_dow_label(target_date)})")
    if region == "XSMN":
        print(f"🏢 Target provinces ({len(provinces)}): {provinces}")
    print(f"{'='*60}")

    all_model_results = []

    # Run models per province (or just once if provinces is empty, e.g. XSMB)
    provs_to_run = provinces if provinces else [None]
    for province in provs_to_run:
        results = await run_models_for_target(db, storage, region, province, target_date, tmpdir)
        all_model_results.extend(results)

    # Lấy lịch sử 3 kỳ gần nhất
    limit_per_prov = 5 if region.upper() == "XSMB" else 3
    recent_tails = get_recent_tails(db, region, provinces, target_date, limit_per_province=limit_per_prov)
    print(f"  📅 Lấy lịch sử {limit_per_prov} kỳ quay cùng thứ: {len(recent_tails)} số")
    print(f"  🌍 GLOBAL ENSEMBLE ({region})")
    print(f"  {'='*50}")

    ensemble_output = compute_global_borda(
        all_model_results, recent_tails, top_n_output=3, region=region
    )

    if not ensemble_output["top_pairs"]:
        raise RuntimeError(
            f"{region} ensemble produced no candidates; all sub-models failed or returned empty output"
        )

    top3_str = ", ".join(f"{p:02d}({s:.2f})" for p, s in ensemble_output["top_pairs"])
    consensus_str = ", ".join(f"{p:02d}" for p in ensemble_output.get("consensus_pairs", []))
    contributing = ", ".join(ensemble_output["contributing_models"])

    print(f"     ✅ Top 3 statistical signals: [{top3_str}]")
    print(f"     📊 Contributing Models: {len(ensemble_output['contributing_models'])}")
    if consensus_str:
        print(f"     🤝 Consensus: [{consensus_str}]")

    # Lưu prediction chung với province="all" (hoặc None cho XSMB)
    save_province = "all" if region == "XSMN" else None
    prediction = format_ensemble_result(region, save_province, ensemble_output, target_date)

    scoring_log_msg = prediction.pop('scoring_log', '')

    save_prediction(db, prediction)

    # Telegram notification
    if prediction:
        date_str = target_date.strftime("%d/%m/%Y")
        dow_str = get_dow_label(target_date)

        # Count active models
        total_expected = len(provs_to_run) * TOTAL_MODELS_PER_PROVINCE
        active_count = len(ensemble_output['contributing_models'])

        msg = f"🎯 <b>BÁO CÁO PHÂN TÍCH TÍN HIỆU {region}</b>\n"
        msg += f"📅 <b>Ngày: {date_str} ({dow_str})</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

        # 1. Single Model [XGBoost v3]
        xgb_results = [r for r in all_model_results if r.get('model_name') == 'xgboost_core' and r.get('status') == 'success']
        if xgb_results:
            msg += f"🤖 <b>Single Model [XGBoost v3]</b>\n"
            for xgb_result in xgb_results:
                prov_name = xgb_result.get('province') or "ALL"
                if len(provs_to_run) > 1:
                    msg += f"🏢 Đài: {prov_name}\n"
                p1, p2, p3 = [p for p, _ in xgb_result['top_pairs'][:3]]
                s1, s2, s3 = [s for _, s in xgb_result['top_pairs'][:3]]
                msg += f"📊 Top 3 tín hiệu: <code>{p1:02d}</code>, <code>{p2:02d}</code>, <code>{p3:02d}</code> | [Score: {s1:.4f} | {s2:.4f} | {s3:.4f}]\n"
                model_ver = xgb_result.get('model_version') or 'N/A'
                msg += f" Model: {model_ver}\n"
            msg += "\n"

        # 2. Multi-Model Ensemble
        msg += f"🤖 <b>Multi-Model Ensemble v3.2 — xếp hạng xác suất tương đối</b>\n"
        
        ep1, ep2, ep3 = prediction["pair_1"], prediction["pair_2"], prediction["pair_3"]
        msg += f"📊 Top 3 tín hiệu: <code>{ep1:02d}</code>, <code>{ep2:02d}</code>, <code>{ep3:02d}</code>\n"

        if scoring_log_msg:
            msg += f"{scoring_log_msg}\n"
            
        msg += f"   Models Active: {active_count}/{total_expected}\n"

        # 3. Model details per province
        for prov in provs_to_run:
            prov_results = [r for r in all_model_results
                           if r.get("province") == prov and r.get("status") == "success"]
            if prov_results:
                prov_name = prov or "ALL"
                msg += f"📍 <b>{prov_name}</b>:\n"
                for r in prov_results:
                    m_short = {
                        "frequency": "Freq", "gap_overdue": "Gap",
                        "markov": "Markov", "xgboost_core": "XGB", "lstm": "LSTM",
                    }.get(r["model_name"], r["model_name"])
                    pairs = ", ".join(f"{p:02d}" for p, _ in r["top_pairs"][:3])
                    msg += f"   🔹 {m_short}: [{pairs}]\n"

        max_len = 4000
        if len(msg) <= max_len:
            await notifier.send_message(msg, config_key=f"predict_ensemble_{region.lower()}")
        else:
            # Chunk message if it exceeds limit
            chunks = msg.split('\n\n')
            current_chunk = ""
            for chunk in chunks:
                if len(current_chunk) + len(chunk) + 2 > max_len:
                    if current_chunk:
                        await notifier.send_message(current_chunk, config_key=f"predict_ensemble_{region.lower()}")
                    current_chunk = chunk
                else:
                    current_chunk += ("\n\n" + chunk) if current_chunk else chunk
            if current_chunk:
                await notifier.send_message(current_chunk, config_key=f"predict_ensemble_{region.lower()}")

        print(f"\n📱 Telegram notification sent for {region}!")

    print(f"\n✅ {region} Ensemble Prediction complete!")


async def main():
    parser = argparse.ArgumentParser(description="Multi-Model Ensemble Prediction (v3.2 — 5 Models)")
    parser.add_argument("--date", type=str, help="Ngày xếp hạng tín hiệu (YYYY-MM-DD). Mặc định = hôm nay")
    args = parser.parse_args()

    # Target date
    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        vn_now = datetime.utcnow() + timedelta(hours=7)
        target_date = vn_now.date()

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="predict_ensemble")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Chạy XSMB
        print(f"\n{'='*60}")
        print("🎯 BẮT ĐẦU CHẠY ENSEMBLE CHO XSMB")
        await run_ensemble_for_region("XSMB", target_date, [], db, storage, notifier, tmpdir)

        # Chạy XSMN
        xsmn_provinces = get_target_provinces(target_date)
        if xsmn_provinces:
            await run_ensemble_for_region("XSMN", target_date, xsmn_provinces, db, storage, notifier, tmpdir)
        else:
            print(f"⚠️  Không có province nào cho XSMN ngày {target_date}")

    print(f"\n{'='*60}")
    print(f"✅ ALL ENSEMBLE PREDICTIONS COMPLETE!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
