"""
predict_ensemble.py — v4.1 (7-Model XSMB + 5-Model XSMN)
Orchestration script cho Multi-Model Ensemble pipeline (XSMB & XSMN).
Chạy bởi GitHub Actions workflow: 02-predict-ensemble.yml

XSMB (v4.0 — 7 models):
  1. Frequency (multi-window)    → Top 5
  2. Gap/Overdue (weekday)       → Top 5
  3. Markov (second-order)       → Top 5
  4. XGBoost (25 features)       → Top 5
  5. BiLSTM + Attention          → Top 5
  6. Bayesian (posterior)         → Top 5
  7. Cyclic (FFT patterns)       → Top 5
  → Adaptive Borda Ensemble      → Top 3

XSMN (v3.2 — 5 models, backward compatible):
  1-5. Frequency/Gap/Markov/XGB/LSTM → Borda+CombSUM → Top 3

Flow mỗi ngày:
  1. XSMB: chạy 7 models → v4 ensemble
  2. XSMN: resolve provinces → chạy 5 models per province → v3.2 ensemble
  3. Ghi prediction_results + model_predictions
  4. Gửi Telegram notification

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

# XSMN imports (v3.2 — backward compatible)
from src.xsmn_ensemble.model_frequency import predict_frequency as xsmn_predict_frequency
from src.xsmn_ensemble.model_gap import predict_gap as xsmn_predict_gap
from src.xsmn_ensemble.model_markov import predict_markov as xsmn_predict_markov
from src.xsmn_ensemble.model_xgboost import predict_xgboost as xsmn_predict_xgboost
from src.xsmn_ensemble.model_lstm import predict_lstm as xsmn_predict_lstm
from src.xsmn_ensemble.ensemble_engine import (
    compute_global_borda,
    format_ensemble_result as xsmn_format_ensemble_result,
    format_model_prediction_log as xsmn_format_model_prediction_log,
)

# XSMB imports (v4.0 — dedicated 7-model pipeline)
from src.xsmb_ensemble.model_frequency import predict_frequency as xsmb_predict_frequency
from src.xsmb_ensemble.model_gap import predict_gap as xsmb_predict_gap
from src.xsmb_ensemble.model_markov import predict_markov as xsmb_predict_markov
from src.xsmb_ensemble.model_xgboost import predict_xgboost as xsmb_predict_xgboost
from src.xsmb_ensemble.model_lstm import predict_lstm as xsmb_predict_lstm
from src.xsmb_ensemble.model_bayesian import predict_bayesian as xsmb_predict_bayesian
from src.xsmb_ensemble.model_cyclic import predict_cyclic as xsmb_predict_cyclic
from src.xsmb_ensemble.ensemble_engine import (
    compute_xsmb_ensemble,
    format_ensemble_result as xsmb_format_ensemble_result,
    format_model_prediction_log as xsmb_format_model_prediction_log,
)
from src.xsmb_ensemble.auto_weight import compute_optimal_weights

from src.database.prediction_repo import save_prediction, save_model_prediction


TOTAL_MODELS_XSMB = 7           # v4: 7 models for XSMB
TOTAL_MODELS_PER_PROVINCE = 5   # v3.2: 5 models per XSMN province
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
        # Lấy đủ số kỳ để tìm ra N kỳ cùng thứ (thường x7 lần limit)
        fetch_limit = limit_per_province * 7 + 10
        q1 = db.supabase.table("lottery_draws") \
            .select("draw_date") \
            .eq("region", region) \
            .lt("draw_date", str(target_date)) \
            .order("draw_date", desc=True) \
            .limit(fetch_limit)
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


async def run_xsmb_models(
    db: LotteryDB,
    storage: LotteryStorage,
    target_date: date,
    tmpdir: str,
) -> list:
    """
    Chạy 7 models XSMB v4. Trả về list model_results.
    Fault-tolerant: model lỗi → ensemble vẫn chạy với model còn lại.
    """
    print(f"\n  {'='*50}")
    print(f"  📍 XSMB v4.0 — 7-Model Pipeline")
    print(f"  {'='*50}")

    model_results = []

    # ── Model A: Frequency (Multi-window) ──
    print(f"  🔹 Model A (Frequency/Multi-window)...")
    result = xsmb_predict_frequency(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "A")

    # ── Model B: Gap/Overdue (Weekday-specific) ──
    print(f"  🔹 Model B (Gap/Weekday-specific)...")
    result = xsmb_predict_gap(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "B")

    # ── Model C: Markov (Second-order) ──
    print(f"  🔹 Model C (Markov²)...")
    result = xsmb_predict_markov(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "C")

    # ── Model D: XGBoost (25 features) ──
    print(f"  🔹 Model D (XGBoost v4)...")
    result = xsmb_predict_xgboost(
        db, storage, province=None, target_date=target_date,
        region="XSMB", n_draws=XGB_FEATURE_LOOKBACK_DRAWS, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result)
    _log_model_result(result, "D")

    # ── Model E: BiLSTM + Attention ──
    print(f"  🔹 Model E (BiLSTM+Attention)...")
    result = xsmb_predict_lstm(
        db, storage=storage, province=None, target_date=target_date,
        region="XSMB", n_draws=LSTM_LOOKBACK_DRAWS, seq_len=60, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result)
    _log_model_result(result, "E")

    # ── Model F: Bayesian ──
    print(f"  🔹 Model F (Bayesian)...")
    result = xsmb_predict_bayesian(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5, region="XSMB",
    )
    model_results.append(result)
    if result["status"] == "success":
        conf = result.get("confidence", 0)
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result["top_pairs"])
        print(f"     ✅ Top 5: [{pairs_str}] (conf={conf:.2f}, {result['execution_time_ms']}ms)")
    else:
        print(f"     ❌ Error: {result['error_message']}")

    # ── Model G: Cyclic (FFT) ──
    print(f"  🔹 Model G (Cyclic/FFT)...")
    result = xsmb_predict_cyclic(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "G")

    # ── Summary ──
    success_count = sum(1 for r in model_results if r["status"] == "success")
    print(f"\n  📊 XSMB Models Active: {success_count}/{TOTAL_MODELS_XSMB}")

    # Save model_predictions logs
    for mr in model_results:
        log = xsmb_format_model_prediction_log("XSMB", None, mr, target_date)
        try:
            save_model_prediction(db, log)
        except Exception as e:
            print(f"     ⚠️  Log save failed ({mr['model_name']}): {e}")

    return model_results


async def run_xsmn_models_for_target(
    db: LotteryDB,
    storage: LotteryStorage,
    province: str | None,
    target_date: date,
    tmpdir: str,
) -> list:
    """
    Chạy 5 models XSMN (v3.2) cho 1 province. Backward compatible.
    """
    print(f"\n  {'='*50}")
    print(f"  📍 XSMN | Province: {province or 'ALL'}")
    print(f"  {'='*50}")

    model_results = []

    # ── Model 1: Frequency/Hot-Cool ──
    print(f"  🔹 Model 1 (Frequency/Hot-Cool)...")
    result_1 = xsmn_predict_frequency(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_1)
    _log_model_result(result_1, "1")

    # ── Model 2: Gap/Overdue ──
    print(f"  🔹 Model 2 (Gap/Overdue)...")
    result_2 = xsmn_predict_gap(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_2)
    _log_model_result(result_2, "2")

    # ── Model 3: Markov ──
    print(f"  🔹 Model 3 (Markov)...")
    result_3 = xsmn_predict_markov(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=5,
    )
    model_results.append(result_3)
    _log_model_result(result_3, "3")

    # ── Model 4: XGBoost ──
    print(f"  🔹 Model 4 (XGBoost)...")
    result_4 = xsmn_predict_xgboost(
        db, storage, province, target_date, region="XSMN",
        n_draws=XGB_FEATURE_LOOKBACK_DRAWS, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result_4)
    _log_model_result(result_4, "4")

    # ── Model 5: LSTM/GRU ──
    print(f"  🔹 Model 5 (LSTM/GRU)...")
    result_5 = xsmn_predict_lstm(
        db, storage=storage, province=province, target_date=target_date,
        region="XSMN", n_draws=LSTM_LOOKBACK_DRAWS, seq_len=30, top_n=5, tmpdir=tmpdir,
    )
    model_results.append(result_5)
    _log_model_result(result_5, "5")

    # ── Summary ──
    success_count = sum(1 for r in model_results if r["status"] == "success")
    print(f"\n  📊 XSMN Models Active: {success_count}/{TOTAL_MODELS_PER_PROVINCE}")

    # Save model_predictions logs
    for mr in model_results:
        log = xsmn_format_model_prediction_log("XSMN", province, mr, target_date)
        try:
            save_model_prediction(db, log)
        except Exception as e:
            print(f"     ⚠️  Log save failed ({mr['model_name']}): {e}")

    return model_results


def _log_model_result(result: dict, label: str):
    """Helper log kết quả model."""
    if result["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result["top_pairs"])
        version_str = f" [{result.get('model_version', '')}]" if result.get('model_version') else ""
        time_str = f"{result['execution_time_ms']}ms"
        n_str = f"n={result['n_draws_used']} kỳ, " if result.get('n_draws_used') else ""
        print(f"     ✅ Top 5: [{pairs_str}]{version_str} ({n_str}{time_str})")
    else:
        print(f"     ❌ Error: {result['error_message']}")


async def run_xsmb_ensemble(
    target_date: date,
    db: LotteryDB,
    storage: LotteryStorage,
    notifier: LotteryNotifier,
    tmpdir: str,
):
    """
    XSMB v4.1 — 7-Model Dedicated Ensemble Pipeline.
    """
    print(f"\n{'='*60}")
    print(f"🎯 XSMB MULTI-MODEL ENSEMBLE v4.1 (7 Models)")
    print(f"📅 Target date: {target_date} ({get_dow_label(target_date)})")
    print(f"{'='*60}")

    # Run 7 models
    all_model_results = await run_xsmb_models(db, storage, target_date, tmpdir)

    # Auto-weight tuning
    auto_weights = None
    try:
        auto_weights = compute_optimal_weights(db, lookback_days=30, region="XSMB")
        if auto_weights:
            print(f"  🔧 Auto-weights applied: {', '.join(f'{k}={v:.2f}' for k,v in auto_weights.items())}")
    except Exception as e:
        print(f"  ⚠️  Auto-weight failed (using defaults): {e}")

    # Extract Bayesian confidence
    model_confidences = {}
    for r in all_model_results:
        if r.get("model_name") == "bayesian" and r.get("status") == "success":
            model_confidences["bayesian"] = r.get("confidence", 1.0)

    # History tails (5 kỳ cùng thứ)
    recent_tails = get_recent_tails(db, "XSMB", [], target_date, limit_per_province=5)
    print(f"  📅 Lấy lịch sử 5 kỳ quay cùng thứ: {len(recent_tails)} số")

    # Extended tails (10 kỳ cho Toxic Gap)
    extended_tails = get_recent_tails(db, "XSMB", [], target_date, limit_per_province=10)
    print(f"  📅 Lấy lịch sử mở rộng 10 kỳ (Toxic Gap): {len(extended_tails)} số")

    print(f"\n  {'='*50}")
    print(f"  🌍 XSMB ENSEMBLE v4.1")
    print(f"  {'='*50}")

    ensemble_output = compute_xsmb_ensemble(
        all_model_results, recent_tails,
        weights=auto_weights,
        top_n_output=3,
        extended_tails=extended_tails,
        model_confidences=model_confidences,
    )

    if not ensemble_output["top_pairs"]:
        raise RuntimeError("XSMB ensemble produced no candidates; all sub-models failed")

    top3_str = ", ".join(f"{p:02d}({s:.2f})" for p, s in ensemble_output["top_pairs"])
    consensus_str = ", ".join(f"{p:02d}" for p in ensemble_output.get("consensus_pairs", []))

    print(f"     ✅ Top 3 statistical signals: [{top3_str}]")
    print(f"     📊 Models Active: {ensemble_output.get('models_active', 0)}/{TOTAL_MODELS_XSMB}")
    if consensus_str:
        print(f"     🤝 Consensus: [{consensus_str}]")

    # Save prediction
    prediction = xsmb_format_ensemble_result("XSMB", None, ensemble_output, target_date)
    scoring_log_msg = prediction.pop('scoring_log', '')
    save_prediction(db, prediction)

    # Telegram notification
    if prediction:
        date_str = target_date.strftime("%d/%m/%Y")
        dow_str = get_dow_label(target_date)
        active = ensemble_output.get('models_active', 0)

        msg = f"🎯 <b>BÁO CÁO PHÂN TÍCH TÍN HIỆU XSMB</b>\n"
        msg += f"📅 <b>Ngày: {date_str} ({dow_str})</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

        # XGBoost standalone
        xgb_results = [r for r in all_model_results if r.get('model_name') == 'xgboost_core' and r.get('status') == 'success']
        if xgb_results:
            xgb = xgb_results[0]
            p1, p2, p3 = [p for p, _ in xgb['top_pairs'][:3]]
            s1, s2, s3 = [s for _, s in xgb['top_pairs'][:3]]
            msg += f"🤖 <b>Single Model [XGBoost v4]</b>\n"
            msg += f"📊 Top 3: <code>{p1:02d}</code>, <code>{p2:02d}</code>, <code>{p3:02d}</code> | [{s1:.4f} | {s2:.4f} | {s3:.4f}]\n\n"

        # Ensemble
        ep1, ep2, ep3 = prediction["pair_1"], prediction["pair_2"], prediction["pair_3"]
        msg += f"🤖 <b>Multi-Model Ensemble v4.1 — 7 models</b>\n"
        msg += f"📊 Top 3 tín hiệu: <code>{ep1:02d}</code>, <code>{ep2:02d}</code>, <code>{ep3:02d}</code>\n"

        if scoring_log_msg:
            msg += f"{scoring_log_msg}\n"

        msg += f"   Models Active: {active}/{TOTAL_MODELS_XSMB}\n\n"

        # Per-model details
        msg += "📋 <b>Chi tiết các model:</b>\n"
        model_short = {
            "frequency": "Freq", "gap_overdue": "Gap", "markov": "Markov²",
            "xgboost_core": "XGB", "lstm": "BiLSTM", "bayesian": "Bayes", "cyclic": "Cyclic",
        }
        for r in all_model_results:
            if r.get("status") == "success":
                m_short = model_short.get(r["model_name"], r["model_name"])
                pairs = ", ".join(f"{p:02d}" for p, _ in r["top_pairs"][:3])
                msg += f"   🔹 {m_short}: [{pairs}]\n"

        await _send_chunked(notifier, msg, "predict_ensemble_xsmb")
        print(f"\n📱 Telegram notification sent for XSMB!")

    print(f"\n✅ XSMB Ensemble v4.1 Prediction complete!")


async def run_xsmn_ensemble(
    target_date: date,
    provinces: list,
    db: LotteryDB,
    storage: LotteryStorage,
    notifier: LotteryNotifier,
    tmpdir: str,
):
    """
    XSMN v3.2 — 5-Model Ensemble (backward compatible, unchanged).
    """
    print(f"\n{'='*60}")
    print(f"🎯 XSMN MULTI-MODEL ENSEMBLE (v3.2 — 5 Models)")
    print(f"📅 Target date: {target_date} ({get_dow_label(target_date)})")
    print(f"🏢 Target provinces ({len(provinces)}): {provinces}")
    print(f"{'='*60}")

    all_model_results = []
    provs_to_run = provinces if provinces else [None]

    for province in provs_to_run:
        results = await run_xsmn_models_for_target(db, storage, province, target_date, tmpdir)
        all_model_results.extend(results)

    # History (3 kỳ cùng thứ)
    recent_tails = get_recent_tails(db, "XSMN", provinces, target_date, limit_per_province=3)
    print(f"  📅 Lấy lịch sử 3 kỳ quay cùng thứ: {len(recent_tails)} số")

    print(f"\n  {'='*50}")
    print(f"  🌍 GLOBAL ENSEMBLE (XSMN)")
    print(f"  {'='*50}")

    ensemble_output = compute_global_borda(
        all_model_results, recent_tails, top_n_output=3, region="XSMN"
    )

    if not ensemble_output["top_pairs"]:
        raise RuntimeError("XSMN ensemble produced no candidates")

    top3_str = ", ".join(f"{p:02d}({s:.2f})" for p, s in ensemble_output["top_pairs"])
    consensus_str = ", ".join(f"{p:02d}" for p in ensemble_output.get("consensus_pairs", []))

    print(f"     ✅ Top 3: [{top3_str}]")
    print(f"     📊 Contributing: {len(ensemble_output['contributing_models'])}")
    if consensus_str:
        print(f"     🤝 Consensus: [{consensus_str}]")

    # Save
    prediction = xsmn_format_ensemble_result("XSMN", "all", ensemble_output, target_date)
    scoring_log_msg = prediction.pop('scoring_log', '')
    save_prediction(db, prediction)

    # Telegram
    if prediction:
        date_str = target_date.strftime("%d/%m/%Y")
        dow_str = get_dow_label(target_date)
        total_expected = len(provs_to_run) * TOTAL_MODELS_PER_PROVINCE
        active_count = len(ensemble_output['contributing_models'])

        msg = f"🎯 <b>BÁO CÁO PHÂN TÍCH TÍN HIỆU XSMN</b>\n"
        msg += f"📅 <b>Ngày: {date_str} ({dow_str})</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"

        ep1, ep2, ep3 = prediction["pair_1"], prediction["pair_2"], prediction["pair_3"]
        msg += f"🤖 <b>Multi-Model Ensemble v3.2</b>\n"
        msg += f"📊 Top 3: <code>{ep1:02d}</code>, <code>{ep2:02d}</code>, <code>{ep3:02d}</code>\n"

        if scoring_log_msg:
            msg += f"{scoring_log_msg}\n"

        msg += f"   Models Active: {active_count}/{total_expected}\n"

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

        await _send_chunked(notifier, msg, "predict_ensemble_xsmn")
        print(f"\n📱 Telegram notification sent for XSMN!")

    print(f"\n✅ XSMN Ensemble Prediction complete!")


async def _send_chunked(notifier, msg: str, config_key: str):
    """Send Telegram message, chunking if > 4000 chars."""
    max_len = 4000
    if len(msg) <= max_len:
        await notifier.send_message(msg, config_key=config_key)
    else:
        chunks = msg.split('\n\n')
        current_chunk = ""
        for chunk in chunks:
            if len(current_chunk) + len(chunk) + 2 > max_len:
                if current_chunk:
                    await notifier.send_message(current_chunk, config_key=config_key)
                current_chunk = chunk
            else:
                current_chunk += ("\n\n" + chunk) if current_chunk else chunk
        if current_chunk:
            await notifier.send_message(current_chunk, config_key=config_key)


async def main():
    parser = argparse.ArgumentParser(description="Multi-Model Ensemble Prediction (XSMB v4.1 + XSMN v3.2)")
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
        # XSMB — v4.1 (7 models)
        print(f"\n{'='*60}")
        print("🎯 BẮT ĐẦU CHẠY XSMB ENSEMBLE v4.1 (7 Models)")
        await run_xsmb_ensemble(target_date, db, storage, notifier, tmpdir)

        # XSMN — v3.2 (5 models, backward compatible)
        xsmn_provinces = get_target_provinces(target_date)
        if xsmn_provinces:
            await run_xsmn_ensemble(target_date, xsmn_provinces, db, storage, notifier, tmpdir)
        else:
            print(f"⚠️  Không có province nào cho XSMN ngày {target_date}")

    print(f"\n{'='*60}")
    print(f"✅ ALL ENSEMBLE PREDICTIONS COMPLETE!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
