from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from src.xsmn_relationship.backtest import walk_forward_backtest
from src.xsmn_relationship.domain import (
    EXPECTED_PRIZE_COUNTS,
    MatchedOccasion,
    RelationshipConfig,
    build_matched_occasions,
)
from src.xsmn_relationship.predictor import (
    build_consensus_evidence,
    build_edge_evidence,
    predict_relationship,
    select_anchor,
)
from src.xsmn_relationship.repository import (
    load_archived_model_predictions,
    load_matched_history,
    load_tail_rows,
)
from src.xsmn_relationship.service import generate_relationship_shadow


PROVINCES = ("province-a", "province-b")
TARGET = date(2026, 8, 2)


def _config(**overrides: object) -> RelationshipConfig:
    values = {
        "min_active_model_families": 4,
        "history_lookback_occurrences": 12,
        "min_history_occurrences": 6,
        "prior_strength": 4.0,
        "min_pair_support_count": 1,
    }
    values.update(overrides)
    return RelationshipConfig(**values)


def _models(*, same_unit_only: bool = False) -> list[dict]:
    primary = [11, 21, 31, 41, 51, 61] if same_unit_only else [11, 25, 3, 44, 56, 77]
    rows: list[dict] = []
    for family_index in range(4):
        family = f"family-{family_index + 1}"
        for province in PROVINCES:
            pairs = (
                primary
                if province == PROVINCES[0] or same_unit_only
                else [*primary[:3], 68, 79, 90]
            )
            rows.append(
                {
                    "model_name": family,
                    "province": province,
                    "status": "success",
                    "top_pairs": [
                        (pair, 1.0 - rank / 10.0)
                        for rank, pair in enumerate(pairs)
                    ],
                }
            )
    return rows


def _history(count: int = 8, *, anchor_recent_hits: int = 0) -> list[MatchedOccasion]:
    start = date(2025, 1, 1)
    occasions: list[MatchedOccasion] = []
    for index in range(count):
        tails_a = {25, 44}
        tails_b = {3, 56}
        if index % 2 == 0:
            tails_a.add(11)
        if index >= count - anchor_recent_hits:
            tails_b.add(11)
        occasions.append(
            MatchedOccasion(
                draw_date=start + timedelta(days=index * 7),
                tails_by_province={PROVINCES[0]: tails_a, PROVINCES[1]: tails_b},
            )
        )
    return occasions


def _draw_rows(
    province: str,
    draw_date: date,
    *,
    first_id: int,
    base_tail: int = 0,
) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    for prize_code, count in EXPECTED_PRIZE_COUNTS.items():
        for _ in range(count):
            rows.append(
                {
                    "id": first_id + offset,
                    "region": "XSMN",
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                    "prize_code": prize_code,
                    "tail_2d": (base_tail + offset) % 100,
                }
            )
            offset += 1
    return rows


def test_family_vote_is_deduped_across_provinces_and_top_five_is_non_mutating() -> None:
    models = _models()
    original = deepcopy(models)

    evidence = build_consensus_evidence(models, PROVINCES, _config())

    assert evidence["nodes"][11]["family_vote_count"] == 4
    assert evidence["nodes"][11]["province_coverage"] == 1.0
    assert 77 not in evidence["nodes"]
    assert models == original


def test_top_five_boundary_does_not_backfill_from_rank_six() -> None:
    models = [
        {
            "model_name": "family-1",
            "model_version": "family-1-v2",
            "created_at": "2026-08-01T00:00:00+00:00",
            "province": PROVINCES[0],
            "status": "success",
            "top_pairs": [
                (11, 1.0),
                (11, 0.9),
                (101, 0.8),
                (25, 0.7),
                (3, 0.6),
                (44, 0.5),
            ],
        }
    ]

    evidence = build_consensus_evidence(models, PROVINCES, _config())

    source = evidence["source_top_5"][0]
    assert [item["pair"] for item in source["top_5"]] == [11, 25, 3]
    assert [item["rank"] for item in source["top_5"]] == [1, 4, 5]
    assert 44 not in evidence["nodes"]
    assert source["model_version"] == "family-1-v2"
    assert source["created_at"] == "2026-08-01T00:00:00+00:00"


def test_dynamic_denominator_excludes_failed_family_and_audits_it() -> None:
    models = _models()
    models.append(
        {
            "model_name": "failed-family",
            "province": PROVINCES[0],
            "status": "error",
            "top_pairs": [],
        }
    )

    evidence = build_consensus_evidence(models, PROVINCES, _config())

    assert evidence["nodes"][11]["active_family_count"] == 4
    assert evidence["nodes"][11]["family_vote_ratio"] == 1.0
    assert evidence["skipped_model_families"] == ["failed-family"]


def test_anchor_rejects_only_two_of_two_recent_hits() -> None:
    nodes = build_consensus_evidence(_models(), PROVINCES, _config())["nodes"]
    history = _history()
    history[-2:] = [
        MatchedOccasion(
            history[-2].draw_date,
            {PROVINCES[0]: {11, 25}, PROVINCES[1]: {3}},
        ),
        MatchedOccasion(
            history[-1].draw_date,
            {PROVINCES[0]: {11}, PROVINCES[1]: {3}},
        ),
    ]

    anchor, audit = select_anchor(nodes, history[-2:], _config())

    assert audit[0]["pair"] == 11
    assert audit[0]["reason"] == "consecutive_merged_hit_2of2"
    assert anchor == 25

    one_hit_history = _history()
    one_hit_history[-2:] = [
        MatchedOccasion(
            one_hit_history[-2].draw_date,
            {PROVINCES[0]: {11}, PROVINCES[1]: {3}},
        ),
        MatchedOccasion(
            one_hit_history[-1].draw_date,
            {PROVINCES[0]: {25}, PROVINCES[1]: {3}},
        ),
    ]
    anchor, audit = select_anchor(nodes, one_hit_history[-2:], _config())
    assert anchor == 11
    assert audit[0]["recent_hits"] == 1


def test_success_audits_triangle_direct_combo_and_distinct_units() -> None:
    result = predict_relationship(
        _models(),
        _history(),
        PROVINCES,
        TARGET,
        _config(),
    )

    assert result["status"] == "success"
    assert result["top_3"][0] == "11"
    assert len({int(pair) % 10 for pair in result["top_3"]}) == 3
    selected = result["run_metadata"]["selected_combo"]
    assert len(selected["edges"]) == 3
    assert selected["direct_evidence"]["history_count"] == 8
    assert selected["direct_evidence"]["hit_count_2of3"] >= 1
    assert result["score_semantics"] == "ranking_score_uncalibrated"


def test_edge_evidence_records_raw_denominators_cross_scope_and_shrinkage() -> None:
    edge = build_edge_evidence(25, 3, _history(), PROVINCES, _config())

    assert edge["history_count"] == 8
    assert edge["merged_joint_count"] == 8
    assert edge["cross_province_joint_count"] == 8
    assert edge["merged_support"] == 1.0
    assert edge["support_eligible"] is True
    assert 0.0 <= edge["merged_joint_shrunk"] <= 1.0


def test_edge_score_is_zero_when_joint_rate_has_no_excess_over_prior() -> None:
    history = [
        MatchedOccasion(
            date(2026, 1, 1),
            {PROVINCES[0]: {11, 25}, PROVINCES[1]: set()},
        ),
        MatchedOccasion(
            date(2026, 1, 8),
            {PROVINCES[0]: {11}, PROVINCES[1]: set()},
        ),
        MatchedOccasion(
            date(2026, 1, 15),
            {PROVINCES[0]: {25}, PROVINCES[1]: set()},
        ),
        MatchedOccasion(
            date(2026, 1, 22),
            {PROVINCES[0]: set(), PROVINCES[1]: set()},
        ),
    ]

    edge = build_edge_evidence(11, 25, history, PROVINCES, _config())

    assert edge["merged_joint_shrunk"] == edge["independent_joint_prior"]
    assert edge["merged_excess_over_prior"] == 0.0
    assert edge["cross_excess_over_prior"] == 0.0
    assert edge["association_strength"] == 0.0


@pytest.mark.parametrize(
    ("history", "expected_status"),
    [
        (_history(1), "insufficient_recent_history"),
        (_history(4), "insufficient_matched_draws"),
    ],
)
def test_history_gates_fail_closed(
    history: list[MatchedOccasion], expected_status: str
) -> None:
    result = predict_relationship(
        _models(), history, PROVINCES, TARGET, _config()
    )

    assert result["status"] == expected_status
    assert result["top_3"] == []


def test_distinct_unit_guard_abstains_instead_of_relaxing() -> None:
    result = predict_relationship(
        _models(same_unit_only=True),
        _history(),
        PROVINCES,
        TARGET,
        _config(),
    )

    assert result["status"] == "insufficient_candidate_diversity"
    assert result["top_3"] == []


def test_future_history_is_excluded_and_output_is_byte_deterministic() -> None:
    history = _history()
    history.extend(
        [
            MatchedOccasion(TARGET, {PROVINCES[0]: {99}, PROVINCES[1]: {98}}),
            MatchedOccasion(
                TARGET + timedelta(days=7),
                {PROVINCES[0]: {97}, PROVINCES[1]: {96}},
            ),
        ]
    )
    first = predict_relationship(_models(), history, PROVINCES, TARGET, _config())
    second = predict_relationship(_models(), reversed(history), PROVINCES, TARGET, _config())

    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    assert canonical(first) == canonical(second)
    assert first["run_metadata"]["matched_history_count"] == 8
    assert first["run_metadata"]["matched_history_end"] < TARGET.isoformat()


def test_duplicate_history_is_deduped_and_conflicts_fail_closed() -> None:
    history = _history()
    duplicate = MatchedOccasion(
        history[-1].draw_date,
        dict(history[-1].tails_by_province),
    )

    result = predict_relationship(
        _models(), [*history, duplicate], PROVINCES, TARGET, _config()
    )

    assert result["status"] == "success"
    assert result["run_metadata"]["deduplicated_history_dates"] == [
        history[-1].draw_date.isoformat()
    ]

    conflict = MatchedOccasion(
        history[-1].draw_date,
        {PROVINCES[0]: {98}, PROVINCES[1]: {99}},
    )
    failed = predict_relationship(
        _models(), [*history, conflict], PROVINCES, TARGET, _config()
    )

    assert failed["status"] == "error"
    assert failed["reason"] == "conflicting_duplicate_history_date"


def test_r_a_selection_and_pair_scores_do_not_use_history_components() -> None:
    model_rows: list[dict] = []
    rotations = (
        [11, 25, 36, 47],
        [11, 36, 47, 25],
        [11, 47, 25, 36],
    )
    for family_index, pairs in enumerate(rotations, start=1):
        for province in PROVINCES:
            model_rows.append(
                {
                    "model_name": f"family-{family_index}",
                    "province": province,
                    "status": "success",
                    "top_pairs": [(pair, 1.0) for pair in pairs],
                }
            )
    history_favoring_larger = [
        MatchedOccasion(
            date(2026, 1, 1) + timedelta(days=index * 7),
            {PROVINCES[0]: {11, 36}, PROVINCES[1]: {47}},
        )
        for index in range(6)
    ]
    neutral_history = [
        MatchedOccasion(
            date(2026, 1, 1) + timedelta(days=index * 7),
            {PROVINCES[0]: {11}, PROVINCES[1]: set()},
        )
        for index in range(6)
    ]
    config = _config(
        min_active_model_families=3,
        min_history_occurrences=6,
    )

    favored = predict_relationship(
        model_rows,
        history_favoring_larger,
        PROVINCES,
        TARGET,
        config,
        variant="R-A",
        apply_anchor_guard=False,
    )
    neutral = predict_relationship(
        model_rows,
        neutral_history,
        PROVINCES,
        TARGET,
        config,
        variant="R-A",
        apply_anchor_guard=False,
    )

    assert favored["top_3"] == neutral["top_3"] == ["11", "25", "36"]
    assert favored["relationship_score"] == neutral["relationship_score"]
    assert [
        item["ranking_score_uncalibrated"]
        for item in favored["selected_evidence"]
    ] == [
        item["ranking_score_uncalibrated"]
        for item in neutral["selected_evidence"]
    ]


def test_daily_service_refuses_disabled_unit_digit_guard() -> None:
    with pytest.raises(ValueError, match="requires distinct unit digits"):
        generate_relationship_shadow(
            object(),
            _models(),
            PROVINCES,
            TARGET,
            _config(require_distinct_unit_digits=False),
        )


def test_build_matched_occasions_requires_both_complete_draws_and_strict_cutoff() -> None:
    prior = date(2026, 7, 26)
    rows = _draw_rows(PROVINCES[0], prior, first_id=1)
    rows.extend(_draw_rows(PROVINCES[1], prior, first_id=20, base_tail=20))
    rows.extend(_draw_rows(PROVINCES[0], TARGET, first_id=40))
    rows.extend(_draw_rows(PROVINCES[1], TARGET, first_id=60))
    rows.extend(_draw_rows(PROVINCES[0], date(2026, 7, 19), first_id=80))

    occasions = build_matched_occasions(rows, PROVINCES, TARGET, limit=10)

    assert [occasion.draw_date for occasion in occasions] == [prior]
    assert set(occasions[0].tails_by_province) == set(PROVINCES)


class _Response:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _Query:
    def __init__(
        self,
        rows: list[dict],
        calls: list[tuple[str, object]],
        response_cap: int | None = None,
    ) -> None:
        self.rows = rows
        self.calls = calls
        self.filters: list[tuple[str, str, object]] = []
        self.page_size = 1000
        self.response_cap = response_cap
        self.order_key = "id"
        self.order_desc = False

    def select(self, value: str) -> "_Query":
        self.calls.append(("select", value)); return self

    def eq(self, key: str, value: object) -> "_Query":
        self.filters.append(("eq", key, value)); return self

    def in_(self, key: str, value: object) -> "_Query":
        self.filters.append(("in", key, value))
        self.calls.append((f"in:{key}", tuple(value)))
        return self

    def lt(self, key: str, value: object) -> "_Query":
        self.filters.append(("lt", key, value)); return self

    def gt(self, key: str, value: object) -> "_Query":
        self.filters.append(("gt", key, value)); return self

    def gte(self, key: str, value: object) -> "_Query":
        self.filters.append(("gte", key, value)); return self

    def lte(self, key: str, value: object) -> "_Query":
        self.filters.append(("lte", key, value)); return self

    def order(self, key: str, desc: bool = False) -> "_Query":
        self.order_key = key
        self.order_desc = desc
        self.calls.append((f"order:{key}", desc)); return self

    def limit(self, value: int) -> "_Query":
        self.page_size = value; return self

    def execute(self) -> _Response:
        rows = list(self.rows)
        for operation, key, value in self.filters:
            if operation == "eq":
                rows = [row for row in rows if row[key] == value]
            elif operation == "in":
                rows = [row for row in rows if row[key] in value]
            elif operation == "lt":
                rows = [row for row in rows if row[key] < value]
            elif operation == "gt":
                rows = [row for row in rows if row[key] > value]
            elif operation == "gte":
                rows = [row for row in rows if row[key] >= value]
            elif operation == "lte":
                rows = [row for row in rows if row[key] <= value]
        limit = min(self.page_size, self.response_cap or self.page_size)
        return _Response(
            sorted(
                rows,
                key=lambda row: row[self.order_key],
                reverse=self.order_desc,
            )[:limit]
        )


class _DB:
    def __init__(
        self,
        rows: list[dict],
        response_cap: int | None = None,
    ) -> None:
        self.rows = rows
        self.response_cap = response_cap
        self.calls: list[tuple[str, object]] = []
        self.supabase = self

    def table(self, name: str) -> _Query:
        self.calls.append(("table", name))
        return _Query(self.rows, self.calls, self.response_cap)


class _TablesDB:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self.tables = tables
        self.calls: list[tuple[str, object]] = []
        self.supabase = self

    def table(self, name: str) -> _Query:
        self.calls.append(("table", name))
        return _Query(self.tables.get(name, []), self.calls)


def test_repository_keyset_paginates_and_never_reads_target_date() -> None:
    prior = date(2026, 7, 26)
    rows = _draw_rows(PROVINCES[0], prior, first_id=1)
    rows.extend(_draw_rows(PROVINCES[1], prior, first_id=20))
    rows.extend(_draw_rows(PROVINCES[0], TARGET, first_id=40))
    db = _DB(rows)

    loaded = load_tail_rows(db, PROVINCES, TARGET, page_size=7)

    assert len(loaded) == 36
    assert all(row["draw_date"] < TARGET.isoformat() for row in loaded)
    assert sum(call == ("table", "tails_2d") for call in db.calls) > 1


def test_repository_continues_after_short_nonempty_pages() -> None:
    prior = date(2026, 7, 26)
    rows = _draw_rows(PROVINCES[0], prior, first_id=1)
    rows.extend(_draw_rows(PROVINCES[1], prior, first_id=20))
    db = _DB(rows, response_cap=3)

    loaded = load_tail_rows(db, PROVINCES, TARGET, page_size=7)

    assert len(loaded) == 36
    assert sum(call == ("table", "tails_2d") for call in db.calls) == 13


def test_archived_model_reader_continues_after_short_nonempty_pages() -> None:
    rows = [
        {
            "id": index,
            "prediction_date": "2026-08-01",
            "region": "XSMN",
            "province": PROVINCES[index % 2],
            "model_name": f"family-{index}",
            "prediction_mode": "production",
        }
        for index in range(1, 7)
    ]
    db = _DB(rows, response_cap=2)

    loaded = load_archived_model_predictions(
        db,
        PROVINCES,
        date(2026, 8, 1),
        date(2026, 8, 1),
        page_size=5,
    )

    assert [row["id"] for row in loaded] == [1, 2, 3, 4, 5, 6]


def test_matched_history_bounds_tail_query_to_recent_candidate_dates() -> None:
    lottery_rows: list[dict] = []
    tail_rows: list[dict] = []
    for index in range(20):
        draw_date = TARGET - timedelta(days=7 * (20 - index))
        for province_index, province in enumerate(PROVINCES):
            lottery_rows.append(
                {
                    "id": len(lottery_rows) + 1,
                    "region": "XSMN",
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                }
            )
            tail_rows.extend(
                _draw_rows(
                    province,
                    draw_date,
                    first_id=index * 100 + province_index * 20 + 1,
                    base_tail=index * 3 + province_index,
                )
            )
    db = _TablesDB(
        {"lottery_draws": lottery_rows, "tails_2d": tail_rows}
    )

    history = load_matched_history(
        db,
        PROVINCES,
        TARGET,
        limit=4,
        page_size=17,
    )

    queried_date_sets = [
        value for key, value in db.calls if key == "in:draw_date"
    ]
    assert len(history) == 4
    assert queried_date_sets
    assert len(set(queried_date_sets)) == 1
    assert len(queried_date_sets[0]) == 8
    assert history[-1].draw_date == TARGET - timedelta(days=7)


def test_matched_history_backfills_older_dates_when_recent_tails_are_partial() -> None:
    lottery_rows: list[dict] = []
    tail_rows: list[dict] = []
    expected_dates: list[date] = []
    for index in range(10):
        draw_date = TARGET - timedelta(days=7 * (10 - index))
        for province_index, province in enumerate(PROVINCES):
            lottery_rows.append(
                {
                    "id": len(lottery_rows) + 1,
                    "region": "XSMN",
                    "draw_date": draw_date.isoformat(),
                    "province": province,
                }
            )
            if index < 4:
                tail_rows.extend(
                    _draw_rows(
                        province,
                        draw_date,
                        first_id=index * 100 + province_index * 20 + 1,
                    )
                )
        if index < 4:
            expected_dates.append(draw_date)
    db = _TablesDB(
        {"lottery_draws": lottery_rows, "tails_2d": tail_rows}
    )

    history = load_matched_history(db, PROVINCES, TARGET, limit=4)

    assert [occasion.draw_date for occasion in history] == expected_dates


def test_walk_forward_uses_archived_same_day_sources_and_reports_coverage() -> None:
    occasions = _history(9)
    fold_date = occasions[-1].draw_date
    model_rows = []
    for result in _models():
        model_rows.append(
            {
                "prediction_date": fold_date.isoformat(),
                "province": result["province"],
                "model_name": result["model_name"],
                "model_version": f"{result['model_name']}_v-test",
                "created_at": f"{fold_date.isoformat()}T00:00:00+00:00",
                "status": "success",
                **{
                    f"pair_{index}": pair
                    for index, (pair, _) in enumerate(result["top_pairs"][:5], start=1)
                },
                **{
                    f"score_{index}": score
                    for index, (_, score) in enumerate(result["top_pairs"][:5], start=1)
                },
            }
        )

    report = walk_forward_backtest(
        model_rows,
        occasions,
        PROVINCES,
        _config(min_history_occurrences=6),
        family_weight_snapshots={
            fold_date: {
                "family-1": 1.2,
                "family-2": 1.0,
                "family-3": 0.9,
                "family-4": 0.8,
            }
        },
        production_prediction_rows=[
            {
                "id": 99,
                "prediction_date": fold_date.isoformat(),
                "region": "XSMN",
                "province": "all",
                "model_version": "ensemble_v3.5",
                "pair_1": 11,
                "pair_2": 25,
                "pair_3": 90,
                "created_at": f"{fold_date.isoformat()}T00:00:00+00:00",
            }
        ],
    )

    assert report["eligible_dates"] == [fold_date.isoformat()]
    assert set(report["variants"]) == {
        "R-A", "R-B", "R-C", "R-C_guard_off", "R-C_guard_on"
    }
    assert report["variants"]["R-C"]["evaluated_days"] == 1
    assert report["variants"]["R-C"]["coverage"] == 1.0
    assert report["family_weight_provenance"] == "per_target_snapshot"
    assert report["production_baseline"]["evaluated_days"] == 1
    assert report["production_baseline"]["hit_days_at_least_2of3"] == 1
    paired = report["variants"]["R-C"]["paired_vs_production"]
    assert paired["paired_days"] == 1
    assert paired["hit_day_delta"] == 0
    assert report["variants"]["R-C_guard_on"]["alias_of"] == "R-C"
    assert report["variants"]["R-C"]["folds"][0]["source_top_5"][0][
        "model_version"
    ].endswith("_v-test")


def test_daily_shadow_row_labels_combo_score_as_uncalibrated() -> None:
    from src.bot.ensemble_messages import format_compact_ensemble_message
    from src.scripts.predict_ensemble import _relationship_shadow_row

    row = _relationship_shadow_row(
        {
            "status": "success",
            "top_3": ["11", "25", "03"],
            "relationship_score": 0.61234,
        }
    )
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=TARGET,
        dow_label="Chủ Nhật",
        top_pairs=((12, 1.0), (34, 0.9), (56, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        additional_shadows=(row,),
    )

    assert "Relationship shadow" in message
    assert "<code>11</code> • <code>25</code> • <code>03</code>" in message
    assert "điểm bộ chưa calibration: <b>0.6123</b>" in message
    assert "probability" not in message
