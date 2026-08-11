"""
resolve_provinces.py
Resolve TARGET_PROVINCES cho XSMN dựa trên ngày trong tuần.

Lịch xổ Miền Nam (2 đài focus/ngày):
  Thứ Hai:    TP.HCM, Đồng Tháp
  Thứ Ba:     Vũng Tàu, Bến Tre
  Thứ Tư:     Đồng Nai, Cần Thơ
  Thứ Năm:    Tây Ninh, An Giang
  Thứ Sáu:    Vĩnh Long, Bình Dương
  Thứ Bảy:    TP.HCM, Long An
  Chủ Nhật:   Tiền Giang, Kiên Giang

Usage:
  from src.xsmn_ensemble.resolve_provinces import get_target_provinces
  provinces = get_target_provinces(target_date)
  # → ['tp-hcm', 'dong-thap']
"""

import os
from datetime import date, timedelta
from typing import List


# Lịch 2 đài XSMN ensemble target theo DOW (0=Mon..6=Sun)
# Đây là danh sách slug, khớp với PROVINCE_MAP trong xsmn_crawler.py
XSMN_ENSEMBLE_SCHEDULE: dict[int, List[str]] = {
    0: ["tp-hcm", "dong-thap"],       # Thứ Hai
    1: ["vung-tau", "ben-tre"],        # Thứ Ba
    2: ["dong-nai", "can-tho"],        # Thứ Tư
    3: ["tay-ninh", "an-giang"],       # Thứ Năm
    4: ["vinh-long", "binh-duong"],    # Thứ Sáu
    5: ["tp-hcm", "long-an"],          # Thứ Bảy
    6: ["tien-giang", "kien-giang"],   # Chủ Nhật
}

# Canonical schedule for source-integrity checks and crawling.  The ensemble
# intentionally keeps its two-province focus above, while freshness checks must
# certify every XSMN station that was scheduled on the regional D-1 boundary.
XSMN_FULL_SCHEDULE: dict[int, List[str]] = {
    0: ["tp-hcm", "dong-thap", "ca-mau"],
    1: ["ben-tre", "vung-tau", "bac-lieu"],
    2: ["dong-nai", "can-tho", "soc-trang"],
    3: ["tay-ninh", "an-giang", "binh-thuan"],
    4: ["vinh-long", "binh-duong", "tra-vinh"],
    5: ["tp-hcm", "long-an", "binh-phuoc", "hau-giang"],
    6: ["tien-giang", "kien-giang", "da-lat"],
}

DOW_NAMES_VN = [
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
]


def get_target_provinces(target_date: date) -> List[str]:
    """
    Trả về danh sách province slugs cho ngày target_date.

    Ưu tiên:
    1. Env var TARGET_PROVINCES (nếu có) — dạng "tp-hcm,dong-thap"
    2. Fallback: schedule tĩnh theo DOW

    Returns:
        List[str]: e.g. ['tp-hcm', 'dong-thap']
    """
    # 1. Check env var override (GH Actions truyền vào)
    env_val = os.getenv("TARGET_PROVINCES", "").strip()
    if env_val:
        provinces = [p.strip() for p in env_val.split(",") if p.strip()]
        if provinces:
            return provinces

    # 2. Fallback: schedule
    dow = target_date.weekday()  # 0=Mon..6=Sun
    return XSMN_ENSEMBLE_SCHEDULE.get(dow, [])


def get_dow_label(target_date: date) -> str:
    """Trả về tên thứ tiếng Việt."""
    return DOW_NAMES_VN[target_date.weekday()]


def get_scheduled_provinces(target_date: date) -> List[str]:
    """Return every XSMN province scheduled for ``target_date``.

    Unlike :func:`get_target_provinces`, this canonical source schedule never
    reads ``TARGET_PROVINCES``.  It is used to prove crawler coverage and keeps
    the existing two-province production scope unchanged.
    """
    return list(XSMN_FULL_SCHEDULE.get(target_date.weekday(), ()))


def get_previous_scheduled_date(target_date: date, province: str) -> date:
    """Resolve a province's latest scheduled occurrence strictly before D.

    Searching the schedule instead of subtracting seven days correctly routes
    TP.HCM from Monday to the preceding Saturday and from Saturday to Monday.
    """
    if not isinstance(target_date, date):
        raise TypeError("target_date must be a date")
    if not isinstance(province, str) or not province.strip():
        raise ValueError("province must be a nonempty string")
    if not any(province in values for values in XSMN_FULL_SCHEDULE.values()):
        raise ValueError(f"unknown XSMN province: {province}")
    for days_back in range(1, 8):
        candidate = target_date - timedelta(days=days_back)
        if province in XSMN_FULL_SCHEDULE.get(candidate.weekday(), ()):
            return candidate
    raise RuntimeError(f"no previous scheduled date found for {province}")


if __name__ == "__main__":
    from datetime import datetime, timedelta
    today = date.today()
    print(f"📅 Hôm nay: {today} ({get_dow_label(today)})")
    print(f"🎯 Target provinces: {get_target_provinces(today)}")
    print()
    for i in range(7):
        d = today + timedelta(days=i)
        print(f"  {d} ({get_dow_label(d)}): {get_target_provinces(d)}")
