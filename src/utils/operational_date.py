"""
Operational date helpers for scheduled workflows.

GitHub Actions scheduled jobs can be delayed. Lottery crawl jobs are intended
to process the draw day that just ended; if a 21:47 VN job slips past midnight,
the correct operational date is still yesterday.
"""

from datetime import date, datetime, timedelta
from typing import Optional


def vietnam_now() -> datetime:
    """Return current Vietnam local time using UTC+7."""
    return datetime.utcnow() + timedelta(hours=7)


def resolve_operational_date(
    vn_now: Optional[datetime] = None,
    *,
    rollover_hour: int = 6,
) -> date:
    """
    Resolve the default operational date.

    Before rollover_hour VN, use yesterday. This protects late scheduled runs
    from accidentally crawling the next day before results exist.
    """
    now = vn_now or vietnam_now()
    if now.hour < rollover_hour:
        return now.date() - timedelta(days=1)
    return now.date()
