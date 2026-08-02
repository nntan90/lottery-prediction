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
from types import SimpleNamespace
from xml.etree.ElementTree import Element

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scripts.weekly_report import (
    _week_range,
    _format_vnd,
    _analyze,
    _collect_shadow_predictions,
    _seven_day_performance,
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


class TestSevenDayPerformance(unittest.TestCase):
    """Verify canonical daily KPI aggregation for production and shadow scopes."""

    @staticmethod
    def _ensemble_row(day, region, *, province, matched_pairs):
        return {
            "prediction_date": day,
            "region": region,
            "province": province,
            "pair_1": 10,
            "pair_2": 20,
            "pair_3": 30,
            "model_version": "ensemble_test",
            "matched_pairs": matched_pairs,
            "hit": len(set(matched_pairs)) >= 2,
        }

    def test_counts_one_canonical_verdict_per_day_and_ignores_provinces(self):
        predictions = []
        for offset in range(7):
            day = f"2026-07-{20 + offset:02d}"
            predictions.append(
                self._ensemble_row(
                    day,
                    "XSMB",
                    province=None,
                    matched_pairs=[10],
                )
            )
            noncanonical_xsmb = self._ensemble_row(
                day,
                "XSMB",
                province="all",
                matched_pairs=[10, 20, 30],
            )
            noncanonical_xsmb["created_at"] = "9999-12-31T23:59:59"
            predictions.append(noncanonical_xsmb)
            predictions.append(
                self._ensemble_row(
                    day,
                    "XSMN",
                    province="all",
                    matched_pairs=[10, 20] if offset in {0, 4} else [10],
                )
            )
            predictions.append(
                self._ensemble_row(
                    day,
                    "XSMN",
                    province="tp-hcm",
                    matched_pairs=[10, 20, 30],
                )
            )
            stale_metadata = self._ensemble_row(
                day,
                "XSMN",
                province="all",
                matched_pairs=[10, 20, 30],
            )
            stale_metadata["model_version"] = "single_model"
            stale_metadata["ensemble_method"] = "manual"
            stale_metadata["created_at"] = "9999-12-31T23:59:59"
            predictions.append(stale_metadata)

        performance = _seven_day_performance(
            predictions,
            [],
            date(2026, 7, 20),
            date(2026, 7, 26),
        )

        xsmb = performance["scopes"]["xsmb"]
        xsmn = performance["scopes"]["xsmn_consensus"]
        self.assertEqual(
            (xsmb["hit_days"], xsmb["prediction_days"], xsmb["verified_days"]),
            (0, 7, 7),
        )
        self.assertEqual(
            (xsmn["hit_days"], xsmn["prediction_days"], xsmn["verified_days"]),
            (2, 7, 7),
        )

        analysis = _analyze(
            predictions,
            [],
            [],
            [],
            [],
            [],
            date(2026, 7, 20),
            date(2026, 7, 26),
        )
        message = _build_telegram_message(analysis)
        self.assertIn("XSMB: <b>0/7 ngày trúng</b> · verify 7/7", message)
        self.assertIn(
            "XSMN đồng thuận: <b>2/7 ngày trúng</b> · verify 7/7",
            message,
        )

    def test_shadow_legacy_any_hit_does_not_become_combo_hit(self):
        shadow_rows = [
            {
                "prediction_date": "2026-07-20",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "status": "success",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "hit": True,
                "hit_count": 99,
                "matched_pairs": [10],
            },
            {
                "prediction_date": "2026-07-21",
                "region": "XSMN",
                "province": "all",
                "model_name": "cmr_shadow",
                "status": "uncalibrated",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "hit": True,
                "matched_pairs": [10, 20],
            },
        ]

        performance = _seven_day_performance(
            [],
            shadow_rows,
            date(2026, 7, 20),
            date(2026, 7, 26),
        )

        ddt = performance["scopes"]["ddt"]
        cmr = performance["scopes"]["cmr"]
        self.assertEqual(
            (ddt["hit_days"], ddt["prediction_days"], ddt["verified_days"]),
            (0, 1, 1),
        )
        self.assertEqual(
            (cmr["hit_days"], cmr["prediction_days"], cmr["verified_days"]),
            (1, 1, 1),
        )

    def test_shadow_primary_combo_fields_count_two_of_three(self):
        shadow_rows = [
            {
                "prediction_date": "2026-07-20",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "status": "success",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "combo_hit": True,
            },
            {
                "prediction_date": "2026-07-21",
                "region": "XSMN",
                "province": "all",
                "model_name": "cmr_shadow",
                "status": "success",
                "pair_1": 11,
                "pair_2": 21,
                "pair_3": 31,
                "hit_count": 2,
            },
            {
                "prediction_date": "2026-07-22",
                "region": "XSMN",
                "province": "all",
                "model_name": "relationship",
                "status": "success",
                "pair_1": 11,
                "pair_2": 25,
                "pair_3": 3,
                "hit_count": 2,
            },
        ]

        performance = _seven_day_performance(
            [],
            shadow_rows,
            date(2026, 7, 20),
            date(2026, 7, 26),
        )

        self.assertEqual(performance["scopes"]["ddt"]["hit_days"], 1)
        self.assertEqual(performance["scopes"]["cmr"]["hit_days"], 1)
        self.assertEqual(performance["scopes"]["relationship"]["hit_days"], 1)

    def test_missing_and_unverified_days_are_coverage_not_misses(self):
        shadow_rows = [
            {
                "prediction_date": "2026-07-20",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "status": "success",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "hit": False,
                "matched_pairs": [],
            },
            {
                "prediction_date": "2026-07-21",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "status": "success",
                "pair_1": 11,
                "pair_2": 21,
                "pair_3": 31,
                "hit": None,
                "matched_pairs": None,
            },
            {
                "prediction_date": "2026-07-22",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "status": "success",
                "pair_1": 12,
                "pair_2": 12,
                "pair_3": 32,
                "hit": True,
                "matched_pairs": [12],
            },
        ]

        ddt = _seven_day_performance(
            [],
            shadow_rows,
            date(2026, 7, 20),
            date(2026, 7, 26),
        )["scopes"]["ddt"]

        self.assertEqual(ddt["period_days"], 7)
        self.assertEqual(ddt["prediction_days"], 2)
        self.assertEqual(ddt["verified_days"], 1)
        self.assertEqual(ddt["hit_days"], 0)

        analysis = _analyze(
            [
                {
                    "prediction_date": "2026-07-20",
                    "region": "XSMB",
                    "province": None,
                    "model_version": "ensemble_test",
                    "pair_1": 10,
                    "pair_2": 20,
                    "pair_3": 30,
                    "hit": None,
                    "matched_pairs": None,
                }
            ],
            [],
            [],
            [],
            [],
            [],
            date(2026, 7, 20),
            date(2026, 7, 26),
            shadow_rows,
        )
        message = _build_telegram_message(analysis)
        self.assertIn("⚪ XSMB: <b>0/7 ngày trúng</b>", message)
        self.assertIn("⚪ DDT: <b>0/7 ngày trúng</b>", message)

    def test_duplicate_date_prefers_verified_row_deterministically(self):
        rows = [
            {
                "id": 2,
                "created_at": "2026-07-20T09:00:00",
                "prediction_date": "2026-07-20",
                "region": "XSMN",
                "province": "all",
                "model_name": "cmr_shadow",
                "status": "success",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "hit": None,
                "matched_pairs": None,
            },
            {
                "id": 1,
                "created_at": "2026-07-20T08:00:00",
                "prediction_date": "2026-07-20",
                "region": "XSMN",
                "province": "all",
                "model_name": "cmr_shadow",
                "status": "success",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "hit": True,
                "matched_pairs": [10, 20],
            },
        ]

        cmr = _seven_day_performance(
            [],
            rows,
            date(2026, 7, 20),
            date(2026, 7, 26),
        )["scopes"]["cmr"]

        self.assertEqual(cmr["prediction_days"], 1)
        self.assertEqual(cmr["verified_days"], 1)
        self.assertEqual(cmr["hit_days"], 1)

    def test_shadow_collection_failure_is_non_fatal(self):
        class FailingSupabase:
            def table(self, _name):
                raise RuntimeError("model_predictions unavailable")

        db = type("FakeDB", (), {"supabase": FailingSupabase()})()

        self.assertEqual(
            _collect_shadow_predictions(
                db,
                date(2026, 7, 20),
                date(2026, 7, 26),
            ),
            [],
        )

    def test_shadow_collection_uses_canonical_filters(self):
        calls = []
        expected = [{"model_name": "ddt_shadow"}]

        class Query:
            def _record(self, method, *args, **kwargs):
                calls.append((method, args, kwargs))
                return self

            def select(self, *args, **kwargs):
                return self._record("select", *args, **kwargs)

            def gte(self, *args, **kwargs):
                return self._record("gte", *args, **kwargs)

            def lte(self, *args, **kwargs):
                return self._record("lte", *args, **kwargs)

            def eq(self, *args, **kwargs):
                return self._record("eq", *args, **kwargs)

            def in_(self, *args, **kwargs):
                return self._record("in_", *args, **kwargs)

            def order(self, *args, **kwargs):
                return self._record("order", *args, **kwargs)

            def execute(self):
                return SimpleNamespace(data=expected)

        class Supabase:
            def table(self, name):
                calls.append(("table", (name,), {}))
                return Query()

        db = type("FakeDB", (), {"supabase": Supabase()})()
        rows = _collect_shadow_predictions(
            db,
            date(2026, 7, 20),
            date(2026, 7, 26),
        )

        self.assertEqual(rows, expected)
        self.assertIn(("eq", ("region", "XSMN"), {}), calls)
        self.assertIn(("eq", ("province", "all"), {}), calls)
        self.assertIn(
            (
                "in_",
                ("model_name", ["cmr_shadow", "ddt_shadow", "relationship"]),
                {},
            ),
            calls,
        )
        self.assertIn(("gte", ("prediction_date", "2026-07-20"), {}), calls)
        self.assertIn(("lte", ("prediction_date", "2026-07-26"), {}), calls)


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
        analysis = self._make_analysis()
        root = _build_xml(analysis)
        tags = [child.tag for child in root]
        self.assertIn("ReportPeriod", tags)
        self.assertIn("Predictions", tags)
        self.assertIn("SevenDayPerformance", tags)
        self.assertIn("Profit", tags)
        self.assertIn("Crawler", tags)
        self.assertIn("RetrainAgent", tags)
        self.assertIn("ActiveModels", tags)

        performance = root.find("SevenDayPerformance")
        self.assertIsNotNone(performance)
        self.assertEqual(performance.findtext("Criterion"), "at_least_2_of_3")
        scopes = {
            scope.attrib["key"]: scope
            for scope in performance.findall("Scope")
        }
        self.assertEqual(
            set(scopes),
            {"xsmb", "xsmn_consensus", "ddt", "cmr", "relationship"},
        )
        self.assertEqual(scopes["xsmb"].findtext("PeriodDays"), "7")
        self.assertEqual(scopes["xsmb"].findtext("PredictionDays"), "0")

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
        self.assertIn("HIỆU QUẢ 7 NGÀY", msg)
        self.assertIn("XSMB", msg)
        self.assertIn("XSMN đồng thuận", msg)
        self.assertIn("DDT", msg)
        self.assertIn("CMR", msg)
        self.assertIn("Relationship", msg)
        self.assertIn("TÀI CHÍNH", msg)
        self.assertIn("CRAWLER", msg)
        self.assertIn("RETRAIN AGENT", msg)
        self.assertIn("MODELS", msg)

    def test_message_fits_telegram_limit(self):
        """Telegram max message = 4096 chars."""
        msg = _build_telegram_message(self._make_analysis())
        self.assertLess(len(msg), 4096)

    def test_message_uses_fixed_seven_day_denominator_and_coverage(self):
        analysis = _analyze(
            [],
            [],
            [],
            [],
            [],
            [],
            date(2026, 7, 20),
            date(2026, 7, 26),
            [
                {
                    "prediction_date": "2026-07-20",
                    "region": "XSMN",
                    "province": "all",
                    "model_name": "ddt_shadow",
                    "status": "success",
                    "pair_1": 10,
                    "pair_2": 20,
                    "pair_3": 30,
                    "hit": True,
                    "matched_pairs": [10],
                }
            ],
        )

        msg = _build_telegram_message(analysis)

        self.assertIn("DDT: <b>0/7 ngày trúng</b> · chạy 1/7 · verify 1/7", msg)
        self.assertIn("CMR: <b>0/7 ngày trúng</b> · chạy 0/7 · verify 0/7", msg)
        self.assertIn(
            "Relationship: <b>0/7 ngày trúng</b> · chạy 0/7 · verify 0/7",
            msg,
        )


if __name__ == "__main__":
    unittest.main()
