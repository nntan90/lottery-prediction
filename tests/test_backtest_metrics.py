"""
Regression tests for walk-forward backtest metrics.
"""

import os
import sys
import math
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analytics.backtest import (
    build_backtest_report,
    format_backtest_report,
    random_at_least_hits_probability,
    random_hit_probability,
    summarize_model_contribution,
    summarize_predictions,
    summarize_profit,
)


class TestRandomBaseline(unittest.TestCase):
    def test_random_hit_probability_for_top3(self):
        prob = random_hit_probability(tail_count=10, picks=3)
        self.assertAlmostEqual(prob, 1 - (90 * 89 * 88) / (100 * 99 * 98), places=6)

    def test_random_hit_probability_edges(self):
        self.assertEqual(random_hit_probability(0, 3), 0.0)
        self.assertEqual(random_hit_probability(100, 3), 1.0)

    def test_random_two_of_three_probability(self):
        probability = random_at_least_hits_probability(10, picks=3, min_hits=2)
        expected = (
            math.comb(10, 2) * math.comb(90, 1) + math.comb(10, 3)
        ) / math.comb(100, 3)
        self.assertAlmostEqual(probability, expected, places=8)


class TestPredictionSummary(unittest.TestCase):
    def test_hit_rates_and_lift_are_computed_against_tail_set_baseline(self):
        predictions = [
            {
                "prediction_date": "2026-05-01",
                "region": "XSMB",
                "province": None,
                "pair_1": 1,
                "pair_2": 2,
                "pair_3": 3,
                "tail_set": [1, 9, 10],
            },
            {
                "prediction_date": "2026-05-02",
                "region": "XSMB",
                "province": None,
                "pair_1": 4,
                "pair_2": 5,
                "pair_3": 6,
                "tail_set": [7, 8, 9],
            },
        ]

        summary = summarize_predictions(predictions)
        overall = summary["overall"]

        self.assertEqual(overall["predictions"], 2)
        self.assertEqual(overall["hit_1"], 1)
        self.assertEqual(overall["hit_3"], 1)
        self.assertGreater(overall["baseline_hit_3_rate"], 0)
        self.assertGreater(overall["lift_hit_3"], 1)


class TestProfitSummary(unittest.TestCase):
    def test_roi_is_computed_from_profit_tracking_rows(self):
        summary = summarize_profit([
            {"region": "xsmb", "province": "all", "cost": 100, "revenue": 150, "profit": 50},
            {"region": "xsmb", "province": "all", "cost": 100, "revenue": 0, "profit": -100},
        ])

        self.assertEqual(summary["overall"]["cost"], 200)
        self.assertEqual(summary["overall"]["profit"], -50)
        self.assertEqual(summary["overall"]["roi"], -0.25)


class TestModelContribution(unittest.TestCase):
    def test_model_overlap_uses_global_xsmn_fallback(self):
        predictions = [
            {
                "prediction_date": "2026-05-01",
                "region": "XSMN",
                "province": "all",
                "pair_1": 10,
                "pair_2": 20,
                "pair_3": 30,
                "matched_pairs": [20],
            }
        ]
        logs = [
            {
                "prediction_date": "2026-05-01",
                "region": "XSMN",
                "province": "tp-hcm",
                "model_name": "frequency",
                "status": "success",
                "pair_1": 20,
                "pair_2": 99,
                "pair_3": 98,
                "pair_4": 97,
                "pair_5": 96,
            }
        ]

        contribution = summarize_model_contribution(predictions, logs)
        self.assertEqual(contribution["frequency"]["successful_logs"], 1)
        self.assertEqual(contribution["frequency"]["avg_final_overlap"], 1.0)
        self.assertEqual(contribution["frequency"]["hit_overlap_pairs"], 1)


class TestFullReport(unittest.TestCase):
    def test_build_backtest_report_shape(self):
        report = build_backtest_report([], [], [])
        self.assertIn("prediction_metrics", report)
        self.assertIn("profit_metrics", report)
        self.assertIn("rolling_hit_3", report)
        self.assertIn("model_contribution", report)

    def test_region_report_labels_combo_baseline(self):
        report = build_backtest_report([
            {
                "prediction_date": "2026-05-01",
                "region": "XSMN",
                "province": "all",
                "pair_1": 1,
                "pair_2": 2,
                "pair_3": 3,
                "tail_set": [1, 2],
            }
        ])

        rendered = format_backtest_report(report)

        self.assertIn("random>=2/3=", rendered)
        self.assertIn("lift>=2/3=", rendered)


class TestBacktestQuery(unittest.TestCase):
    def test_query_range_paginates_past_postgrest_default_cap(self):
        from src.scripts.backtest_walk_forward import _query_range

        class Query:
            def __init__(self, rows):
                self.rows = rows
                self.start = 0
                self.end = 999

            def select(self, *args): return self
            def gte(self, *args): return self
            def lte(self, *args): return self
            def eq(self, *args): return self

            def range(self, start, end):
                self.start, self.end = start, end
                return self

            def execute(self):
                result = type("Result", (), {})()
                result.data = self.rows[self.start:self.end + 1]
                return result

        rows = [{"id": idx} for idx in range(1005)]
        supabase = type("Supabase", (), {"table": lambda self, name: Query(rows)})()
        db = type("DB", (), {"supabase": supabase})()

        result = _query_range(
            db,
            "prediction_results",
            "prediction_date",
            date(2026, 1, 1),
            date(2026, 7, 1),
            "XSMN",
        )

        self.assertEqual(len(result), 1005)
        self.assertEqual(result[-1]["id"], 1004)


if __name__ == "__main__":
    unittest.main()
