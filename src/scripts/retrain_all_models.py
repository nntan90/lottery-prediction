"""
retrain_all_models.py
Script retrain toàn bộ models sau khi thay đổi features (v2: 17 features).
Dùng để chạy LOCAL một lần sau khi backfill pair_features xong.

Coverage:
  - XSMB: weekday 0-6 (7 models riêng biệt)
  - XSMN: mỗi tỉnh × weekday của nó (21 tỉnh × 1-2 weekday = ~42 models)

Usage:
  python src/scripts/retrain_all_models.py           # Train tất cả
  python src/scripts/retrain_all_models.py --region XSMB  # Chỉ XSMB
  python src/scripts/retrain_all_models.py --region XSMN  # Chỉ XSMN
  python src/scripts/retrain_all_models.py --dry-run  # Xem danh sách không train
"""

import argparse
import subprocess
import sys
import os
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# XSMB: quay mỗi ngày (trừ CN) → train riêng 7 weekday
XSMB_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]

# XSMN: mỗi tỉnh chỉ quay 1-2 thứ/tuần
XSMN_WEEKDAY_PROVINCES = {
    0: ["tp-hcm", "dong-thap", "ca-mau"],
    1: ["ben-tre", "vung-tau", "bac-lieu"],
    2: ["dong-nai", "can-tho", "soc-trang"],
    3: ["tay-ninh", "an-giang", "binh-thuan"],
    4: ["vinh-long", "binh-duong", "tra-vinh"],
    5: ["tp-hcm", "long-an", "binh-phuoc", "hau-giang"],
    6: ["tien-giang", "kien-giang", "da-lat"],
}


def run_train(region: str, province: str, weekday: int, dry_run: bool = False) -> bool:
    """Chạy train_xgb.py cho 1 model. Trả về True nếu thành công."""
    label = f"{region}/{province} [{DOW_NAMES[weekday]}]"
    today = date.today().strftime("%Y%m%d")
    version = f"v3_retrain_{today}_wd{weekday}"

    cmd = [
        sys.executable, "src/scripts/train_xgb.py",
        "--region", region,
        "--province", province,
        "--weekday", str(weekday),
        "--version", version,
        "--force",   # cho phép train dù ít data (weekday models)
    ]

    if dry_run:
        print(f"  [DRY-RUN] {label}: {' '.join(cmd)}")
        return True

    print(f"\n🚀 Training: {label}")
    try:
        result = subprocess.run(cmd, timeout=600)
        if result.returncode == 0:
            print(f"  ✅ Done: {label}")
            return True
        else:
            print(f"  ❌ Failed (exit {result.returncode}): {label}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ⏰ Timeout (600s): {label}")
        return False
    except Exception as e:
        print(f"  ❌ Error: {label}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Retrain tất cả models với features mới")
    parser.add_argument("--region", choices=["XSMB", "XSMN", "ALL"], default="ALL",
                        help="Region cần retrain (default: ALL)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Giây chờ giữa mỗi model (default: 2s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Xem danh sách jobs không train thực")
    args = parser.parse_args()

    jobs = []  # (region, province, weekday)

    # Build danh sách XSMB
    if args.region in ("XSMB", "ALL"):
        for wd in XSMB_WEEKDAYS:
            jobs.append(("XSMB", "all", wd))

    # Build danh sách XSMN
    if args.region in ("XSMN", "ALL"):
        for wd, provinces in XSMN_WEEKDAY_PROVINCES.items():
            for prov in provinces:
                jobs.append(("XSMN", prov, wd))

    print(f"\n{'='*60}")
    print(f"🔄 Retrain All Models — {date.today()}")
    print(f"   Region: {args.region} | Jobs: {len(jobs)} | Delay: {args.delay}s")
    print(f"{'='*60}")
    print(f"\n📋 Danh sách models sẽ retrain:")
    for region, province, wd in jobs:
        print(f"   • {region}/{province} [{DOW_NAMES[wd]}]")
    print()

    if args.dry_run:
        print("[DRY-RUN MODE — không train thực]\n")

    success = []
    failed = []

    for i, (region, province, wd) in enumerate(jobs):
        ok = run_train(region, province, wd, dry_run=args.dry_run)
        if ok:
            success.append(f"{region}/{province}[{DOW_NAMES[wd]}]")
        else:
            failed.append(f"{region}/{province}[{DOW_NAMES[wd]}]")

        if not args.dry_run and args.delay > 0 and i < len(jobs) - 1:
            time.sleep(args.delay)

    print(f"\n{'='*60}")
    print(f"✅ Thành công: {len(success)}/{len(jobs)}")
    if failed:
        print(f"❌ Thất bại ({len(failed)}):")
        for f in failed:
            print(f"   - {f}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
