"""
Regression tests for shared XSMN/XSMB ensemble data utilities.
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.xsmn_ensemble.data_utils import _load_tails_by_draws


class MockResult:
    def __init__(self, data):
        self.data = data


class MockQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def is_(self, *args, **kwargs): return self
    def lt(self, *args, **kwargs): return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self

    def execute(self):
        return MockResult(self.rows)


class MockSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        self.table_name = name
        return MockQuery(self.rows)


class MockDB:
    def __init__(self, rows):
        self.supabase = MockSupabase(rows)


class TestLoadTailsByDraws(unittest.TestCase):
    def test_returns_chronological_history_with_newest_at_tail(self):
        rows = [
            {"draw_date": "2026-05-03", "tail_2d": 3},
            {"draw_date": "2026-05-02", "tail_2d": 2},
            {"draw_date": "2026-05-01", "tail_2d": 1},
            {"draw_date": "2026-05-03", "tail_2d": 13},
        ]
        history = _load_tails_by_draws(
            MockDB(rows),
            region="XSMN",
            province="tp-hcm",
            n_draws=3,
            before_date=date(2026, 5, 4),
        )

        self.assertEqual(
            [d.strftime("%Y-%m-%d") for d in history["draw_date"]],
            ["2026-05-01", "2026-05-02", "2026-05-03"],
        )
        self.assertEqual(history.iloc[-1]["tail_set"], frozenset({3, 13}))


if __name__ == "__main__":
    unittest.main()
