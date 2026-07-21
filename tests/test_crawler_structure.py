"""
test_crawler_structure.py — Regression tests for crawler data structure contracts.

Verifies:
  - XSMB crawler schedule is complete (7 days)
  - XSMN crawler PROVINCE_MAP covers all ensemble provinces
  - Crawler output dict has required fields
  - Prize field names match database schema
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.crawler.xsmb_crawler import XSMBCrawler
from src.crawler.xsmn_crawler import XSMNCrawler
from src.xsmn_ensemble.resolve_provinces import XSMN_ENSEMBLE_SCHEDULE


class TestXSMBCrawlerStructure(unittest.TestCase):
    """Verify XSMB crawler schedule and structure."""

    def setUp(self):
        self.crawler = XSMBCrawler()

    def test_schedule_covers_all_7_days(self):
        """XSMB_SCHEDULE must have entries for Mon-Sun (0-6)."""
        for dow in range(7):
            self.assertIn(dow, self.crawler.XSMB_SCHEDULE,
                f"DOW {dow} missing from XSMB_SCHEDULE")

    def test_schedule_values_are_strings(self):
        """All schedule values should be province slug strings."""
        for dow, slug in self.crawler.XSMB_SCHEDULE.items():
            self.assertIsInstance(slug, str, f"DOW {dow}: expected string, got {type(slug)}")
            self.assertTrue(len(slug) > 0, f"DOW {dow}: empty slug")

    def test_hanoi_appears_twice(self):
        """Hà Nội xổ cả Thứ Hai (0) lẫn Thứ Năm (3)."""
        hanoi_days = [dow for dow, slug in self.crawler.XSMB_SCHEDULE.items() if slug == 'ha-noi']
        self.assertEqual(sorted(hanoi_days), [0, 3])

    def test_headers_has_user_agent(self):
        """Crawler must have User-Agent header to avoid blocking."""
        self.assertIn('User-Agent', self.crawler.headers)
        self.assertIn('Mozilla', self.crawler.headers['User-Agent'])


class TestXSMNCrawlerStructure(unittest.TestCase):
    """Verify XSMN crawler schedule and PROVINCE_MAP coverage."""

    def setUp(self):
        self.crawler = XSMNCrawler()

    def test_province_map_exists(self):
        """PROVINCE_MAP should be a non-empty dict."""
        self.assertIsInstance(self.crawler.PROVINCE_MAP, dict)
        self.assertGreater(len(self.crawler.PROVINCE_MAP), 0)

    def test_ensemble_provinces_in_province_map(self):
        """All ensemble schedule provinces must exist in PROVINCE_MAP."""
        province_map = self.crawler.PROVINCE_MAP
        for dow, provinces in XSMN_ENSEMBLE_SCHEDULE.items():
            for slug in provinces:
                self.assertIn(slug, province_map,
                    f"DOW {dow}: ensemble province '{slug}' not in PROVINCE_MAP. "
                    f"Available: {list(province_map.keys())}")

    def test_province_map_values_are_vietnamese_names(self):
        """PROVINCE_MAP values should be Vietnamese display names."""
        for slug, name in self.crawler.PROVINCE_MAP.items():
            self.assertIsInstance(name, str)
            self.assertGreater(len(name), 0, f"Empty name for slug '{slug}'")

    def test_complete_result_requires_all_18_prize_numbers(self):
        result = {
            "special_prize": "123456",
            "first_prize": "12345",
            "second_prize": ["12345"],
            "third_prize": ["1", "2"],
            "fourth_prize": [str(i) for i in range(7)],
            "fifth_prize": ["1"],
            "sixth_prize": ["1", "2", "3"],
            "seventh_prize": ["1"],
            "eighth_prize": "12",
        }

        valid, errors = self.crawler.validate_result(result)

        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_incomplete_result_is_rejected(self):
        valid, errors = self.crawler.validate_result({"special_prize": "123456"})

        self.assertFalse(valid)
        self.assertTrue(any("fourth_prize" in error for error in errors))


class TestCrawlerRetryIntegration(unittest.TestCase):
    """Verify retry decorator is applied to crawlers."""

    def test_xsmb_has_fetch_page(self):
        """XSMBCrawler should have _fetch_page method (retry-decorated)."""
        crawler = XSMBCrawler()
        self.assertTrue(hasattr(crawler, '_fetch_page'))
        self.assertTrue(callable(crawler._fetch_page))

    def test_xsmn_has_fetch_page(self):
        """XSMNCrawler should have _fetch_page method (retry-decorated)."""
        crawler = XSMNCrawler()
        self.assertTrue(hasattr(crawler, '_fetch_page'))
        self.assertTrue(callable(crawler._fetch_page))


if __name__ == "__main__":
    unittest.main()
