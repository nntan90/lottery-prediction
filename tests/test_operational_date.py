from datetime import date, datetime

from src.utils.operational_date import resolve_operational_date


def test_operational_date_before_rollover_uses_previous_day():
    vn_now = datetime(2026, 5, 14, 0, 3, 58)
    assert resolve_operational_date(vn_now) == date(2026, 5, 13)


def test_operational_date_at_rollover_uses_current_day():
    vn_now = datetime(2026, 5, 14, 6, 0, 0)
    assert resolve_operational_date(vn_now) == date(2026, 5, 14)


def test_operational_date_after_rollover_uses_current_day():
    vn_now = datetime(2026, 5, 14, 21, 47, 0)
    assert resolve_operational_date(vn_now) == date(2026, 5, 14)
