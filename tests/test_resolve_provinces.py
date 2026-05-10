"""
test_resolve_provinces.py — Regression tests for XSMN province scheduling.

Verifies:
  - Correct 2 provinces returned for each day of week (Mon-Sun)
  - ENV var TARGET_PROVINCES override works
  - All province slugs match xsmn_crawler PROVINCE_MAP format
  - TP.HCM appears on both Monday and Saturday
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.xsmn_ensemble.resolve_provinces import (
    get_target_provinces,
    get_dow_label,
    XSMN_ENSEMBLE_SCHEDULE,
    DOW_NAMES_VN,
)


class TestProvinceSchedule(unittest.TestCase):
    """Verify province schedule is complete and correct."""

    def test_all_7_days_covered(self):
        """Every DOW 0-6 must have a province list."""
        for dow in range(7):
            provinces = XSMN_ENSEMBLE_SCHEDULE.get(dow)
            self.assertIsNotNone(provinces, f"DOW {dow} missing from schedule")
            self.assertGreater(len(provinces), 0, f"DOW {dow} has empty province list")

    def test_each_day_has_exactly_2_provinces(self):
        """Each day should have exactly 2 focus provinces."""
        for dow in range(7):
            provinces = XSMN_ENSEMBLE_SCHEDULE[dow]
            self.assertEqual(len(provinces), 2, f"DOW {dow} should have 2 provinces, got {len(provinces)}")

    def test_monday_correct(self):
        """Monday = TP.HCM + Đồng Tháp."""
        d = date(2026, 5, 4)  # Monday
        self.assertEqual(d.weekday(), 0)
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["tp-hcm", "dong-thap"])

    def test_tuesday_correct(self):
        d = date(2026, 5, 5)  # Tuesday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["vung-tau", "ben-tre"])

    def test_wednesday_correct(self):
        d = date(2026, 5, 6)  # Wednesday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["dong-nai", "can-tho"])

    def test_thursday_correct(self):
        d = date(2026, 5, 7)  # Thursday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["tay-ninh", "an-giang"])

    def test_friday_correct(self):
        d = date(2026, 5, 8)  # Friday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["vinh-long", "binh-duong"])

    def test_saturday_correct(self):
        d = date(2026, 5, 9)  # Saturday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["tp-hcm", "long-an"])

    def test_sunday_correct(self):
        d = date(2026, 5, 10)  # Sunday
        provinces = get_target_provinces(d)
        self.assertEqual(provinces, ["tien-giang", "kien-giang"])

    def test_tphcm_appears_twice_per_week(self):
        """TP.HCM xổ cả Thứ Hai (DOW=0) lẫn Thứ Bảy (DOW=5)."""
        tphcm_days = [dow for dow, provs in XSMN_ENSEMBLE_SCHEDULE.items() if "tp-hcm" in provs]
        self.assertEqual(sorted(tphcm_days), [0, 5])

    def test_province_slugs_use_dashes(self):
        """All slugs must use dashes, not underscores (match xsmn_crawler format)."""
        for dow, provs in XSMN_ENSEMBLE_SCHEDULE.items():
            for slug in provs:
                self.assertNotIn("_", slug, f"DOW {dow}: slug '{slug}' uses underscore instead of dash")
                self.assertEqual(slug, slug.lower(), f"DOW {dow}: slug '{slug}' not lowercase")


class TestProvinceEnvOverride(unittest.TestCase):
    """Verify TARGET_PROVINCES env var overrides schedule."""

    def test_env_override(self):
        """TARGET_PROVINCES env var should override static schedule."""
        d = date(2026, 5, 4)  # Monday → normally tp-hcm, dong-thap
        os.environ["TARGET_PROVINCES"] = "can-tho,ben-tre"
        try:
            provinces = get_target_provinces(d)
            self.assertEqual(provinces, ["can-tho", "ben-tre"])
        finally:
            del os.environ["TARGET_PROVINCES"]

    def test_env_empty_falls_back_to_schedule(self):
        """Empty env var should fall back to static schedule."""
        d = date(2026, 5, 4)
        os.environ["TARGET_PROVINCES"] = ""
        try:
            provinces = get_target_provinces(d)
            self.assertEqual(provinces, ["tp-hcm", "dong-thap"])
        finally:
            del os.environ["TARGET_PROVINCES"]

    def test_env_whitespace_only_falls_back(self):
        os.environ["TARGET_PROVINCES"] = "   "
        try:
            d = date(2026, 5, 4)
            provinces = get_target_provinces(d)
            self.assertEqual(provinces, ["tp-hcm", "dong-thap"])
        finally:
            del os.environ["TARGET_PROVINCES"]


class TestDowLabel(unittest.TestCase):
    """Verify Vietnamese day labels."""

    def test_all_7_labels(self):
        self.assertEqual(len(DOW_NAMES_VN), 7)

    def test_monday_label(self):
        d = date(2026, 5, 4)
        self.assertEqual(get_dow_label(d), "Thứ Hai")

    def test_sunday_label(self):
        d = date(2026, 5, 10)
        self.assertEqual(get_dow_label(d), "Chủ Nhật")


if __name__ == "__main__":
    unittest.main()
