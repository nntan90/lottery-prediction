"""
test_weekly_report.py — Regression tests for weekly report generation.

Verifies:
  - _week_range calculates correct Mon-Sun ranges
  - _format_vnd formats currency correctly
  - _analyze produces correct summary from raw data
  - _build_xml generates valid XML structure
  - _build_telegram_message generates non-empty message
"""

import os
import sys
import unittest
import tempfile
from datetime import date
from xml.etree.ElementTree import Element

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scripts.weekly_report import (
    _week_range,
    _format_vnd,
    _analyze,
    _build_xml,
    _build_telegram_message,
    _save_xml,
)


class TestWeekRange(unittest.TestCase):
    """Verify week range calculation."""

    def test_sunday_input(self):
        """Sunday input should return Mon-Sun of that week."""
        d = date(2026, 5, 10)  # Sunday
        self.assertEqual(d.weekday(), 6)
        start, end = _week_range(d)
        self.assertEqual(start, date(2026, 5, 4))   # Monday
        self.assertEqual(end, date(2026, 5, 10))     # Sunday
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end.weekday(), 6)

    def test_monday_input(self):
        """Monday input should rewind to previous Sunday."""
        d = date(2026, 5, 11)  # Monday
        start, end = _week_range(d)
        # Previous Sunday = May 10
        self.assertEqual(end, date(2026, 5, 10))
        self.assertEqual(start, date(2026, 5, 4))

    def test_wednesday_input(self):
        """Wednesday should rewind to previous Sunday."""
        d = date(2026, 5, 6)  # Wednesday
        start, end = _week_range(d)
        self.assertEqual(end, date(2026, 5, 3))  # Previous Sunday = May 3
        self.assertEqual(start, date(2026, 4, 27))

    def test_saturday_input(self):
        """Saturday should rewind to previous Sunday."""
        d = date(2026, 5, 9)  # Saturday
        start, end = _week_range(d)
        self.assertEqual(end, date(2026, 5, 3))  # Previous Sunday = May 3

    def test_7_day_span(self):
        """Week range should always be exactly 7 days."""
        for offset in range(7):
            d = date(2026, 5, 4 + offset)
            start, end = _week_range(d)
            delta = (end - start).days
            self.assertEqual(delta, 6, f"Input {d}: span should be 6 days (Mon-Sun)")


class TestFormatVND(unittest.TestCase):
    """Verify VND formatting."""

    def test_millions(self):
        self.assertEqual(_format_vnd(1_500_000), "1.5M")
        self.assertEqual(_format_vnd(-2_300_000), "-2.3M")

    def test_thousands(self):
        self.assertEqual(_format_vnd(42_000), "42K")
        self.assertEqual(_format_vnd(-14_000), "-14K")

    def test_small(self):
        self.assertEqual(_format_vnd(500), "500")
        self.assertEqual(_format_vnd(0), "0")

    def test_exact_million(self):
        self.assertEqual(_format_vnd(1_000_000), "1.0M")


class TestAnalyze(unittest.TestCase):
    """Verify analysis logic from raw data."""

    def _sample_predictions(self):
        return [
            {"prediction_date": "2026-05-05", "region": "XSMB", "province": None,
             "pair_1": 42, "pair_2": 17, "pair_3": 88, "hit": True,
             "matched_pairs": [42], "model_version": "v3"},
            {"prediction_date": "2026-05-05", "region": "XSMN", "province": "tp-hcm",
             "pair_1": 10, "pair_2": 20, "pair_3": 30, "hit": False,
             "matched_pairs": [], "model_version": "ensemble_v3.1"},
            {"prediction_date": "2026-05-06", "region": "XSMB", "province": None,
             "pair_1": 55, "pair_2": 33, "pair_3": 77, "hit": None,
             "matched_pairs": None, "model_version": "v3"},
        ]

    def _sample_profits(self):
        return [
            {"prediction_date": "2026-05-05", "region": "xsmb", "province": "all",
             "pair": 42, "hit_count": 1, "cost": 46000, "revenue": 160000, "profit": 114000},
            {"prediction_date": "2026-05-05", "region": "xsmn", "province": "tp-hcm",
             "pair": 10, "hit_count": 0, "cost": 42000, "revenue": 0, "profit": -42000},
        ]

    def _sample_crawler_logs(self):
        return [
            {"crawl_date": "2026-05-05", "region": "XSMB", "status": "success",
             "records_inserted": 1, "error_message": None},
            {"crawl_date": "2026-05-05", "region": "XSMN", "status": "success",
             "records_inserted": 2, "error_message": None},
            {"crawl_date": "2026-05-06", "region": "XSMB", "status": "failed",
             "records_inserted": 0, "error_message": "Timeout"},
        ]

    def _sample_agent_actions(self):
        return [
            {"action_date": "2026-05-05", "region": "XSMN", "province": "tp-hcm",
             "action_type": "retrain_triggered", "strategy": "boost_estimators",
             "reason": "3 miss liên tiếp"},
            {"action_date": "2026-05-05", "region": "XSMB", "province": None,
             "action_type": "no_action", "strategy": None, "reason": "Hit today"},
        ]

    def test_prediction_counts(self):
        analysis = _analyze(
            self._sample_predictions(), [], [], [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )
        self.assertEqual(analysis["predictions"]["total"], 3)
        self.assertEqual(analysis["predictions"]["verified"], 2)
        self.assertEqual(analysis["predictions"]["hits"], 1)
        self.assertEqual(analysis["predictions"]["misses"], 1)
        self.assertEqual(analysis["predictions"]["unverified"], 1)
        self.assertAlmostEqual(analysis["predictions"]["hit_rate"], 50.0)

    def test_region_breakdown(self):
        analysis = _analyze(
            self._sample_predictions(), [], [], [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )
        self.assertIn("XSMB", analysis["predictions"]["by_region"])
        self.assertIn("XSMN", analysis["predictions"]["by_region"])
        self.assertEqual(analysis["predictions"]["by_region"]["XSMB"]["hit"], 1)
        self.assertEqual(analysis["predictions"]["by_region"]["XSMN"]["miss"], 1)

    def test_profit_calculation(self):
        analysis = _analyze(
            [], self._sample_profits(), [], [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )
        self.assertEqual(analysis["profit"]["total_cost"], 88000)
        self.assertEqual(analysis["profit"]["total_revenue"], 160000)
        self.assertEqual(analysis["profit"]["total_profit"], 72000)

    def test_crawler_stats(self):
        analysis = _analyze(
            [], [], self._sample_crawler_logs(), [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )
        self.assertEqual(analysis["crawler"]["total_jobs"], 3)
        self.assertEqual(analysis["crawler"]["success"], 2)
        self.assertEqual(analysis["crawler"]["failed"], 1)
        self.assertAlmostEqual(analysis["crawler"]["success_rate"], 66.7, places=0)

    def test_agent_stats(self):
        analysis = _analyze(
            [], [], [], self._sample_agent_actions(), [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )
        self.assertEqual(analysis["agent"]["total_actions"], 2)
        self.assertEqual(analysis["agent"]["retrain_count"], 1)
        self.assertEqual(analysis["agent"]["no_action_count"], 1)
        self.assertIn("boost_estimators", analysis["agent"]["strategy_distribution"])

    def test_empty_data(self):
        """Should handle all empty data gracefully."""
        analysis = _analyze([], [], [], [], [], [], date(2026, 5, 4), date(2026, 5, 10))
        self.assertEqual(analysis["predictions"]["total"], 0)
        self.assertEqual(analysis["predictions"]["hit_rate"], 0)
        self.assertEqual(analysis["profit"]["total_profit"], 0)
        self.assertEqual(analysis["crawler"]["total_jobs"], 0)


class TestBuildXML(unittest.TestCase):
    """Verify XML generation."""

    def _make_analysis(self):
        return _analyze(
            [{"prediction_date": "2026-05-05", "region": "XSMB", "province": None,
              "pair_1": 42, "pair_2": 17, "pair_3": 88, "hit": True,
              "matched_pairs": [42], "model_version": "v3"}],
            [{"prediction_date": "2026-05-05", "region": "xsmb", "province": "all",
              "pair": 42, "hit_count": 1, "cost": 46000, "revenue": 160000, "profit": 114000}],
            [{"crawl_date": "2026-05-05", "region": "XSMB", "status": "success",
              "records_inserted": 1, "error_message": None}],
            [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )

    def test_xml_root_element(self):
        root = _build_xml(self._make_analysis())
        self.assertEqual(root.tag, "WeeklyReport")
        self.assertIn("generated_at", root.attrib)

    def test_xml_has_all_sections(self):
        root = _build_xml(self._make_analysis())
        tags = [child.tag for child in root]
        self.assertIn("ReportPeriod", tags)
        self.assertIn("Predictions", tags)
        self.assertIn("Profit", tags)
        self.assertIn("Crawler", tags)
        self.assertIn("RetrainAgent", tags)
        self.assertIn("ActiveModels", tags)

    def test_xml_save(self):
        """XML should save to file successfully."""
        root = _build_xml(self._make_analysis())
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _save_xml(root, tmpdir, date(2026, 5, 10))
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith("weekly_20260510.xml"))
            # Verify file is valid XML
            with open(path, "r") as f:
                content = f.read()
            self.assertIn("<?xml", content)
            self.assertIn("WeeklyReport", content)


class TestBuildTelegramMessage(unittest.TestCase):
    """Verify Telegram message generation."""

    def _make_analysis(self):
        return _analyze(
            [{"prediction_date": "2026-05-05", "region": "XSMB", "province": None,
              "pair_1": 42, "pair_2": 17, "pair_3": 88, "hit": True,
              "matched_pairs": [42], "model_version": "v3"}],
            [], [], [], [], [],
            date(2026, 5, 4), date(2026, 5, 10),
        )

    def test_message_not_empty(self):
        msg = _build_telegram_message(self._make_analysis())
        self.assertTrue(len(msg) > 100)

    def test_message_contains_sections(self):
        msg = _build_telegram_message(self._make_analysis())
        self.assertIn("BÁO CÁO TUẦN", msg)
        self.assertIn("DỰ ĐOÁN", msg)
        self.assertIn("TÀI CHÍNH", msg)
        self.assertIn("CRAWLER", msg)
        self.assertIn("RETRAIN AGENT", msg)
        self.assertIn("MODELS", msg)

    def test_message_fits_telegram_limit(self):
        """Telegram max message = 4096 chars."""
        msg = _build_telegram_message(self._make_analysis())
        self.assertLess(len(msg), 4096)


if __name__ == "__main__":
    unittest.main()
