"""
Regression tests for health digest date resolution.
"""

from datetime import datetime, date

from src.scripts.health_digest import resolve_default_target_date, summarize_xsmn_model_runs


def test_digest_after_midnight_reports_previous_operational_day():
    vn_now = datetime(2026, 5, 13, 1, 0, 0)
    assert resolve_default_target_date(vn_now) == date(2026, 5, 12)


def test_digest_after_morning_reports_current_day():
    vn_now = datetime(2026, 5, 13, 6, 0, 0)
    assert resolve_default_target_date(vn_now) == date(2026, 5, 13)


def test_xsmn_model_run_summary_counts_unique_successful_sources():
    rows = [
        {"province": "ben-tre", "model_name": f"m{idx}", "status": "success"}
        for idx in range(6)
    ] + [
        {"province": "vung-tau", "model_name": f"m{idx}", "status": "success"}
        for idx in range(4)
    ]

    summary = summarize_xsmn_model_runs(rows, ["ben-tre", "vung-tau"])

    assert summary == {"expected": 12, "successful": 10, "missing_provinces": []}
