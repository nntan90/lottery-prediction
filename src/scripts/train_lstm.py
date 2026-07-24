"""
train_lstm.py
Train LSTM model cho 1 station (region + province).
Dùng để chạy độc lập và lưu model vĩnh viễn lên Supabase Storage.

Usage:
  python src/scripts/train_lstm.py --region XSMN --province tp-hcm
  python src/scripts/train_lstm.py --region XSMN --province tp-hcm --version lstm_v4_20260603
"""

import argparse
import asyncio
import hashlib
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
from src.xsmn_ensemble.data_utils import _load_tails_by_draws as load_xsmn_tails_by_draws
from src.xsmn_ensemble.model_lstm import LotteryLSTM, _encode_draws_to_binary, _ensure_torch
from src.xsmb_ensemble.data_utils import _load_tails_by_draws as load_xsmb_tails_by_draws
from src.xsmb_ensemble.model_lstm import XSMBLSTMv4, _encode_draws_enriched


def train_xsmn_lstm_with_full_refit(
    model_factory: Callable[[], Any],
    sequences: Any,
    labels: Any,
    *,
    epochs: int,
    lr: float,
    seed: int,
    verbose: bool,
) -> tuple[Any, int]:
    """Select epoch count chronologically, then refit a new model on every sequence."""
    validation_model = model_factory()
    best_epoch = validation_model.train_model(
        sequences=sequences,
        labels=labels,
        epochs=epochs,
        lr=lr,
        val_split=0.2,
        patience=15,
        seed=seed,
        verbose=verbose,
    )
    production_model = model_factory()
    production_model.train_model(
        sequences=sequences,
        labels=labels,
        epochs=best_epoch,
        lr=lr,
        val_split=0.0,
        patience=best_epoch + 1,
        seed=seed,
        verbose=verbose,
    )
    return production_model, best_epoch


async def main():
    parser = argparse.ArgumentParser(description="Train LSTM V4 cho XSMN/XSMB")
    parser.add_argument("--region", required=True, choices=["XSMB", "XSMN"])
    parser.add_argument("--province", default=None, help="Slug tỉnh, hoặc 'all' cho XSMB")
    parser.add_argument("--version", default=None, help="Version string, mặc định = ngày hôm nay")
    parser.add_argument("--n_draws", type=int, default=250, help="Số kỳ quay để lấy làm dataset")
    parser.add_argument("--seq_len", type=int, default=None, help="Sequence length cho LSTM")
    parser.add_argument("--epochs", type=int, default=100, help="Số epochs")
    parser.add_argument("--lr", type=float, default=0.002, help="Learning rate")
    parser.add_argument("--weekday", type=int, default=None, choices=list(range(7)),
                        help="Weekday XSMN riêng biệt (0=T2..6=CN)")
    parser.add_argument("--target-date", type=date.fromisoformat, default=None,
                        help="Cutoff train inclusive (YYYY-MM-DD)")
    parser.add_argument("--defer-queue-completion", action="store_true",
                        help="Coordinator sẽ hoàn tất training_queue sau khi đủ mọi family")
    args = parser.parse_args()

    # Yêu cầu PyTorch
    try:
        _ensure_torch()
    except ImportError:
        print("❌ Lỗi: Cần cài đặt PyTorch (pip install torch) để train LSTM.")
        sys.exit(1)

    province = None if args.province in (None, "all", "") else args.province
    weekday = args.weekday if args.region == "XSMN" else None
    wd_suffix = f"_wd{weekday}" if weekday is not None else ""
    version = args.version or f"lstm_v4_{date.today().strftime('%Y%m%d')}{wd_suffix}"
    label = f"{args.region}/{province or 'all'}"
    seq_len = args.seq_len if args.seq_len is not None else (60 if args.region == "XSMB" else 30)

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="train_model")

    print(f"\n🚀 Training LSTM V4: {label} | version={version}")
    print("=" * 60)

    # 1. Load data lịch sử
    print(f"📥 Đang tải {args.n_draws} kỳ lịch sử cho {label}...")
    if args.region == "XSMB":
        history_df = load_xsmb_tails_by_draws(db, args.region, province=province, n_draws=args.n_draws)
    else:
        before_date = args.target_date + timedelta(days=1) if args.target_date else None
        history_df = load_xsmn_tails_by_draws(
            db,
            args.region,
            province=province,
            n_draws=args.n_draws,
            before_date=before_date,
            target_weekday=weekday,
        )

    if history_df.empty or len(history_df) < seq_len + 10:
        msg = f"❌ Không đủ data cho {label}: có {len(history_df)} kỳ, cần tối thiểu {seq_len + 10} kỳ."
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)
    latest_history_date = str(history_df.iloc[-1]["draw_date"])[:10]
    if args.target_date and latest_history_date != args.target_date.isoformat():
        msg = (
            f"❌ LSTM history cutoff mismatch cho {label}: "
            f"latest={latest_history_date}, expected={args.target_date.isoformat()}"
        )
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)

    # 2. Encode sequences
    print(f"⚙️  Encoding sequences (seq_len={seq_len})...")
    if args.region == "XSMB":
        last_seq, training_data = _encode_draws_enriched(history_df, seq_len=seq_len)
    else:
        last_seq, training_data = _encode_draws_to_binary(history_df, seq_len=seq_len)

    if not training_data:
        msg = f"❌ Lỗi encode training data cho {label}."
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)

    sequences, labels = training_data
    draw_count = len(history_df)
    train_samples = len(sequences)
    print(f"  ✅ Data: {draw_count} draws → {train_samples} training samples.")

    # 3. Khởi tạo và Train LSTM
    print("\n🏋️ Training LSTM (PyTorch)...")
    seed_material = (
        f"{args.region}|{province or 'all'}|{weekday}|"
        f"{args.target_date or history_df.iloc[-1]['draw_date']}"
    )
    dynamic_seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:8], 16) % 10_000

    # Train
    start_time = time.time()
    if args.region == "XSMN":
        lstm, best_epoch = train_xsmn_lstm_with_full_refit(
            lambda: LotteryLSTM(input_dim=100, hidden_dim=64, num_layers=1),
            sequences,
            labels,
            epochs=args.epochs,
            lr=args.lr,
            seed=dynamic_seed,
            verbose=True,
        )
        print(f"  🔁 Final refit trên toàn bộ sequences với best_epoch={best_epoch}...")
    else:
        lstm = XSMBLSTMv4(input_dim=200, hidden_dim=64, num_layers=1, use_attention=True)
        lstm.train_model(
            sequences=sequences,
            labels=labels,
            epochs=args.epochs,
            lr=args.lr,
            val_split=0.2,
            patience=15,
            seed=dynamic_seed,
            verbose=True,
        )
    train_time = int(time.time() - start_time)
    print(f"  ✅ Train xong trong {train_time}s.")

    # 4. Save locally
    with tempfile.TemporaryDirectory() as tmpdir:
        model_filename = f"{province or 'all'}{wd_suffix}_{version}.pth"
        local_path = os.path.join(tmpdir, model_filename)
        lstm.save(local_path)

        # 5. Upload to Supabase
        region_folder = f"models/{args.region}/lstm"
        storage_path = f"{region_folder}/{model_filename}"
        print(f"\n📤 Uploading to Supabase Storage: {storage_path}...")

        # LotteryStorage upload model cho file .pth
        if not storage.upload_model(local_path, storage_path):
            msg = f"❌ Upload thất bại cho {label}"
            print(msg)
            await notifier.send_error_alert(msg)
            raise SystemExit(1)

    # 6. Register replacement before deprecating old rows so publication
    # failure cannot remove the currently active production fallback.
    print(f"📝 Đăng ký model mới vào registry...")
    dates_used = sorted(
        value.date().isoformat() if hasattr(value, "date") else str(value)[:10]
        for value in history_df["draw_date"].unique()
    )
    insert_result = db.supabase.table("model_registry").insert({
        "region":           args.region,
        "province":         province,
        "weekday":          weekday,
        "model_name":       "lstm",
        "version":          version,
        "status":           "active",
        "file_path":        storage_path,
        "train_start_date": str(dates_used[0]),
        "train_end_date":   str(dates_used[-1]),
        "train_draws":      draw_count,
        "metric_auc":       None,
        "metric_hit_rate":  None,
        "trained_at":       datetime.utcnow().isoformat(),
    }).execute()
    new_rows = insert_result.data or []
    new_model_id = new_rows[0].get("id") if new_rows else None

    # 7. Deprecate only prior active LSTM rows at the same province-weekday grain.
    if new_model_id is not None:
        print(f"🔄 Đang deprecate các model LSTM cũ của {label}...")
        dep_query = db.supabase.table("model_registry")\
            .update({"status": "deprecated"})\
            .eq("region", args.region)\
            .eq("status", "active")\
            .eq("model_name", "lstm")\
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

    # 8. Cập nhật training queue (tùy chọn)
    if not args.defer_queue_completion:
        try:
            tq_upd = db.supabase.table("training_queue")\
                .update({"status": "done", "completed_at": datetime.utcnow().isoformat()})\
                .eq("region", args.region)\
                .eq("status", "triggered")
            if province is not None:
                tq_upd = tq_upd.eq("province", province)
            else:
                tq_upd = tq_upd.is_("province", "null")
            tq_upd.execute()
        except Exception as e:
            print(f"⚠️  Skip training_queue update: {e}")

    # 9. Gửi báo cáo qua Telegram
    msg = (
        f"✅ <b>Training LSTM xong: {label}</b>\n\n"
        f"🧠 Neural Net: <code>PyTorch LSTM (64 hidden)</code>\n"
        f"📅 Data: {dates_used[0]} → {dates_used[-1]}\n"
        f"🔢 Số kỳ: {draw_count} draws → {train_samples} samples\n"
        f"⏱ Thời gian train: {train_time}s\n"
        f"🏷 Version: <code>{version}</code>\n\n"
        f"<i>Model đã upload Storage và set active.</i>"
    )
    await notifier.send_message(msg)
    print(f"\n✅ All done for LSTM: {label}")


if __name__ == "__main__":
    asyncio.run(main())
