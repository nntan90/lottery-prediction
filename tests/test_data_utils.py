"""
Regression tests for shared XSMN/XSMB ensemble data utilities.
"""

import os
import sys
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.xsmn_ensemble.data_utils import _load_tails_by_draws as load_xsmn_tails_by_draws
from src.xsmb_ensemble.data_utils import _load_tails_by_draws as load_xsmb_tails_by_draws


class MockResult:
    def __init__(self, data):
        self.data = data


class MockQuery:
    def __init__(self, rows):
        self.rows = rows
        self.start = None
        self.end = None

    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def is_(self, *args, **kwargs): return self
    def lt(self, *args, **kwargs): return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def range(self, start, end):
        self.start = start
        self.end = end
        return self

    def execute(self):
        if self.start is None or self.end is None:
            return MockResult(self.rows)
        return MockResult(self.rows[self.start:self.end + 1])


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
        history = load_xsmn_tails_by_draws(
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

    def test_xsmb_paginates_past_postgrest_default_cap(self):
        rows = []
        # 70 XSMB draw dates, 27 tails/date. A single 1000-row page only covers
        # about 38 dates, which is not enough for ChiInd/LSTM.
        base_date = date(2026, 3, 1)
        for idx in range(69, -1, -1):
            draw_date = (base_date + timedelta(days=idx)).isoformat()
            for tail in range(27):
                rows.append({"draw_date": draw_date, "tail_2d": tail})

        history = load_xsmb_tails_by_draws(
            MockDB(rows),
            region="XSMB",
            province=None,
            n_draws=65,
            before_date=date(2026, 6, 24),
        )

        self.assertEqual(len(history), 65)
        self.assertEqual(len(history.iloc[-1]["tail_set"]), 27)

    def test_xsmn_same_weekday_filter_separates_twice_weekly_station(self):
        rows = [
            {"draw_date": "2026-07-18", "tail_2d": 18},  # Saturday
            {"draw_date": "2026-07-13", "tail_2d": 13},  # Monday
            {"draw_date": "2026-07-11", "tail_2d": 11},  # Saturday
            {"draw_date": "2026-07-06", "tail_2d": 6},   # Monday
        ]

        history = load_xsmn_tails_by_draws(
            MockDB(rows),
            region="XSMN",
            province="tp-hcm",
            n_draws=2,
            before_date=date(2026, 7, 20),
            target_weekday=0,
        )

        self.assertEqual(
            [draw.strftime("%Y-%m-%d") for draw in history["draw_date"]],
            ["2026-07-06", "2026-07-13"],
        )


if __name__ == "__main__":
    unittest.main()
