"""
predict_single_model.py
Generate single-model XGBoost predictions for the daily preview.

This is the restored v3 single-model path. It intentionally coexists with the
ensemble workflow:
  - XSMN province rows are saved to prediction_results for per-province verify.
  - XSMB is saved only when no ensemble row exists yet, so a delayed single run
    cannot overwrite the main ensemble prediction for the same date.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd

from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.prediction_repo import save_model_prediction, save_prediction
from src.database.supabase_client import LotteryDB
from src.features.feature_builder import _extract_history, build_features_for_day
from src.models.xgb_model import FEATURE_COLS, LotteryXGB
from src.utils.storage import LotteryStorage
from src.xsmn_ensemble.ensemble_engine import format_model_prediction_log


HISTORY_DRAWS = 240
_model_cache: dict[str, LotteryXGB] = {}


def get_active_model(
    db: LotteryDB,
    region: str,
    province: str | None,
    weekday: int | None = None,
) -> dict | None:
    """Load the newest active XGBoost model, preferring same-weekday models."""

    def _base_query():
        q = (
            db.supabase.table("model_registry")
            .select("*")
            .eq("region", region)
            .eq("status", "active")
            .order("trained_at", desc=True)
            .limit(1)
        )
        return q.eq("province", province) if province else q.is_("province", "null")

    if weekday is not None:
        result = _base_query().eq("weekday", weekday).execute()
        if result.data:
            return result.data[0]

    result = _base_query().is_("weekday", "null").execute()
    return result.data[0] if result.data else None


def load_model_cached(storage: LotteryStorage, file_path: str, tmpdir: str) -> LotteryXGB | None:
    """Download and load a model once per run."""
    if file_path in _model_cache:
        return _model_cache[file_path]

    local_path = os.path.join(tmpdir, os.path.basename(file_path))
    if not storage.download_model(file_path, local_path):
        return None

    model = LotteryXGB()
    model.load(local_path)
    _model_cache[file_path] = model
    return model


def get_feature_df(
    db: LotteryDB,
    region: str,
    province: str | None,
    target_date: date,
) -> pd.DataFrame | None:
    """Load prebuilt pair_features, or build them on the fly from tails_2d."""
    query = (
        db.supabase.table("pair_features")
        .select(",".join(FEATURE_COLS + ["pair"]))
        .eq("feature_date", target_date.isoformat())
        .eq("region", region)
        .order("pair")
    )
    query = query.eq("province", province) if province else query.is_("province", "null")

    result = query.execute()
    if result.data and len(result.data) == 100:
        return pd.DataFrame(result.data)

    print("  ⚠️  pair_features missing; building on the fly from tails_2d...")
    q2 = (
        db.supabase.table("tails_2d")
        .select("draw_date,tail_2d")
        .eq("region", region)
        .lt("draw_date", target_date.isoformat())
        .order("draw_date", desc=True)
        .limit(HISTORY_DRAWS * 30)
    )
    q2 = q2.eq("province", province) if province else q2.is_("province", "null")

    history_rows = q2.execute().data
    if not history_rows:
        return None

    history_df = _extract_history(history_rows, max_rows=HISTORY_DRAWS)
    if len(history_df) < 10:
        return None

    return pd.DataFrame(build_features_for_day(target_date, history_df, target_tail_set=None))


def _ensemble_prediction_exists(db: LotteryDB, target_date: date, region: str, province: str | None) -> bool:
    """Return True when the main ensemble row already exists for the same key."""
    q = (
        db.supabase.table("prediction_results")
        .select("model_version,ensemble_method")
        .eq("prediction_date", target_date.isoformat())
        .eq("region", region)
    )
    q = q.eq("province", province) if province else q.is_("province", "null")

    rows = q.execute().data
    if not rows:
        return False
    row = rows[0]
    return bool(row.get("ensemble_method")) or str(row.get("model_version", "")).startswith("ensemble")


def _save_single_prediction(db: LotteryDB, prediction: dict) -> None:
    """Save single-model output without overwriting an existing XSMB ensemble row."""
    region = prediction["region"]
    province = prediction.get("province")
    target_date = date.fromisoformat(prediction["prediction_date"])

    if region == "XSMB" and _ensemble_prediction_exists(db, target_date, region, province):
        print("  ↪️  Skip saving XSMB single row: ensemble prediction already exists")
        return

    save_prediction(db, prediction)


def _log_single_model_output(db: LotteryDB, prediction: dict, registry: dict, target_date: date) -> None:
    """Write Top-3 XGBoost output to model_predictions when migration 06 exists."""
    model_result = {
        "model_name": "xgboost_single",
        "model_type": "ml",
        "province": prediction.get("province"),
        "top_pairs": [
            (prediction["pair_1"], prediction.get("prob_1")),
            (prediction["pair_2"], prediction.get("prob_2")),
            (prediction["pair_3"], prediction.get("prob_3")),
        ],
        "n_draws_used": 0,
        "model_version": registry.get("version"),
        "status": "success",
        "error_message": None,
        "execution_time_ms": 0,
    }
    log = format_model_prediction_log(prediction["region"], prediction.get("province"), model_result, target_date)
    save_model_prediction(db, log)


async def predict_station(
    db: LotteryDB,
    storage: LotteryStorage,
    region: str,
    province: str | None,
    target_date: date,
    tmpdir: str,
) -> dict | None:
    """Predict Top-3 pairs for one station with the active XGBoost model."""
    label = f"{region}/{province or 'all'}"
    weekday = target_date.weekday()

    registry = get_active_model(db, region, province, weekday)
    if not registry:
        print(f"  ⚠️  {label}: no active XGBoost model")
        return None

    model = load_model_cached(storage, registry["file_path"], tmpdir)
    if model is None:
        print(f"  ❌ {label}: model download/load failed")
        return None

    feat_df = get_feature_df(db, region, province, target_date)
    if feat_df is None or len(feat_df) < 100:
        print(f"  ❌ {label}: not enough feature data")
        return None

    top3 = model.top_k(feat_df, k=3)
    pair_1, prob_1 = top3[0]
    pair_2, prob_2 = top3[1]
    pair_3, prob_3 = top3[2]

    model_wd = registry.get("weekday")
    wd_note = f" [wd={model_wd}]" if model_wd is not None else " [legacy]"
    print(
        f"  ✅ {label}{wd_note}: "
        f"[{pair_1:02d}, {pair_2:02d}, {pair_3:02d}] "
        f"scores=[{prob_1:.3f}, {prob_2:.3f}, {prob_3:.3f}]"
    )

    prediction = {
        "prediction_date": target_date.isoformat(),
        "region": region,
        "province": province,
        "pair_1": pair_1,
        "pair_2": pair_2,
        "pair_3": pair_3,
        "prob_1": prob_1,
        "prob_2": prob_2,
        "prob_3": prob_3,
        "model_version": registry["version"],
        "hit": None,
        "matched_pairs": None,
        "tail_set": None,
    }
    _log_single_model_output(db, prediction, registry, target_date)
    return prediction


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily single-model XGBoost predictions")
    parser.add_argument("--date", type=str, help="Prediction date (YYYY-MM-DD). Default = current VN date")
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        vn_now = datetime.utcnow() + timedelta(hours=7)
        target_date = vn_now.date()
        print(f"🌅 Single-model predicting for {target_date} (VN time: {vn_now.strftime('%H:%M')})")

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="predict_single")
    crawler = XSMNCrawler()

    all_results: dict[str, list | dict | None] = {"XSMB": None, "XSMN": []}
    date_str = target_date.strftime("%d/%m/%Y")

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\n📅 Single-model prediction for {target_date}")
        print("=" * 50)

        print("\n🎯 XSMB:")
        xsmb_result = await predict_station(db, storage, "XSMB", None, target_date, tmpdir)
        if xsmb_result:
            all_results["XSMB"] = xsmb_result
            _save_single_prediction(db, xsmb_result)

        provinces = crawler.get_provinces_for_date(target_date)
        print(f"\n🎯 XSMN ({len(provinces)} đài): {provinces}")
        for province in provinces:
            result = await predict_station(db, storage, "XSMN", province, target_date, tmpdir)
            if result:
                all_results["XSMN"].append(result)
                _save_single_prediction(db, result)

    if all_results["XSMB"]:
        r = all_results["XSMB"]
        pairs_str = f"<code>{r['pair_1']:02d}</code>, <code>{r['pair_2']:02d}</code>, <code>{r['pair_3']:02d}</code>"
        msg = (
            f"🎯 <b>TÍN HIỆU SINGLE XSMB — {date_str}</b>\n"
            f"<i>🤖 Single Model [XGBoost v3]</i>\n\n"
            f"📊 Top 3 tín hiệu: {pairs_str}\n"
            f"Score: <code>{r['prob_1']:.4f}</code> | <code>{r['prob_2']:.4f}</code> | <code>{r['prob_3']:.4f}</code>\n"
            f"<i>Model: {r['model_version']}</i>"
        )
        await notifier.send_message(msg, config_key="predict_single_xsmb")

    if all_results["XSMN"]:
        province_map = crawler.PROVINCE_MAP
        msg = (
            f"🎯 <b>TÍN HIỆU SINGLE XSMN — {date_str}</b>\n"
            f"<i>🤖 Single Model [XGBoost v3] — per-province</i>\n\n"
        )
        for r in all_results["XSMN"]:
            pname = province_map.get(r["province"], r["province"])
            pairs_str = f"<code>{r['pair_1']:02d}</code>, <code>{r['pair_2']:02d}</code>, <code>{r['pair_3']:02d}</code>"
            msg += f"📍 <b>{pname}</b>: {pairs_str}\n"
            msg += f"   <i>Model: {r['model_version']}</i>\n"
        await notifier.send_message(msg, config_key="predict_single_xsmn")

    print("\n✅ Single-model prediction complete!")


if __name__ == "__main__":
    asyncio.run(main())
