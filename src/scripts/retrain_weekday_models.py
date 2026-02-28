"""
retrain_weekday_models.py
Script chạy 1 lần để trigger training model theo weekday cho tất cả XSMN provinces.

Logic:
  - XSMB: không đổi, dùng 1 model chung (weekday=None)
  - XSMN: mỗi tỉnh tham gia 2 thứ/tuần → trigger 2 jobs train riêng biệt

Usage:
  python src/scripts/retrain_weekday_models.py           # trigger qua gh CLI
  python src/scripts/retrain_weekday_models.py --local   # train trực tiếp (không qua GitHub Actions)
"""

import argparse
import asyncio
import os
import subprocess
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier


# Map weekday → danh sách tỉnh XSMN quay ngày đó (khớp với verify_v3.py)
# 0=Thứ Hai, ..., 6=Chủ Nhật
XSMN_WEEKDAY_PROVINCES = {
    0: ["tp-hcm", "dong-thap", "ca-mau"],          # Thứ 2
    1: ["ben-tre", "vung-tau", "bac-lieu"],          # Thứ 3
    2: ["dong-nai", "can-tho", "soc-trang"],         # Thứ 4
    3: ["tay-ninh", "an-giang", "binh-thuan"],       # Thứ 5
    4: ["vinh-long", "binh-duong", "tra-vinh"],      # Thứ 6
    5: ["tp-hcm", "long-an", "binh-phuoc", "hau-giang"],  # Thứ 7
    6: ["tien-giang", "kien-giang", "da-lat"],       # Chủ nhật
}

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def trigger_via_gh(region: str, province: str, weekday: int) -> bool:
    """Trigger 05-train-model.yml via gh CLI."""
    prov_arg = province if province else "all"
    cmd = [
        "gh", "workflow", "run", "05-train-model.yml",
        "-f", f"region={region}",
        "-f", f"province={prov_arg}",
        "-f", f"weekday={weekday}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"  ✅ Triggered: {region}/{province} [{DOW_NAMES[weekday]}]")
            return True
        else:
            print(f"  ❌ Failed: {result.stderr.strip()}")
            return False
    except Exception as e:
        print(f"  ❌ Exception: {e}")
        return False


async def train_local(region: str, province: str, weekday: int):
    """Train trực tiếp tại local (không dùng gh CLI)."""
    prov_arg = province if province else "all"
    cmd = [
        sys.executable,
        "src/scripts/train_xgb.py",
        "--region", region,
        "--province", prov_arg,
        "--weekday", str(weekday),
        "--force",  # allow ít data hơn 1000 rows khi train theo weekday
    ]
    print(f"\n🚀 Training: {region}/{province} [{DOW_NAMES[weekday]}]")
    result = subprocess.run(cmd, timeout=600)
    if result.returncode != 0:
        print(f"  ❌ Training failed (exit code {result.returncode})")
        return False
    return True


async def main():
    parser = argparse.ArgumentParser(description="Retrain all XSMN models split by weekday")
    parser.add_argument("--local", action="store_true",
                        help="Train trực tiếp (không dùng gh CLI)")
    parser.add_argument("--weekday", type=int, default=None, choices=list(range(7)),
                        help="Chỉ train weekday cụ thể (mặc định: tất cả)")
    parser.add_argument("--province", type=str, default=None,
                        help="Chỉ train province cụ thể")
    args = parser.parse_args()

    db = LotteryDB()
    notifier = LotteryNotifier()
    triggered = []
    failed = []

    # Build danh sách jobs cần train
    jobs = []  # List[(weekday, province)]
    for wd, provinces in XSMN_WEEKDAY_PROVINCES.items():
        if args.weekday is not None and wd != args.weekday:
            continue
        for prov in provinces:
            if args.province is not None and prov != args.province:
                continue
            jobs.append((wd, prov))

    print(f"\n📋 Sẽ train {len(jobs)} model(s) cho XSMN:")
    for wd, prov in jobs:
        print(f"   - {prov} [{DOW_NAMES[wd]}]")

    print("\n" + "=" * 60)

    for wd, prov in jobs:
        if args.local:
            ok = await train_local("XSMN", prov, wd)
        else:
            ok = trigger_via_gh("XSMN", prov, wd)

        if ok:
            triggered.append(f"XSMN/{prov} [{DOW_NAMES[wd]}]")
        else:
            failed.append(f"XSMN/{prov} [{DOW_NAMES[wd]}]")

    # Summary
    print(f"\n{'='*60}")
    print(f"✅ Thành công: {len(triggered)} | ❌ Thất bại: {len(failed)}")
    if failed:
        print("Thất bại:")
        for f in failed:
            print(f"  - {f}")

    # Telegram summary
    mode_label = "Local" if args.local else "GitHub Actions"
    msg = (
        f"🔔 <b>Retrain Weekday Models — {date.today()}</b>\n\n"
        f"📦 Mode: {mode_label}\n"
        f"✅ Triggered: {len(triggered)}/{len(jobs)}\n"
    )
    if failed:
        msg += f"❌ Thất bại: {', '.join(failed)}\n"
    msg += "\n<i>Models sẽ được active sau khi training hoàn thành.</i>"
    await notifier.send_message(msg)


if __name__ == "__main__":
    asyncio.run(main())
