"""
test_verify_profit.py — Regression tests for profit calculation logic in verify_v3.py.

Verifies:
  - Cost calculation per region/tier
  - Revenue with 0 hits, 1 hit, multiple hits
  - Profit = revenue - cost
  - XSMB vs XSMN pricing constants
  - Edge case: pair=None is skipped
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scripts.verify_v3 import (
    calculate_station_profit,
    XSMB_TIER_POINTS, XSMB_COST_PER_POINT, XSMB_REVENUE_PER_HIT_POINT,
    XSMN_TIER_POINTS, XSMN_COST_PER_POINT, XSMN_REVENUE_PER_HIT_POINT,
)


class TestProfitConstants(unittest.TestCase):
    """Verify pricing constants are correct."""

    def test_xsmb_tier_points(self):
        """XSMB: pair_1=2đ, pair_2=1đ, pair_3=1đ."""
        self.assertEqual(XSMB_TIER_POINTS, [2, 1, 1])

    def test_xsmn_tier_points(self):
        """XSMN: pair_1=3đ, pair_2=2đ, pair_3=2đ."""
        self.assertEqual(XSMN_TIER_POINTS, [3, 2, 2])

    def test_xsmb_pricing(self):
        self.assertEqual(XSMB_COST_PER_POINT, 23000)
        self.assertEqual(XSMB_REVENUE_PER_HIT_POINT, 80000)

    def test_xsmn_pricing(self):
        self.assertEqual(XSMN_COST_PER_POINT, 14000)
        self.assertEqual(XSMN_REVENUE_PER_HIT_POINT, 70000)


class TestCalculateStationProfit(unittest.TestCase):
    """Tests for calculate_station_profit function."""

    def test_xsmn_no_hit(self):
        """XSMN pair that doesn't appear in tails → cost only, no revenue."""
        pairs = [42, 17, 88]
        tail_rows = [{"tail_2d": 10}, {"tail_2d": 20}, {"tail_2d": 30}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        
        self.assertEqual(len(results), 3)
        # pair_1: 3 * 14000 = 42000 cost, 0 revenue
        self.assertEqual(results[0]["pair"], 42)
        self.assertEqual(results[0]["cost"], 3 * 14000)
        self.assertEqual(results[0]["revenue"], 0)
        self.assertEqual(results[0]["profit"], -3 * 14000)

    def test_xsmn_single_hit(self):
        """XSMN pair appearing once → cost + 1x revenue."""
        pairs = [42, 17, 88]
        tail_rows = [{"tail_2d": 42}, {"tail_2d": 20}, {"tail_2d": 30}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        
        # pair_1 (42): cost=3*14000=42000, rev=3*1*70000=210000, profit=168000
        self.assertEqual(results[0]["hit_count"], 1)
        self.assertEqual(results[0]["cost"], 42000)
        self.assertEqual(results[0]["revenue"], 210000)
        self.assertEqual(results[0]["profit"], 168000)

    def test_xsmn_multiple_hits(self):
        """XSMN pair appearing 3 times → 3x revenue."""
        pairs = [42, 17, 88]
        tail_rows = [{"tail_2d": 42}, {"tail_2d": 42}, {"tail_2d": 42}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        
        # pair_1 (42): rev = 3pts * 3occurrences * 70000 = 630000
        self.assertEqual(results[0]["hit_count"], 3)
        self.assertEqual(results[0]["revenue"], 3 * 3 * 70000)

    def test_xsmb_single_hit(self):
        """XSMB pair appearing once → different pricing."""
        pairs = [42, 17, 88]
        tail_rows = [{"tail_2d": 42}]
        
        results = calculate_station_profit("XSMB", pairs, tail_rows)
        
        # pair_1: cost=2*23000=46000, rev=2*1*80000=160000
        self.assertEqual(results[0]["cost"], 2 * 23000)
        self.assertEqual(results[0]["revenue"], 2 * 1 * 80000)
        self.assertEqual(results[0]["profit"], 160000 - 46000)

    def test_pair_2_uses_tier_2_points(self):
        """pair_2 should use tier 2 points (XSMN: 2đ, XSMB: 1đ)."""
        pairs = [10, 20, 30]
        tail_rows = [{"tail_2d": 20}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        # pair_2 (idx=1): 2pts * 14000 = 28000 cost, 2pts * 1 * 70000 = 140000 rev
        self.assertEqual(results[1]["cost"], 2 * 14000)
        self.assertEqual(results[1]["revenue"], 2 * 1 * 70000)

    def test_none_pair_skipped(self):
        """Pairs with None value should be skipped."""
        pairs = [42, None, 88]
        tail_rows = [{"tail_2d": 42}]
        
        results = calculate_station_profit("XSMN", pairs, tail_rows)
        self.assertEqual(len(results), 2)  # None skipped

    def test_unknown_region_returns_empty(self):
        """Unknown region should return empty list."""
        results = calculate_station_profit("XSMT", [42, 17, 88], [])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
