"""
test_verify_profit.py — Regression tests for profit calculation logic in verify_v3.py.

Verifies:
  - Cost calculation: COST_DA_VONG = 328000 (Đá vòng 3 con)
  - Revenue based on xiên (betting circles):
    - 0-1 matched: 0 vòng → revenue = 0
    - 2 matched: 1 vòng xiên 2 → revenue = 1,100,000
    - 3 matched: 3 vòng (xiên 2 combinations) → revenue = 3,300,000
  - Profit = revenue - cost
  - Edge case: None pairs are skipped
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scripts.verify_v3 import (
    calculate_station_profit,
    COST_DA_VONG,
    REVENUE_PER_VONG,
)


class TestProfitConstants(unittest.TestCase):
    """Verify pricing constants are correct."""

    def test_cost_da_vong(self):
        """Đá vòng 3 con costs 328000."""
        self.assertEqual(COST_DA_VONG, 328000)

    def test_revenue_per_vong(self):
        """Revenue per xiên vòng (1 vòng) = 1,100,000."""
        self.assertEqual(REVENUE_PER_VONG, 1100000)


class TestCalculateStationProfit(unittest.TestCase):
    """Tests for calculate_station_profit function (Đá vòng model)."""

    def test_no_matched_pairs(self):
        """0 matched pairs → 0 vòng → cost only, no revenue."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 99}, {"tail_2d": 88}, {"tail_2d": 77}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["pair"], -1)
        self.assertEqual(results[0]["hit_count"], 0)
        self.assertEqual(results[0]["cost"], COST_DA_VONG)
        self.assertEqual(results[0]["revenue"], 0)
        self.assertEqual(results[0]["profit"], -COST_DA_VONG)

    def test_one_matched_pair(self):
        """1 matched pair → 0 vòng (need 2+) → cost only."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 88}, {"tail_2d": 77}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        self.assertEqual(results[0]["hit_count"], 1)
        self.assertEqual(results[0]["revenue"], 0)  # Need 2+ matched
        self.assertEqual(results[0]["profit"], -COST_DA_VONG)

    def test_two_matched_pairs(self):
        """2 matched pairs → 1 vòng xiên 2 → revenue = 1,100,000."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 20}, {"tail_2d": 88}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        self.assertEqual(results[0]["hit_count"], 2)
        self.assertEqual(results[0]["revenue"], 1 * REVENUE_PER_VONG)
        self.assertEqual(results[0]["profit"], REVENUE_PER_VONG - COST_DA_VONG)

    def test_three_matched_pairs(self):
        """3 matched pairs → 3 vòng (C(3,2)=3) → revenue = 3,300,000."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 20}, {"tail_2d": 30}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        self.assertEqual(results[0]["hit_count"], 3)
        self.assertEqual(results[0]["revenue"], 3 * REVENUE_PER_VONG)
        self.assertEqual(results[0]["profit"], 3 * REVENUE_PER_VONG - COST_DA_VONG)

    def test_xsmn_two_matched(self):
        """XSMN uses same Đá vòng model → same profit calculation."""
        pairs = [42, 17, 88]
        tail_rows = [{"tail_2d": 42}, {"tail_2d": 17}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        
        self.assertEqual(results[0]["hit_count"], 2)
        self.assertEqual(results[0]["revenue"], 1 * REVENUE_PER_VONG)
        self.assertEqual(results[0]["cost"], COST_DA_VONG)

    def test_none_pair_skipped(self):
        """Pairs with None value should be skipped in matched count."""
        pairs = [10, None, 30]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 30}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        # Only 2 valid pairs (10, 30), both matched
        self.assertEqual(results[0]["hit_count"], 2)
        self.assertEqual(results[0]["revenue"], 1 * REVENUE_PER_VONG)

    def test_duplicate_pairs_treated_as_set(self):
        """Duplicate pairs should be treated as a set (deduplicated)."""
        pairs = [10, 10, 10]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 20}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        # set(10, 10, 10) = {10}, only 1 unique pair matched
        self.assertEqual(results[0]["hit_count"], 1)
        self.assertEqual(results[0]["revenue"], 0)  # Need 2+ matched

    def test_multiple_occurrences_same_pair(self):
        """If same pair appears multiple times in tails, count as 1 match."""
        pairs = [10, 20, 30]
        tail_rows = [
            {"tail_2d": 10},
            {"tail_2d": 10},  # Duplicate
            {"tail_2d": 20},
        ]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        # tail_set = {10, 20}, 2 unique matches
        self.assertEqual(results[0]["hit_count"], 2)
        self.assertEqual(results[0]["revenue"], 1 * REVENUE_PER_VONG)

    def test_result_structure(self):
        """Verify result dict structure."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 20}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        self.assertEqual(len(results), 1)
        result = results[0]
        
        # Check all required fields exist
        self.assertIn("pair", result)
        self.assertIn("hit_count", result)
        self.assertIn("cost", result)
        self.assertIn("revenue", result)
        self.assertIn("profit", result)
        
        # pair should be -1 (combo, not individual)
        self.assertEqual(result["pair"], -1)
        
        # profit = revenue - cost
        self.assertEqual(result["profit"], result["revenue"] - result["cost"])


if __name__ == "__main__":
    unittest.main()
