"""Focused verification and Telegram-report regression tests."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot.verification_messages import (
    format_verification_message,
    summarize_multi_results,
)
from src.scripts.verify_v3 import _evaluate_prediction_pairs, verify_date


@pytest.mark.parametrize(
    ("actual", "hit_count", "combo_hit"),
    [
        ({88}, 0, False),
        ({50}, 1, False),
        ({50, 83}, 2, True),
        ({50, 83, 89}, 3, True),
    ],
)
def test_multi_evaluation_uses_two_of_three(
    actual: set[int],
    hit_count: int,
    combo_hit: bool,
) -> None:
    result = _evaluate_prediction_pairs(
        [50, 83, 89],
        actual,
        model_scope="ensemble",
    )

    assert result["hit_count"] == hit_count
    assert result["hit"] is combo_hit
    assert result["combo_hit"] is combo_hit


def test_invalid_duplicate_combo_is_a_safe_miss() -> None:
    result = _evaluate_prediction_pairs(
        [10, 10, 20],
        {10, 20},
        model_scope="ensemble",
    )

    assert result["hit"] is False
    assert result["combo_hit"] is False
    assert result["matched"] == [10, 20]
    assert result["validation_error"]


def test_invalid_single_combo_is_also_a_safe_miss() -> None:
    result = _evaluate_prediction_pairs(
        [10, 10, 20],
        {10, 20},
        model_scope="single",
    )

    assert result["hit"] is False
    assert result["matched"] == [10, 20]
    assert result["validation_error"]


def test_headline_counts_only_ensemble_rows() -> None:
    results = [
        {
            "model_scope": "ensemble",
            "hit": False,
            "combo_hit": False,
        },
        {
            "model_scope": "single",
            "hit": True,
            "combo_hit": False,
        },
    ]

    assert summarize_multi_results(results) == (0, 1, 0.0)


def test_formatter_marks_one_of_three_as_miss_and_explains_diagnostics() -> None:
    message = format_verification_message(
        "25/07/2026",
        [
            {
                "label": "XSMB/all",
                "region": "XSMB",
                "province": None,
                "model_scope": "ensemble",
                "pairs": [50, 83, 89],
                "matched": [83],
                "hit_count": 1,
                "hit": False,
                "combo_hit": False,
            }
        ],
        {
            "XSMB/all": [
                {
                    "model_name": "frequency",
                    "pairs": [68, 50, 52],
                    "matched": [50],
                    "hit": True,
                },
                {
                    "model_name": "lstm",
                    "pairs": [],
                    "matched": [],
                    "hit": False,
                },
            ]
        },
    )

    assert "└ 🔴 Bộ 3: 50 | 83 | 89 → 83 (1/3 · chưa đạt)" in message
    assert "🟢 = có candidate khớp; không phải verdict Multi-Model." in message
    assert "└ 🟢 Freq: [68, 50, 52] → 50" in message
    assert "└ ⚪ LSTM: không có dữ liệu" in message
    assert "Multi-Model đạt ≥2/3: 0/1 (0%)" in message


def test_formatter_preserves_invalid_slots_and_reports_missing_multi() -> None:
    invalid_message = format_verification_message(
        "25/07/2026",
        [
            {
                "region": "XSMB",
                "model_scope": "ensemble",
                "pairs": [10, 10, 20],
                "matched": [10, 20],
                "hit_count": 2,
                "combo_hit": False,
                "validation_error": "duplicate pairs",
            }
        ],
        {},
    )
    no_multi_message = format_verification_message(
        "25/07/2026",
        [{"region": "XSMB", "model_scope": "single", "hit": True}],
        {},
    )

    assert "10 | 10 | 20" in invalid_message
    assert "2/3 · dữ liệu không hợp lệ" in invalid_message
    assert "Multi-Model: không có dữ liệu để đánh giá" in no_multi_message
    assert "0/0 (0%)" not in no_multi_message


def test_formatter_marks_single_as_diagnostic_and_escapes_labels() -> None:
    message = format_verification_message(
        "25/07/2026",
        [{"region": "XSMN", "model_scope": "single", "hit": False}],
        {
            "XSMN/a&b": [
                {
                    "model_name": "xgboost_single",
                    "pairs": [10, 20, 30],
                    "matched": [10],
                },
                {
                    "model_name": "<custom>",
                    "pairs": [10],
                    "matched": [],
                },
            ]
        },
        {"a&b": "A & B"},
        ["a&b"],
    )

    assert "└ 🔎 A &amp; B: 10 | 20 | 30 → 10 (1/3 khớp)" in message
    assert "&lt;custom&gt;" in message


def test_formatter_keeps_unverified_shadow_out_of_multi_denominator() -> None:
    message = format_verification_message(
        "26/07/2026",
        [
            {
                "region": "XSMN",
                "model_scope": "ensemble",
                "pairs": [10, 20, 30],
                "matched": [10, 20],
                "hit_count": 2,
                "combo_hit": True,
            }
        ],
        {},
        shadow_results=[
            {
                "model_name": "cmr_shadow",
                "status": "success",
                "verification_status": "pending_results",
            },
            {
                "model_name": "ddt_shadow",
                "status": "insufficient_evidence",
                "reason": "not enough folds",
                "verification_status": "no_prediction",
            },
        ],
    )

    assert "CMR: chờ kết quả xổ số" in message
    assert "DDT: chưa đủ dữ liệu · not enough folds" in message
    assert "Multi-Model đạt ≥2/3: 1/1 (100%)" in message


def test_formatter_separates_xsmb_combo_shadow_and_uses_two_of_three_verdict() -> None:
    message = format_verification_message(
        "28/07/2026",
        [],
        {},
        shadow_results=[
            {
                "model_name": "xsmb_combo_shadow",
                "status": "success",
                "pairs": [12, 34, 56],
                "matched": [12],
                "hit_count": 1,
                "combo_hit": False,
                "verification_status": "verified",
            }
        ],
    )

    assert "XSMB Combo v6: 12 | 34 | 56 → 12" in message
    assert "(1/3 · chưa đạt shadow)" in message
    assert "Multi-Model: không có dữ liệu để đánh giá" in message


def test_formatter_filters_matches_to_displayed_top_n() -> None:
    message = format_verification_message(
        "25/07/2026",
        [
            {
                "region": "XSMB",
                "model_scope": "ensemble",
                "pairs": [50, 83, 89],
                "matched": [],
                "hit_count": 0,
                "combo_hit": False,
            }
        ],
        {
            "XSMB/all": [
                {
                    "model_name": "frequency",
                    "pairs": [10, 20, 30, 40, 50],
                    "matched": [40],
                    "hit": True,
                }
            ]
        },
    )

    assert "└ 🔴 Freq: [10, 20, 30] → —" in message
    assert "→ 40" not in message


def test_formatter_hides_empty_single_section_and_orders_regions() -> None:
    results = [
        {
            "region": "XSMN",
            "province": "all",
            "model_scope": "ensemble",
            "pairs": [7, 60, 61],
            "matched": [7, 60],
            "hit_count": 2,
            "combo_hit": True,
        },
        {
            "region": "XSMB",
            "province": None,
            "model_scope": "ensemble",
            "pairs": [50, 83, 89],
            "matched": [],
            "hit_count": 0,
            "combo_hit": False,
        },
    ]

    message = format_verification_message(
        "25/07/2026",
        results,
        {
            "XSMN/tp-hcm": [
                {
                    "model_name": "xgboost_core",
                    "pairs": [4, 7, 71, 61, 62],
                    "matched": [7],
                    "hit": True,
                },
                {
                    "model_name": "lstm",
                    "pairs": [],
                    "matched": [],
                    "hit": False,
                }
            ],
            "XSMN/long-an": [
                {
                    "model_name": "frequency",
                    "pairs": [47, 30, 88, 65, 2],
                    "matched": [],
                    "hit": False,
                }
            ],
        },
        {"tp-hcm": "TP. HCM", "long-an": "Long An"},
        ["tp-hcm", "long-an"],
    )

    assert "Single Model" not in message
    assert "└ 🟢 XGB: [04, 07, 71, 61, 62] → 07" in message
    assert message.index("📍 <b>XSMB</b>") < message.index("📍 <b>XSMN</b>")
    assert message.index("📍 <b>Tp. Hcm</b>") < message.index("📍 <b>Long An</b>")
    assert "└ 🟢 Bộ 3: 07 | 60 | 61 → 07, 60 (2/3 · TRÚNG)" in message
    assert "📍 <b>Tp. Hcm</b>" in message
    assert "Multi-Model đạt ≥2/3: 1/2 (50%)" in message


class _FakeQuery:
    def __init__(self, store: dict[str, list[dict]], table: str) -> None:
        self.store = store
        self.table = table
        self.filters: list[tuple[str, str, object]] = []
        self.operation = "select"
        self.payload: dict | None = None

    def select(self, *_args, **_kwargs):
        return self

    def update(self, payload: dict):
        self.operation = "update"
        self.payload = payload
        return self

    def insert(self, payload: dict):
        self.operation = "insert"
        self.payload = payload
        return self

    def eq(self, field: str, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values):
        self.filters.append(("in", field, set(values)))
        return self

    def is_(self, field: str, value):
        self.filters.append(("is", field, value))
        return self

    def _matches(self, row: dict) -> bool:
        for operation, field, value in self.filters:
            if operation == "eq" and row.get(field) != value:
                return False
            if operation == "in" and row.get(field) not in value:
                return False
            if operation == "is" and value == "null" and row.get(field) is not None:
                return False
        return True

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.operation == "update":
            for row in rows:
                if self._matches(row):
                    row.update(self.payload or {})
            return SimpleNamespace(data=[])
        if self.operation == "insert":
            rows.append(deepcopy(self.payload or {}))
            return SimpleNamespace(data=[deepcopy(self.payload or {})])
        return SimpleNamespace(
            data=[deepcopy(row) for row in rows if self._matches(row)]
        )


class _FakeSupabase:
    def __init__(self, store: dict[str, list[dict]]) -> None:
        self.store = store

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self.store, name)


def test_verify_date_persists_one_of_three_as_multi_miss() -> None:
    store = {
        "prediction_results": [
            {
                "id": 1,
                "prediction_date": "2026-07-25",
                "region": "XSMB",
                "province": None,
                "pair_1": 50,
                "pair_2": 83,
                "pair_3": 89,
                "model_version": "ensemble_v5.1",
            }
        ],
        "tails_2d": [
            {
                "region": "XSMB",
                "draw_date": "2026-07-25",
                "province": None,
                "tail_2d": 83,
            }
        ],
        "model_predictions": [
            {
                "id": 2,
                "prediction_date": "2026-07-25",
                "region": "XSMB",
                "province": None,
                "model_name": "frequency",
                "pair_1": 83,
                "pair_2": 10,
                "pair_3": 20,
                "pair_4": None,
                "pair_5": None,
            }
        ],
        "profit_tracking": [],
    }
    db = SimpleNamespace(supabase=_FakeSupabase(store))
    notifier = SimpleNamespace(send_message=AsyncMock(return_value=True))

    asyncio.run(verify_date(db, notifier, date(2026, 7, 25)))

    saved = store["prediction_results"][0]
    assert saved["hit"] is False
    assert saved["matched_pairs"] == [83]
    assert store["model_predictions"][0]["hit"] is True
    assert store["model_predictions"][0]["matched_pairs"] == [83]
    assert store["profit_tracking"][0]["hit_count"] == 1
    message = notifier.send_message.await_args.args[0]
    assert "1/3 · chưa đạt" in message
    assert "không phải verdict Multi-Model" in message
    assert "Multi-Model đạt ≥2/3: 0/1 (0%)" in message


def test_verify_date_tracks_shadow_combo_without_changing_multi_headline() -> None:
    store = {
        "prediction_results": [
            {
                "id": 1,
                "prediction_date": "2026-07-25",
                "region": "XSMN",
                "province": "all",
                "pair_1": 50,
                "pair_2": 83,
                "pair_3": 89,
                "model_version": "ensemble_v3.5",
            }
        ],
        "tails_2d": [
            {
                "region": "XSMN",
                "draw_date": "2026-07-25",
                "province": province,
                "tail_2d": pair,
            }
            for province, pair in (
                ("tp-hcm", 7),
                ("tp-hcm", 60),
                ("long-an", 1),
            )
        ],
        "model_predictions": [
            {
                "id": 2,
                "prediction_date": "2026-07-25",
                "region": "XSMN",
                "province": "all",
                "model_name": "cmr_shadow",
                "prediction_mode": "shadow",
                "status": "success",
                "pair_1": 7,
                "pair_2": 60,
                "pair_3": 61,
                "pair_4": None,
                "pair_5": None,
                "run_metadata": {"provinces": ["tp-hcm", "long-an"]},
            },
            {
                "id": 3,
                "prediction_date": "2026-07-25",
                "region": "XSMN",
                "province": "all",
                "model_name": "ddt_shadow",
                "prediction_mode": "shadow",
                "status": "uncalibrated",
                "pair_1": 1,
                "pair_2": 2,
                "pair_3": 3,
                "pair_4": None,
                "pair_5": None,
                "run_metadata": {"provinces": ["tp-hcm", "long-an"]},
            },
        ],
        "profit_tracking": [],
    }
    db = SimpleNamespace(supabase=_FakeSupabase(store))
    notifier = SimpleNamespace(send_message=AsyncMock(return_value=True))

    asyncio.run(verify_date(db, notifier, date(2026, 7, 25)))

    cmr, ddt = store["model_predictions"]
    assert cmr["hit"] is True
    assert cmr["hit_count"] == 2
    assert cmr["combo_hit"] is True
    assert cmr["matched_pairs"] == [7, 60]
    assert cmr["verified_at"] != "now()"
    assert datetime.fromisoformat(cmr["verified_at"]).tzinfo is not None
    assert ddt["hit"] is True
    assert ddt["hit_count"] == 1
    assert ddt["combo_hit"] is False
    message = notifier.send_message.await_args.args[0]
    assert "SHADOW — ĐỐI CHIẾU" in message
    assert "CMR: 07 | 60 | 61 → 07, 60 (2/3 · đạt shadow)" in message
    assert "DDT: 01 | 02 | 03 → 01 (1/3 · chưa đạt shadow)" in message
    assert "Multi-Model đạt ≥2/3: 0/1 (0%)" in message


def test_shadow_waits_until_both_target_provinces_have_results() -> None:
    shadow = {
        "id": 3,
        "prediction_date": "2026-07-26",
        "region": "XSMN",
        "province": "all",
        "model_name": "ddt_shadow",
        "prediction_mode": "shadow",
        "status": "success",
        "pair_1": 7,
        "pair_2": 60,
        "pair_3": 61,
        "run_metadata": {"provinces": ["tien-giang", "kien-giang"]},
    }
    store = {
        "prediction_results": [],
        "tails_2d": [
            {
                "region": "XSMN",
                "draw_date": "2026-07-26",
                "province": "tien-giang",
                "tail_2d": 7,
            }
        ],
        "model_predictions": [shadow],
    }
    db = SimpleNamespace(supabase=_FakeSupabase(store))
    notifier = SimpleNamespace(send_message=AsyncMock(return_value=True))

    asyncio.run(verify_date(db, notifier, date(2026, 7, 26)))

    assert "verified_at" not in shadow
    assert "combo_hit" not in shadow
    message = notifier.send_message.await_args.args[0]
    assert "DDT: chờ kết quả xổ số" in message
    assert "CMR: không có Top 3 hợp lệ" in message


def test_insufficient_xsmb_shadow_is_not_mislabeled_pending_results() -> None:
    store = {
        "prediction_results": [],
        "tails_2d": [],
        "model_predictions": [
            {
                "id": 4,
                "prediction_date": "2026-07-28",
                "region": "XSMB",
                "province": None,
                "model_name": "xsmb_combo_shadow",
                "prediction_mode": "shadow",
                "status": "insufficient_history",
                "error_message": "need at least 30 draws",
            }
        ],
    }
    db = SimpleNamespace(supabase=_FakeSupabase(store))
    notifier = SimpleNamespace(send_message=AsyncMock(return_value=True))

    asyncio.run(verify_date(db, notifier, date(2026, 7, 28)))

    message = notifier.send_message.await_args.args[0]
    assert "XSMB Combo v6: chưa đủ dữ liệu · need at least 30 draws" in message
    assert "XSMB Combo v6: chờ kết quả xổ số" not in message


def test_partial_xsmb_draw_keeps_shadow_verification_pending() -> None:
    shadow = {
        "id": 5,
        "prediction_date": "2026-07-28",
        "region": "XSMB",
        "province": None,
        "model_name": "xsmb_combo_shadow",
        "prediction_mode": "shadow",
        "status": "success",
        "pair_1": 12,
        "pair_2": 34,
        "pair_3": 56,
    }
    store = {
        "prediction_results": [],
        "tails_2d": [
            {
                "region": "XSMB",
                "draw_date": "2026-07-28",
                "province": None,
                "tail_2d": 12,
            }
        ],
        "model_predictions": [shadow],
    }
    db = SimpleNamespace(supabase=_FakeSupabase(store))
    notifier = SimpleNamespace(send_message=AsyncMock(return_value=True))

    asyncio.run(verify_date(db, notifier, date(2026, 7, 28)))

    assert "verified_at" not in shadow
    assert "combo_hit" not in shadow
    message = notifier.send_message.await_args.args[0]
    assert "XSMB Combo v6: chờ kết quả xổ số" in message
