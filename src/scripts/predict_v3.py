"""
predict_v3.py
Dự đoán 3 cặp số (00–99) cho mỗi đài hôm nay bằng XGBoost.

Flow:
  1. Xác định các đài cần dự đoán hôm nay
  2. Với mỗi đài: load model .pkl từ Supabase Storage (cache local)
  3. Build feature vector 100 cặp cho ngày D
  4. top_k(k=3) → 3 cặp số
  5. Upsert vào prediction_results
  6. Gửi 1 Telegram XSMB + 1 Telegram XSMN gộp tất cả đài

Usage:
  python src/scripts/predict_v3.py
  python src/scripts/predict_v3.py --date 2026-02-19  # dry-run ngày cụ thể
"""

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd

from src.database.supabase_client import LotteryDB
from src.models.xgb_model import LotteryXGB, FEATURE_COLS
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler
from src.features.feature_builder import _extract_history, build_features_for_day

HISTORY_DAYS = 100  # số kỳ lịch sử để build feature

# Cache model đã download trong session
_model_cache: dict = {}


def get_active_model(db: LotteryDB, region: str, province: str | None, weekday: int | None = None) -> dict | None:
    """Lấy model active mới nhất từ model_registry.
    
    Ưu tiên:
    1. Model weekday-specific (weekday == target weekday)
    2. Fallback: model cũ không có weekday (weekday IS NULL)
    """
    def _build_base_query():
        q = db.supabase.table("model_registry")\
            .select("*")\
            .eq("region", region)\
            .eq("status", "active")\
            .order("trained_at", desc=True)\
            .limit(1)
        if province:
            q = q.eq("province", province)
        else:
            q = q.is_("province", "null")
        return q

    # 1. Tìm model weekday-specific
    if weekday is not None:
        result = _build_base_query().eq("weekday", weekday).execute()
        if result.data:
            return result.data[0]

    # 2. Fallback: model không có weekday (legacy)
    result = _build_base_query().is_("weekday", "null").execute()
    return result.data[0] if result.data else None


def load_model_cached(
    storage: LotteryStorage,
    file_path: str,
    tmpdir: str,
) -> LotteryXGB | None:
    """Download và load model, cache trong session."""
    if file_path in _model_cache:
        return _model_cache[file_path]

    local_path = os.path.join(tmpdir, os.path.basename(file_path))
    if not storage.download_model(file_path, local_path):
        return None

    model = LotteryXGB()
    model.load(local_path)
    _model_cache[file_path] = model
    return model


def get_feature_df(db: LotteryDB, region: str, province: str | None, target_date: date) -> pd.DataFrame | None:
    """
    Ưu tiên lấy từ pair_features DB (đã build sẵn).
    Fallback: tính on-the-fly từ tails_2d.
    """
    # Try DB first
    query = db.supabase.table("pair_features")\
        .select(",".join(FEATURE_COLS + ["pair"]))\
        .eq("feature_date", target_date.isoformat())\
        .eq("region", region)\
        .order("pair")

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    result = query.execute()
    if result.data and len(result.data) == 100:
        return pd.DataFrame(result.data)

    # Fallback: build on-the-fly
    print(f"  ⚠️  pair_features không có sẵn, tính on-the-fly...")
    q2 = db.supabase.table("tails_2d")\
        .select("draw_date,tail_2d")\
        .eq("region", region)\
        .lt("draw_date", target_date.isoformat())\
        .order("draw_date", desc=True)\
        .limit(HISTORY_DAYS * 30)

    if province:
        q2 = q2.eq("province", province)
    else:
        q2 = q2.is_("province", "null")

    history_rows = q2.execute().data
    if not history_rows:
        return None

    history_df = _extract_history(history_rows, max_rows=HISTORY_DAYS)
    if len(history_df) < 5:
        return None

    feature_rows = build_features_for_day(target_date, history_df, target_tail_set=None)
    return pd.DataFrame(feature_rows)


async def predict_station(
    db: LotteryDB,
    storage: LotteryStorage,
    region: str,
    province: str | None,
    target_date: date,
    tmpdir: str,
) -> dict | None:
    """
    Predict top-3 pairs cho 1 station.
    Returns: {'pair_1': int, 'pair_2': int, 'pair_3': int, 'prob_1': float, ...}
    """
    label = f"{region}/{province or 'all'}"
    weekday = target_date.weekday()  # 0=Mon..6=Sun

    # 1. Lấy model (uu tien weekday-specific, fallback legacy)
    registry = get_active_model(db, region, province, weekday)
    if not registry:
        print(f"  ⚠️  {label}: không có model active")
        return None

    model_wd = registry.get("weekday")
    wd_note = f" [wd={model_wd}]" if model_wd is not None else " [legacy]"
    model = load_model_cached(storage, registry["file_path"], tmpdir)
    if model is None:
        print(f"  ❌ {label}: không load được model{wd_note}")
        return None

    # 2. Lấy feature vector
    feat_df = get_feature_df(db, region, province, target_date)
    if feat_df is None or len(feat_df) < 100:
        print(f"  ❌ {label}: không đủ feature data")
        return None

    # 3. Predict top-3
    top3 = model.top_k(feat_df, k=3)
    pair_1, prob_1 = top3[0]
    pair_2, prob_2 = top3[1]
    pair_3, prob_3 = top3[2]

    print(f"  ✅ {label}{wd_note}: [{pair_1:02d}, {pair_2:02d}, {pair_3:02d}] probs=[{prob_1:.3f}, {prob_2:.3f}, {prob_3:.3f}]")

    return {
        "prediction_date": target_date.isoformat(),
        "region":   region,
        "province": province,
        "pair_1":   pair_1,
        "pair_2":   pair_2,
        "pair_3":   pair_3,
        "prob_1":   prob_1,
        "prob_2":   prob_2,
        "prob_3":   prob_3,
        "model_version": registry["version"],
        "hit":      None,
        "matched_pairs": None,
        "tail_set": None,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, help="Ngày dự đoán (YYYY-MM-DD). Mặc định = hôm nay")
    args = parser.parse_args()

    # Xác định ngày dự đoán
    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        vn_now = datetime.utcnow() + timedelta(hours=7)
        target_date = vn_now.date()
        print(f"🌅 Predicting for {target_date} (VN time: {vn_now.strftime('%H:%M')})")

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier()

    all_results = {"XSMB": None, "XSMN": []}
    date_str = target_date.strftime("%d/%m/%Y")

    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\n📅 Predicting for {target_date}")
        print("=" * 50)

        # 1. XSMB
        print("\n🎯 XSMB:")
        xsmb_result = await predict_station(db, storage, "XSMB", None, target_date, tmpdir)
        if xsmb_result:
            all_results["XSMB"] = xsmb_result
            db.supabase.table("prediction_results").upsert(
                xsmb_result, on_conflict="prediction_date,region,province"
            ).execute()

        # 2. XSMN — các đài hôm nay
        crawler = XSMNCrawler()
        provinces = crawler.get_provinces_for_date(target_date)
        print(f"\n🎯 XSMN ({len(provinces)} đài): {provinces}")

        for province in provinces:
            result = await predict_station(db, storage, "XSMN", province, target_date, tmpdir)
            if result:
                all_results["XSMN"].append(result)
                db.supabase.table("prediction_results").upsert(
                    result, on_conflict="prediction_date,region,province"
                ).execute()

    # 3. Gửi Telegram
    # XSMB
    if all_results["XSMB"]:
        r = all_results["XSMB"]
        pairs_str = f"<code>{r['pair_1']:02d}</code>, <code>{r['pair_2']:02d}</code>, <code>{r['pair_3']:02d}</code>"
        msg = (
            f"🎯 <b>DỰ ĐOÁN XSMB — {date_str}</b>\n\n"
            f"🔮 3 cặp số: {pairs_str}\n"
            f"📊 Xác suất: {int(r['prob_1']*100)}% | {int(r['prob_2']*100)}% | {int(r['prob_3']*100)}%\n\n"
            f"<i>Trúng nếu 2 số cuối bất kỳ giải ≡ 1 trong 3 cặp trên</i>\n"
            f"<i>Model: {r['model_version']}</i>"
        )
        await notifier.send_message(msg)

    # XSMN (gộp 1 message)
    if all_results["XSMN"]:
        province_map = XSMNCrawler().PROVINCE_MAP
        xsmn_msg = f"🎯 <b>DỰ ĐOÁN XSMN — {date_str}</b>\n\n"
        for r in all_results["XSMN"]:
            pname = province_map.get(r["province"], r["province"])
            pairs_str = f"<code>{r['pair_1']:02d}</code>, <code>{r['pair_2']:02d}</code>, <code>{r['pair_3']:02d}</code>"
            xsmn_msg += f"📍 <b>{pname}</b>: {pairs_str}\n"
        xsmn_msg += f"\n<i>Tổng: {len(all_results['XSMN'])} đài | Model: xgb_v3</i>"
        await notifier.send_message(xsmn_msg)

    print("\n✅ Predict V3 complete!")


if __name__ == "__main__":
    asyncio.run(main())
