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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import numpy as np
import pandas as pd

from src.database.supabase_client import LotteryDB
from src.models.xgb_model import LotteryXGB, FEATURE_COLS
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier


@dataclass(frozen=True)
class WalkForwardFold:
    """Date boundaries for one expanding walk-forward validation fold."""

    index: int
    train_dates: tuple[Any, ...]
    validation_dates: tuple[Any, ...]


def load_training_data(
    db: LotteryDB,
    region: str,
    province: str | None,
    weekday: int | None = None,
    target_date: date | None = None,
) -> pd.DataFrame:
    """
    Load pair_features từ Supabase cho 1 station (có pagination).
    Chỉ lấy rows có label hit != NULL.
    Nếu weekday được chỉ định, chỉ lấy các kỳ có day_of_week == weekday.
    """
    label = f"{region}/{province or 'all'}"
    weekday_label = f" | weekday={weekday}" if weekday is not None else ""
    print(f"📥 Loading training data: {label}{weekday_label}...")

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

        # Filter theo weekday nếu được chỉ định
        if weekday is not None:
            query = query.eq("day_of_week", weekday)
        if target_date is not None:
            query = query.lte("feature_date", target_date.isoformat())

        batch = query.execute().data
        if not batch:
            break
        all_data.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    df = pd.DataFrame(all_data)
    if not df.empty:
        for col in FEATURE_COLS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype(np.float32)

    n_ky = len(df) // 100 if len(df) > 0 else 0
    print(f"  ✅ Loaded {len(df)} rows ({n_ky} kỳ){weekday_label}")
    return df


def count_training_draws(df: pd.DataFrame) -> int:
    """Count distinct draw/feature dates available for training."""
    if df.empty or "feature_date" not in df.columns:
        return 0
    return int(df["feature_date"].nunique())


def validate_and_sort_training_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Validate draw integrity and return rows sorted by draw occurrence and pair.

    Every draw must contain exactly one row for each pair 00..99. Training stops
    on incomplete or duplicate draws so Hit@3 cannot silently cross draw
    boundaries.
    """
    required_columns = set(FEATURE_COLS + ["pair", "feature_date", "hit"])
    missing_columns = sorted(required_columns.difference(df.columns))
    if missing_columns:
        raise ValueError(f"Thiếu cột training bắt buộc: {missing_columns}")
    if df.empty:
        raise ValueError("Training data rỗng")
    if df["feature_date"].isna().any():
        raise ValueError("Training data có feature_date NULL")

    clean = df.copy()
    numeric_hits = pd.to_numeric(clean["hit"], errors="coerce")
    if numeric_hits.isna().any() or not numeric_hits.isin([0, 1]).all():
        raise ValueError("Cột hit phải là nhãn nhị phân 0/1 và không được NULL")
    clean["hit"] = numeric_hits.astype(int)

    numeric_pairs = pd.to_numeric(clean["pair"], errors="coerce")
    if numeric_pairs.isna().any() or not np.equal(numeric_pairs, np.floor(numeric_pairs)).all():
        raise ValueError("Cột pair phải là số nguyên trong khoảng 00..99")
    clean["pair"] = numeric_pairs.astype(int)

    expected_pairs = set(range(100))
    invalid_draws = []
    for draw_date, draw_rows in clean.groupby("feature_date", sort=True):
        actual_pairs = set(draw_rows["pair"].tolist())
        if len(draw_rows) != 100 or len(actual_pairs) != 100 or actual_pairs != expected_pairs:
            missing_pairs = sorted(expected_pairs.difference(actual_pairs))
            unexpected_pairs = sorted(actual_pairs.difference(expected_pairs))
            invalid_draws.append(
                f"{draw_date}: rows={len(draw_rows)}, unique_pairs={len(actual_pairs)}, "
                f"missing={missing_pairs}, unexpected={unexpected_pairs}"
            )

    if invalid_draws:
        details = "; ".join(invalid_draws[:5])
        if len(invalid_draws) > 5:
            details += f"; ... và {len(invalid_draws) - 5} kỳ khác"
        raise ValueError(f"Dữ liệu kỳ không hợp lệ: {details}")

    return clean.sort_values(["feature_date", "pair"], kind="mergesort").reset_index(drop=True)


def _build_walk_forward_folds_from_sorted(
    df: pd.DataFrame,
    max_folds: int = 5,
    min_initial_train_draws: int = 12,
) -> list[WalkForwardFold]:
    """Build expanding folds from already validated and chronologically sorted data."""
    if max_folds < 2:
        raise ValueError("max_folds phải >= 2")
    if min_initial_train_draws < 1:
        raise ValueError("min_initial_train_draws phải >= 1")

    dates = tuple(df["feature_date"].drop_duplicates().tolist())
    total_draws = len(dates)
    validation_draws = max(3, min(13, total_draws // 10))
    fold_count = min(
        max_folds,
        (total_draws - min_initial_train_draws) // validation_draws,
    )
    if fold_count < 2:
        raise ValueError(
            "Không thể tạo ít nhất 2 walk-forward folds: "
            f"{total_draws} kỳ, initial_train>={min_initial_train_draws}, "
            f"validation_window={validation_draws}"
        )

    validation_span = fold_count * validation_draws
    initial_train_draws = total_draws - validation_span
    folds = []
    for fold_offset in range(fold_count):
        validation_start = initial_train_draws + fold_offset * validation_draws
        validation_end = validation_start + validation_draws
        folds.append(
            WalkForwardFold(
                index=fold_offset + 1,
                train_dates=dates[:validation_start],
                validation_dates=dates[validation_start:validation_end],
            )
        )
    return folds


def build_walk_forward_folds(
    df: pd.DataFrame,
    max_folds: int = 5,
    min_initial_train_draws: int = 12,
) -> list[WalkForwardFold]:
    """
    Build expanding validation folds grouped by distinct draw occurrences.

    The validation window is ``max(3, min(13, total_draws // 10))``. Any
    remainder stays in the initial training window, making the final fold end
    at the newest available draw.
    """
    clean = validate_and_sort_training_data(df)
    return _build_walk_forward_folds_from_sorted(
        clean,
        max_folds=max_folds,
        min_initial_train_draws=min_initial_train_draws,
    )


def train_with_walk_forward(
    df: pd.DataFrame,
    model_params: dict[str, Any],
    model_class: type | None = None,
) -> tuple[Any, dict[str, float]]:
    """
    Evaluate independent models on expanding folds, then fit production on all data.

    Registry-compatible metrics are the median of valid fold AUC values and
    Hit@3 weighted by validation draw count. AUC from a single-class validation
    fold is excluded, and at least two valid AUC folds are required.
    """
    clean = validate_and_sort_training_data(df)
    folds = _build_walk_forward_folds_from_sorted(clean)
    model_factory = model_class or LotteryXGB

    valid_aucs: list[float] = []
    weighted_hit_sum = 0.0
    validation_draw_total = 0

    print(
        f"\n🔁 Walk-forward validation: {len(folds)} folds | "
        f"validation={len(folds[0].validation_dates)} kỳ/fold"
    )
    for fold in folds:
        train_mask = clean["feature_date"].isin(fold.train_dates)
        validation_mask = clean["feature_date"].isin(fold.validation_dates)
        X_train = clean.loc[train_mask, FEATURE_COLS]
        y_train = clean.loc[train_mask, "hit"].astype(int)
        X_validation = clean.loc[validation_mask, FEATURE_COLS]
        y_validation = clean.loc[validation_mask, "hit"].astype(int)

        fold_model = model_factory(**model_params)
        fold_metrics = fold_model.train(
            X_train,
            y_train,
            X_validation,
            y_validation,
        )

        auc_value = fold_metrics.get("auc")
        if y_validation.nunique() < 2:
            auc_label = "N/A (single class)"
        elif auc_value is None:
            auc_label = "N/A (missing metric)"
        else:
            numeric_auc = float(auc_value)
            if np.isfinite(numeric_auc) and 0.0 <= numeric_auc <= 1.0:
                valid_aucs.append(numeric_auc)
                auc_label = f"{numeric_auc:.4f}"
            else:
                auc_label = "N/A (invalid metric)"

        if "hit_rate_top3" not in fold_metrics:
            raise ValueError(f"Fold {fold.index} không trả metric hit_rate_top3")
        hit_rate = float(fold_metrics["hit_rate_top3"])
        if not np.isfinite(hit_rate) or not 0.0 <= hit_rate <= 1.0:
            raise ValueError(
                f"Fold {fold.index} trả hit_rate_top3 không hợp lệ: {hit_rate}"
            )
        validation_draws = len(fold.validation_dates)
        weighted_hit_sum += hit_rate * validation_draws
        validation_draw_total += validation_draws

        print(
            f"  Fold {fold.index}/{len(folds)} | "
            f"Train {fold.train_dates[0]} → {fold.train_dates[-1]} "
            f"({len(fold.train_dates)} kỳ) | "
            f"Val {fold.validation_dates[0]} → {fold.validation_dates[-1]} "
            f"({validation_draws} kỳ) | AUC={auc_label} | Hit@3={hit_rate:.4f}"
        )

    if len(valid_aucs) < 2:
        raise ValueError(
            "Walk-forward cần ít nhất 2 folds có AUC hợp lệ; "
            f"chỉ có {len(valid_aucs)}/{len(folds)}"
        )

    auc_array = np.asarray(valid_aucs, dtype=float)
    metrics = {
        "auc": round(float(np.median(auc_array)), 4),
        "hit_rate_top3": round(weighted_hit_sum / validation_draw_total, 4),
        "auc_mean": round(float(np.mean(auc_array)), 4),
        "auc_min": round(float(np.min(auc_array)), 4),
        "auc_std": round(float(np.std(auc_array)), 4),
        "auc_valid_folds": float(len(valid_aucs)),
        "fold_count": float(len(folds)),
    }
    print(
        "  📊 Walk-forward summary | "
        f"AUC median={metrics['auc']:.4f}, mean={metrics['auc_mean']:.4f}, "
        f"min={metrics['auc_min']:.4f}, std={metrics['auc_std']:.4f} | "
        f"Hit@3 weighted={metrics['hit_rate_top3']:.4f}"
    )

    print(f"\n🏋️ Final fit trên toàn bộ {count_training_draws(clean)} kỳ hợp lệ...")
    production_model = model_factory(**model_params)
    production_model.train(
        clean[FEATURE_COLS],
        clean["hit"].astype(int),
    )
    return production_model, metrics


async def main():
    parser = argparse.ArgumentParser(description="Train XGBoost V3")
    parser.add_argument("--region", required=True, choices=["XSMB", "XSMN"])
    parser.add_argument("--province", default=None, help="Slug tỉnh, hoặc 'all' cho XSMB")
    parser.add_argument("--version", default=None, help="Version string, mặc định = ngày hôm nay")
    parser.add_argument("--force", action="store_true", help="Force train dù ít dữ liệu (<1000 rows)")
    parser.add_argument("--min_draws", type=int, default=None,
                        help="Số kỳ quay tối thiểu để train. Mặc định: 60, hoặc 24 nếu --force")
    parser.add_argument("--weekday", type=int, default=None, choices=list(range(7)),
                        help="Ngày trong tuần để train riêng (0=T2..6=CN). Mặc định: train tất cả")
    parser.add_argument("--target-date", type=date.fromisoformat, default=None,
                        help="Cutoff train inclusive (YYYY-MM-DD), dùng cho recovery/backfill")
    parser.add_argument("--defer-queue-completion", action="store_true",
                        help="Coordinator sẽ hoàn tất training_queue sau khi đủ mọi family")
    # Hyperparameter overrides — dùng bởi Master Retrain Agent
    parser.add_argument("--n_estimators", type=int, default=300, help="XGBoost n_estimators (default: 300)")
    parser.add_argument("--max_depth", type=int, default=4, help="XGBoost max_depth (default: 4)")
    parser.add_argument("--learning_rate", type=float, default=0.05, help="XGBoost learning_rate (default: 0.05)")
    parser.add_argument("--subsample", type=float, default=0.8, help="XGBoost subsample (default: 0.8)")
    parser.add_argument("--colsample_bytree", type=float, default=0.8, help="XGBoost colsample_bytree (default: 0.8)")
    parser.add_argument("--scale_pos_weight", type=float, default=1.0,
                        help="XGBoost scale_pos_weight để xử lý class imbalance (default: 1.0; khuyến nghị 3.2 cho hit~24%)")
    args = parser.parse_args()

    province = None if args.province in (None, "all", "") else args.province
    weekday  = args.weekday  # None = không phân biệt
    
    # ── FORCE WEEKDAY=NONE FOR XSMB (v5.0 Unified Model) ──
    if args.region == "XSMB":
        weekday = None

    wd_suffix = f"_wd{weekday}" if weekday is not None else ""
    version = args.version or f"v3_{date.today().strftime('%Y%m%d')}{wd_suffix}"
    label = f"{args.region}/{province or 'all'}"
    if weekday is not None:
        DOW_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        label += f" [{DOW_NAMES[weekday]}]"

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="train_model")

    print(f"\n🚀 Training XGBoost V3: {label} | version={version}")
    print("=" * 60)

    # 1. Load data
    df = load_training_data(db, args.region, province, weekday, args.target_date)
    if args.target_date and (
        df.empty or str(df["feature_date"].max())[:10] != args.target_date.isoformat()
    ):
        latest = None if df.empty else str(df["feature_date"].max())[:10]
        msg = (
            f"❌ XGBoost history cutoff mismatch cho {label}: "
            f"latest={latest or 'none'}, expected={args.target_date.isoformat()}"
        )
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)

    min_draws = args.min_draws if args.min_draws is not None else (24 if args.force else 60)
    draw_count = count_training_draws(df)
    if draw_count < min_draws:
        msg = (
            f"❌ Không đủ data để train {label}: {draw_count} kỳ "
            f"({len(df)} rows, cần ≥ {min_draws} kỳ)"
        )
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)

    # 2. Walk-forward validation + final fit
    print("\n🏋️ Training XGBoost...")
    print(f"   Params: n_estimators={args.n_estimators}, max_depth={args.max_depth}, learning_rate={args.learning_rate}, subsample={args.subsample}, colsample_bytree={args.colsample_bytree}, scale_pos_weight={args.scale_pos_weight}")
    model_params = {
        "n_estimators": args.n_estimators,
        "max_depth": args.max_depth,
        "learning_rate": args.learning_rate,
        "subsample": args.subsample,
        "colsample_bytree": args.colsample_bytree,
        "scale_pos_weight": args.scale_pos_weight,
    }
    try:
        model, metrics = train_with_walk_forward(df, model_params)
    except ValueError as exc:
        msg = f"❌ Walk-forward validation thất bại cho {label}: {exc}"
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1) from exc
    print(
        f"  AUC median: {metrics.get('auc', 'N/A')} | "
        f"Hit@3 weighted: {metrics.get('hit_rate_top3', 'N/A')}"
    )
    print(f"  Training draws used: {draw_count} kỳ (min={min_draws})")

    # 3. Save model locally
    with tempfile.TemporaryDirectory() as tmpdir:
        model_filename = f"{province or 'all'}_{version}.pkl"
        local_path = os.path.join(tmpdir, model_filename)
        model.save(local_path)

        # 4. Upload to Supabase Storage
        # Convention: storage_path = "models/{region}/{file}" inside bucket "models"
        # Bucket "models" + path "models/XSMN/..." → actual path trong bucket là "models/XSMN/..."
        # (consistent với các model cũ đã lưu theo format này)
        if weekday is not None:
            region_folder = f"models/{args.region}/wd{weekday}"
        else:
            region_folder = f"models/{args.region}"
        storage_path = f"{region_folder}/{model_filename}"
        print(f"\n📤 Uploading to Supabase Storage: {storage_path}...")
        if not storage.upload_model(local_path, storage_path):
            msg = f"❌ Upload thất bại cho {label}"
            print(msg)
            await notifier.send_error_alert(msg)
            raise SystemExit(1)

    # 5. Insert replacement before deprecating old rows. If insert fails, the
    # currently active production artifact remains available.
    dates_used = sorted(df["feature_date"].unique())
    insert_result = db.supabase.table("model_registry").insert({
        "region":           args.region,
        "province":         province,
        "weekday":          weekday,       # None = không phân biệt
        "model_name":       "xgboost_core",
        "version":          version,
        "status":           "active",
        "file_path":        storage_path,
        "train_start_date": dates_used[0],
        "train_end_date":   dates_used[-1],
        "train_draws":      draw_count,
        "metric_auc":       metrics.get("auc"),
        "metric_hit_rate":  metrics.get("hit_rate_top3"),
        "trained_at":       datetime.utcnow().isoformat(),
    }).execute()
    new_rows = insert_result.data or []
    new_model_id = new_rows[0].get("id") if new_rows else None

    # 6. Deprecate prior active rows only after the replacement is registered.
    if new_model_id is not None:
        dep_query = db.supabase.table("model_registry")\
            .update({"status": "deprecated"})\
            .eq("region", args.region)\
            .eq("status", "active")\
            .eq("model_name", "xgboost_core")\
            .neq("id", new_model_id)
        if province is not None:
            dep_query = dep_query.eq("province", province)
        else:
            dep_query = dep_query.is_("province", "null")
        if weekday is not None:
            dep_query = dep_query.eq("weekday", weekday)
        else:
            dep_query = dep_query.is_("weekday", "null")
        dep_query.execute()
    else:
        print("  ⚠️ Registry insert không trả id; giữ các active rows cũ để rollback an toàn")

    # 7. Update training_queue unless a multi-family coordinator owns completion.
    if not args.defer_queue_completion:
        tq_upd = db.supabase.table("training_queue")\
            .update({"status": "done", "completed_at": datetime.utcnow().isoformat()})\
            .eq("region", args.region)\
            .eq("status", "triggered")
        if province is not None:
            tq_upd = tq_upd.eq("province", province)
        else:
            tq_upd = tq_upd.is_("province", "null")
        tq_upd.execute()

    # 8. Gửi Telegram
    hit_pct = int(metrics.get("hit_rate_top3", 0) * 100)
    auc = metrics.get("auc", 0)
    wd_info = f" | Weekday: {weekday}" if weekday is not None else ""
    msg = (
        f"✅ <b>Training xong: {label}</b>\n\n"
        f"📊 AUC walk-forward (median): <code>{auc}</code>\n"
        f"🎯 Hit@3 walk-forward (weighted): <code>{hit_pct}%</code>\n"
        f"📅 Data: {dates_used[0]} → {dates_used[-1]}{wd_info}\n"
        f"🔢 Kỳ train: {draw_count} | Min: {min_draws} | Version: {version}\n\n"
        f"<i>Model đã được set active trong registry.</i>"
    )
    await notifier.send_message(msg)
    print(f"\n✅ Done: {label} | WF AUC median={auc} | WF Hit@3 weighted={hit_pct}%")


if __name__ == "__main__":
    asyncio.run(main())
