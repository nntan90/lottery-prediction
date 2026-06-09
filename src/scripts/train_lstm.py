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
import os
import sys
import tempfile
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
from src.xsmn_ensemble.data_utils import _load_tails_by_draws
from src.xsmn_ensemble.model_lstm import LotteryLSTM, _encode_draws_to_binary, _ensure_torch


async def main():
    parser = argparse.ArgumentParser(description="Train LSTM V4 cho XSMN/XSMB")
    parser.add_argument("--region", required=True, choices=["XSMB", "XSMN"])
    parser.add_argument("--province", default=None, help="Slug tỉnh, hoặc 'all' cho XSMB")
    parser.add_argument("--version", default=None, help="Version string, mặc định = ngày hôm nay")
    parser.add_argument("--n_draws", type=int, default=250, help="Số kỳ quay để lấy làm dataset")
    parser.add_argument("--seq_len", type=int, default=30, help="Sequence length cho LSTM")
    parser.add_argument("--epochs", type=int, default=100, help="Số epochs")
    parser.add_argument("--lr", type=float, default=0.002, help="Learning rate")
    args = parser.parse_args()

    # Yêu cầu PyTorch
    try:
        _ensure_torch()
    except ImportError:
        print("❌ Lỗi: Cần cài đặt PyTorch (pip install torch) để train LSTM.")
        sys.exit(1)

    province = None if args.province in (None, "all", "") else args.province
    version = args.version or f"lstm_v4_{date.today().strftime('%Y%m%d')}"
    label = f"{args.region}/{province or 'all'}"

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="train_model")

    print(f"\n🚀 Training LSTM V4: {label} | version={version}")
    print("=" * 60)

    # 1. Load data lịch sử
    print(f"📥 Đang tải {args.n_draws} kỳ lịch sử cho {label}...")
    history_df = _load_tails_by_draws(db, args.region, province=province, n_draws=args.n_draws)

    if history_df.empty or len(history_df) < args.seq_len + 10:
        msg = f"❌ Không đủ data cho {label}: có {len(history_df)} kỳ, cần tối thiểu {args.seq_len + 10} kỳ."
        print(msg)
        await notifier.send_error_alert(msg)
        raise SystemExit(1)

    # 2. Encode sequences
    print(f"⚙️  Encoding sequences (seq_len={args.seq_len})...")
    last_seq, training_data = _encode_draws_to_binary(history_df, seq_len=args.seq_len)

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
    lstm = LotteryLSTM(input_dim=100, hidden_dim=64, num_layers=1)

    dynamic_seed = int(time.time()) % 10000

    # Train
    start_time = time.time()
    lstm.train_model(
        sequences=sequences,
        labels=labels,
        epochs=args.epochs,
        lr=args.lr,
        val_split=0.2,
        patience=15,
        seed=dynamic_seed,
        verbose=True
    )
    train_time = int(time.time() - start_time)
    print(f"  ✅ Train xong trong {train_time}s.")

    # 4. Save locally
    with tempfile.TemporaryDirectory() as tmpdir:
        model_filename = f"{province or 'all'}_{version}.pth"
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

    # 6. Deprecate LSTM models cũ
    print(f"🔄 Đang deprecate các model LSTM cũ của {label}...")
    dep_query = db.supabase.table("model_registry")\
        .update({"status": "deprecated"})\
        .eq("region", args.region)\
        .eq("status", "active")\
        .like("version", "lstm_%")  # Chỉ deprecate các model LSTM

    if province is not None:
        dep_query = dep_query.eq("province", province)
    else:
        dep_query = dep_query.is_("province", "null")
    dep_query.execute()

    # 7. Insert model mới vào model_registry
    print(f"📝 Đăng ký model mới vào registry...")
    dates_used = sorted(history_df["draw_date"].unique())
    db.supabase.table("model_registry").insert({
        "region":           args.region,
        "province":         province,
        "weekday":          None,
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

    # 8. Cập nhật training queue (tùy chọn)
    tq_upd = db.supabase.table("training_queue")\
        .update({"status": "done", "completed_at": datetime.utcnow().isoformat()})\
        .eq("region", args.region)\
        .eq("status", "triggered")\
        .like("model_name", "%lstm%")
    if province is not None:
        tq_upd = tq_upd.eq("province", province)
    else:
        tq_upd = tq_upd.is_("province", "null")
    tq_upd.execute()

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
