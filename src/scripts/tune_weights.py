"""
tune_weights.py — Weekly Auto-Weight Tuning Script for XSMB v4.0

Tính toán optimal weights cho 7 sub-models dựa trên backtest performance 30 ngày gần nhất
và gửi báo cáo Telegram đề xuất weights mới.
"""

import argparse
import asyncio
import os
import sys
import yaml
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.xsmb_ensemble.auto_weight import compute_optimal_weights, format_weight_report
from src.xsmb_ensemble.ensemble_engine import DEFAULT_WEIGHTS


def load_scoring_config() -> dict:
    """Load scoring config từ config/scoring.yaml."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "config", "scoring.yaml"
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            print(f"⚠️ Không thể đọc scoring.yaml: {e}")
    return {}


async def main():
    parser = argparse.ArgumentParser(description="XSMB Auto-Weight Tuning")
    parser.add_argument("--lookback", type=int, help="Lookback days (override config)")
    parser.add_argument("--smoothing", type=float, help="Smoothing factor (override config)")
    parser.add_argument("--no-telegram", action="store_true", help="Không gửi Telegram notification")
    args = parser.parse_args()

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="predict_ensemble_scoring_log")

    # Load parameters từ config
    config = load_scoring_config()
    xsmb_v4_cfg = config.get("xsmb_v4", {})
    auto_weight_cfg = xsmb_v4_cfg.get("auto_weight", {})

    current_weights = xsmb_v4_cfg.get("weights", DEFAULT_WEIGHTS).copy()

    # Override defaults
    lookback_days = args.lookback or auto_weight_cfg.get("lookback_days", 30)
    smoothing = args.smoothing or auto_weight_cfg.get("smoothing", 0.7)
    min_weight = auto_weight_cfg.get("min_weight", 0.05)
    max_weight = auto_weight_cfg.get("max_weight", 0.35)

    print(f"🔄 Đang tính toán Auto-Weight Tuning cho XSMB...")
    print(f"   Lookback days: {lookback_days} ngày")
    print(f"   Smoothing:     {smoothing}")
    print(f"   Min weight:    {min_weight}")
    print(f"   Max weight:    {max_weight}")

    # 1. Compute optimal weights
    optimal_weights = compute_optimal_weights(
        db=db,
        lookback_days=lookback_days,
        region="XSMB",
        current_weights=current_weights,
        smoothing=smoothing,
        min_weight=min_weight,
        max_weight=max_weight
    )

    # 2. Lấy performance để định dạng report chi tiết
    from src.xsmb_ensemble.auto_weight import (
        _query_model_predictions,
        _query_actual_tails,
        _compute_model_performance
    )
    
    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)
    model_preds = _query_model_predictions(db, "XSMB", start_date, end_date)
    actual_tails = _query_actual_tails(db, "XSMB", start_date, end_date)
    performance = _compute_model_performance(model_preds, actual_tails) if model_preds and actual_tails else None

    # 3. Format report
    report = format_weight_report(current_weights, optimal_weights, performance)
    print("\n" + "=" * 50)
    print(report)
    print("=" * 50 + "\n")

    # 4. Gửi Telegram nếu không chọn --no-telegram
    if not args.no_telegram:
        success = await notifier.send_message(report)
        if success:
            print("✉️ Gửi Telegram report thành công.")
        else:
            print("❌ Gửi Telegram report thất bại.")
    else:
        print("ℹ️ Bỏ qua gửi Telegram.")


if __name__ == "__main__":
    asyncio.run(main())
