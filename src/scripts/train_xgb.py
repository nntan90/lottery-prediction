"""
train_xgb.py
Train XGBoost model cho 1 station (region + province).
Chạy trên GitHub Actions (CPU, ubuntu-latest) — triggered bởi 05-train-model.yml.

Usage:
  python src/scripts/train_xgb.py --region XSMB --province all
  python src/scripts/train_xgb.py --region XSMN --province tp-hcm
  python src/scripts/train_xgb.py --region XSMB --province all --version v3_20260219
"""

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
from sklearn.model_selection import train_test_split

from src.database.supabase_client import LotteryDB
from src.models.xgb_model import LotteryXGB, FEATURE_COLS
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier


def load_training_data(db: LotteryDB, region: str, province: str | None) -> pd.DataFrame:
    """
    Load pair_features từ Supabase cho 1 station (có pagination).
    Chỉ lấy rows có label hit != NULL.
    """
    label = f"{region}/{province or 'all'}"
    print(f"📥 Loading training data: {label}...")

    cols = ",".join(FEATURE_COLS + ["pair", "feature_date", "hit"])
    all_data = []
    offset = 0

    while True:
        query = db.supabase.table("pair_features")\
            .select(cols)\
            .eq("region", region)\
            .not_.is_("hit", "null")\
            .order("feature_date")\
            .range(offset, offset + 999)

        if province:
            query = query.eq("province", province)
        else:
            query = query.is_("province", "null")

        batch = query.execute().data
        if not batch:
            break
        all_data.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    df = pd.DataFrame(all_data)
    print(f"  ✅ Loaded {len(df)} rows ({len(df)//100} kỳ)")
    return df


def time_based_split(df: pd.DataFrame, val_ratio: float = 0.2):
    """Split theo thời gian (không shuffle) để tránh leakage."""
    dates = sorted(df["feature_date"].unique())
    split_idx = int(len(dates) * (1 - val_ratio))
    train_dates = set(dates[:split_idx])
    val_dates = set(dates[split_idx:])

    X_train = df[df["feature_date"].isin(train_dates)][FEATURE_COLS]
    y_train = df[df["feature_date"].isin(train_dates)]["hit"].astype(int)
    X_val = df[df["feature_date"].isin(val_dates)][FEATURE_COLS]
    y_val = df[df["feature_date"].isin(val_dates)]["hit"].astype(int)

    print(f"  Train: {len(train_dates)} kỳ ({len(X_train)} rows) | Val: {len(val_dates)} kỳ ({len(X_val)} rows)")
    return X_train, y_train, X_val, y_val


async def main():
    parser = argparse.ArgumentParser(description="Train XGBoost V3")
    parser.add_argument("--region", required=True, choices=["XSMB", "XSMN"])
    parser.add_argument("--province", default=None, help="Slug tỉnh, hoặc 'all' cho XSMB")
    parser.add_argument("--version", default=None, help="Version string, mặc định = ngày hôm nay")
    args = parser.parse_args()

    province = None if args.province in (None, "all", "") else args.province
    version = args.version or f"v3_{date.today().strftime('%Y%m%d')}"
    label = f"{args.region}/{province or 'all'}"

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier()

    print(f"\n🚀 Training XGBoost V3: {label} | version={version}")
    print("=" * 60)

    # 1. Load data
    df = load_training_data(db, args.region, province)
    if len(df) < 1000:  # < 10 kỳ
        msg = f"❌ Không đủ data để train {label}: {len(df)} rows (cần ≥ 1000)"
        print(msg)
        await notifier.send_error_alert(msg)
        return

    # 2. Split
    X_train, y_train, X_val, y_val = time_based_split(df)

    # 3. Train
    print("\n🏋️ Training XGBoost...")
    model = LotteryXGB(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
    )
    metrics = model.train(X_train, y_train, X_val, y_val)
    print(f"  AUC: {metrics.get('auc', 'N/A')} | Hit@3: {metrics.get('hit_rate_top3', 'N/A')}")

    # 4. Save model locally
    with tempfile.TemporaryDirectory() as tmpdir:
        model_filename = f"{province or 'all'}_{version}.pkl"
        local_path = os.path.join(tmpdir, model_filename)
        model.save(local_path)

        # 5. Upload to Supabase Storage
        storage_path = f"models/{args.region}/{model_filename}"
        print(f"\n📤 Uploading to Supabase Storage: {storage_path}...")
        if not storage.upload_model(local_path, storage_path):
            msg = f"❌ Upload thất bại cho {label}"
            print(msg)
            await notifier.send_error_alert(msg)
            return

    # 6. Deprecate model cũ
    db.supabase.table("model_registry")\
        .update({"status": "deprecated"})\
        .eq("region", args.region)\
        .eq("status", "active")\
        .execute() if province is None else \
    db.supabase.table("model_registry")\
        .update({"status": "deprecated"})\
        .eq("region", args.region)\
        .eq("province", province)\
        .eq("status", "active")\
        .execute()

    # 7. Insert vào model_registry
    dates_used = sorted(df["feature_date"].unique())
    db.supabase.table("model_registry").insert({
        "region": args.region,
        "province": province,
        "version": version,
        "status": "active",
        "file_path": storage_path,
        "train_start_date": dates_used[0],
        "train_end_date": dates_used[-1],
        "train_draws": len(df) // 100,
        "metric_auc": metrics.get("auc"),
        "metric_hit_rate": metrics.get("hit_rate_top3"),
        "trained_at": datetime.utcnow().isoformat(),
    }).execute()

    # 8. Update training_queue
    db.supabase.table("training_queue")\
        .update({
            "status": "done",
            "completed_at": datetime.utcnow().isoformat(),
        })\
        .eq("region", args.region)\
        .eq("status", "triggered")\
        .execute() if province is None else \
    db.supabase.table("training_queue")\
        .update({
            "status": "done",
            "completed_at": datetime.utcnow().isoformat(),
        })\
        .eq("region", args.region)\
        .eq("province", province)\
        .eq("status", "triggered")\
        .execute()

    # 9. Gửi Telegram
    hit_pct = int(metrics.get("hit_rate_top3", 0) * 100)
    auc = metrics.get("auc", 0)
    msg = (
        f"✅ <b>Training xong: {label}</b>\n\n"
        f"📊 AUC: <code>{auc}</code>\n"
        f"🎯 Hit@3: <code>{hit_pct}%</code>\n"
        f"📅 Data: {dates_used[0]} → {dates_used[-1]}\n"
        f"🔢 Kỳ train: {len(df)//100} | Version: {version}\n\n"
        f"<i>Model đã được set active trong registry.</i>"
    )
    await notifier.send_message(msg)
    print(f"\n✅ Done: {label} | AUC={auc} | Hit@3={hit_pct}%")


if __name__ == "__main__":
    asyncio.run(main())
