from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from src.xsmn_coupled.domain import CMRConfig, EXPECTED_PRIZE_COUNTS, normalize_tail_rows
from src.xsmn_coupled.fingerprint import build_fingerprint, coupled_similarity
from src.xsmn_coupled.predictor import build_historical_cases, predict_coupled, select_top_three
from src.xsmn_coupled.repository import load_tail_history
from src.scripts.predict_xsmn_coupled import _resolve_provinces


def _draw_rows(province: str, draw_date: date, base: int) -> list[dict]:
    rows = []
    offset = 0
    for prize_code, count in EXPECTED_PRIZE_COUNTS.items():
        for _ in range(count):
            rows.append(
                {
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                    "prize_code": prize_code,
                    "tail_2d": (base + offset) % 100,
                }
            )
            offset += 1
    return rows


def _weekly_history(provinces: tuple[str, str], count: int = 12) -> tuple[list[dict], date]:
    first = date(2026, 4, 27)
    rows = []
    for index in range(count):
        draw_date = first + timedelta(days=7 * index)
        rows.extend(_draw_rows(provinces[0], draw_date, (index * 3) % 70))
        rows.extend(_draw_rows(provinces[1], draw_date, (index * 3 + 5) % 70))
    return rows, first + timedelta(days=7 * count)


def test_normalize_rejects_incomplete_draws() -> None:
    provinces = ("a", "b")
    complete_date = date(2026, 7, 6)
    rows = _draw_rows("a", complete_date, 10)
    rows.extend(_draw_rows("a", complete_date + timedelta(days=7), 20)[:-1])

    draws = normalize_tail_rows(rows, provinces)

    assert [draw.draw_date for draw in draws["a"]] == [complete_date]
    assert draws["b"] == ()


def test_latest_same_province_anchor_allows_tphcm_saturday() -> None:
    provinces = ("tp-hcm", "dong-thap")
    target = date(2026, 7, 20)  # Monday
    rows = []
    for province, draw_date, base in (
        ("tp-hcm", date(2026, 7, 11), 10),
        ("tp-hcm", date(2026, 7, 13), 20),
        ("tp-hcm", date(2026, 7, 18), 30),
        ("dong-thap", date(2026, 7, 6), 40),
        ("dong-thap", date(2026, 7, 13), 50),
    ):
        rows.extend(_draw_rows(province, draw_date, base))
    draws = normalize_tail_rows(rows, provinces, before_date=target)

    _, anchor_a, anchor_b, _ = build_historical_cases(draws, provinces, target)

    assert anchor_a is not None and anchor_a.draw_date == date(2026, 7, 18)
    assert anchor_b is not None and anchor_b.draw_date == date(2026, 7, 13)


def test_each_prize_block_has_equal_total_similarity_effect() -> None:
    provinces = ("a", "b")
    target = date(2026, 7, 20)
    rows = _draw_rows("a", date(2026, 7, 13), 10) + _draw_rows("b", date(2026, 7, 13), 30)
    for row in rows:
        row["tail_2d"] = 10 if row["province"] == "a" else 30
    draws = normalize_tail_rows(rows, provinces)
    reference = build_fingerprint(draws["a"][0], draws["b"][0], target)

    db_changed = [dict(row) for row in rows]
    g4_changed = [dict(row) for row in rows]
    for row in db_changed:
        if row["province"] == "b" and row["prize_code"] == "DB":
            row["tail_2d"] = (int(row["tail_2d"]) + 1) % 100
    for row in g4_changed:
        if row["province"] == "b" and row["prize_code"] == "4":
            row["tail_2d"] = (int(row["tail_2d"]) + 1) % 100

    db_draws = normalize_tail_rows(db_changed, provinces)
    g4_draws = normalize_tail_rows(g4_changed, provinces)
    db_similarity = coupled_similarity(
        reference, build_fingerprint(db_draws["a"][0], db_draws["b"][0], target)
    )
    g4_similarity = coupled_similarity(
        reference, build_fingerprint(g4_draws["a"][0], g4_draws["b"][0], target)
    )

    assert db_similarity.prize_scores["DB"] == pytest.approx(g4_similarity.prize_scores["4"])
    assert db_similarity.score == pytest.approx(g4_similarity.score)


def test_selector_admits_at_most_one_direct_overlap() -> None:
    scores = {1: 0.99, 2: 0.98, 3: 0.80, 4: 0.70}

    selected = select_top_three(scores, frozenset({1, 2}))

    assert selected == (1, 3, 4)
    assert len(set(selected) & {1, 2}) == 1


def test_predictor_is_deterministic_and_ignores_at_or_after_target_rows() -> None:
    provinces = ("dong-nai", "can-tho")
    rows, target = _weekly_history(provinces)
    config = CMRConfig(top_k=8, min_neighbors=5, evidence_cases=3)

    expected = predict_coupled(rows, provinces, target, config)
    leaked_rows = rows + _draw_rows(provinces[0], target, 90) + _draw_rows(
        provinces[1], target + timedelta(days=7), 95
    )
    actual = predict_coupled(reversed(leaked_rows), provinces, target, config)

    assert expected == actual
    assert actual["status"] == "success"
    assert len(actual["top_3"]) == 3
    assert len(set(actual["top_3"]) & set(actual["direct_overlap"])) <= 1
    assert all(case["target_date"] < target.isoformat() for item in actual["selected_evidence"] for case in item["nearest_cases"])


def test_predictor_returns_insufficient_evidence_without_padding() -> None:
    provinces = ("tay-ninh", "an-giang")
    rows, target = _weekly_history(provinces, count=3)

    result = predict_coupled(rows, provinces, target, CMRConfig(top_k=5, min_neighbors=3))

    assert result["status"] == "insufficient_evidence"
    assert result["reason"] == "not_enough_historical_neighbors"
    assert result["top_3"] == []


def test_shrinkage_score_matches_formula_and_moves_toward_prior() -> None:
    provinces = ("dong-nai", "can-tho")
    rows, target = _weekly_history(provinces)
    weak = predict_coupled(rows, provinces, target, CMRConfig(top_k=8, min_neighbors=5, shrinkage_alpha=1.0))
    strong = predict_coupled(rows, provinces, target, CMRConfig(top_k=8, min_neighbors=5, shrinkage_alpha=50.0))
    item = weak["selected_evidence"][0]
    expected = (
        weak["prior_merged_prevalence"] + item["weighted_support_merged"]
    ) / (1.0 + weak["neighbor_weight_sum"])

    assert item["estimated_hit_likelihood_uncalibrated"] == pytest.approx(expected)
    strong_by_number = {item["number"]: item for item in strong["selected_evidence"]}
    if item["number"] in strong_by_number:
        weak_distance = abs(item["estimated_hit_likelihood_uncalibrated"] - weak["prior_merged_prevalence"])
        strong_distance = abs(
            strong_by_number[item["number"]]["estimated_hit_likelihood_uncalibrated"]
            - strong["prior_merged_prevalence"]
        )
        assert strong_distance < weak_distance


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_config_rejects_non_finite_numeric_values(value: float) -> None:
    with pytest.raises(ValueError):
        CMRConfig(shrinkage_alpha=value)
    with pytest.raises(ValueError):
        CMRConfig(context_weight=value)


def test_cli_province_override_must_match_schedule() -> None:
    monday = date(2026, 7, 20)

    assert _resolve_provinces(monday, "tp-hcm,dong-thap") == ("tp-hcm", "dong-thap")
    with pytest.raises(SystemExit, match="must match the schedule"):
        _resolve_provinces(monday, "vung-tau,ben-tre")


class _FakeResponse:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, rows: list[dict], calls: list[tuple[str, object]]) -> None:
        self.rows = rows
        self.calls = calls
        self.last_id = 0
        self.page_size = 1000

    def select(self, columns: str) -> "_FakeQuery":
        self.calls.append(("select", columns))
        return self

    def eq(self, column: str, value: object) -> "_FakeQuery":
        self.calls.append((f"eq:{column}", value))
        return self

    def in_(self, column: str, value: object) -> "_FakeQuery":
        self.calls.append((f"in:{column}", value))
        return self

    def lt(self, column: str, value: object) -> "_FakeQuery":
        self.calls.append((f"lt:{column}", value))
        return self

    def gt(self, column: str, value: int) -> "_FakeQuery":
        self.calls.append((f"gt:{column}", value))
        self.last_id = value
        return self

    def order(self, column: str, desc: bool = False) -> "_FakeQuery":
        self.calls.append((f"order:{column}", desc))
        return self

    def limit(self, value: int) -> "_FakeQuery":
        self.page_size = value
        return self

    def execute(self) -> _FakeResponse:
        page = [row for row in self.rows if row["id"] > self.last_id][: self.page_size]
        return _FakeResponse(page)


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object]] = []

    def table(self, name: str) -> _FakeQuery:
        self.calls.append(("table", name))
        return _FakeQuery(self.rows, self.calls)


class _FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.supabase = _FakeSupabase(rows)


def test_repository_uses_read_only_keyset_pagination_and_strict_cutoff() -> None:
    rows = [
        {"id": index, "draw_date": "2026-01-01", "province": "a", "prize_code": "DB", "tail_2d": index}
        for index in range(1, 6)
    ]
    db = _FakeDB(rows)

    loaded = load_tail_history(db, ("a", "b"), date(2026, 7, 20), page_size=2)

    assert [row["id"] for row in loaded] == [1, 2, 3, 4, 5]
    assert ("select", "id,draw_date,province,prize_code,tail_2d") in db.supabase.calls
    assert ("lt:draw_date", "2026-07-20") in db.supabase.calls
    assert all(not operation.startswith(("insert", "update", "upsert", "delete")) for operation, _ in db.supabase.calls)


def test_ensemble_engines_and_workflows_do_not_embed_cmr_logic() -> None:
    root = Path(__file__).resolve().parents[1]
    production_files = list((root / "src/xsmn_ensemble").glob("*.py"))
    production_files.extend((root / ".github/workflows").glob("*.yml"))

    assert all("xsmn_coupled" not in path.read_text(encoding="utf-8") for path in production_files)
