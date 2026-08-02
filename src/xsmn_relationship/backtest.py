"""Leakage-safe walk-forward evaluation for relationship ablations."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Sequence

from .domain import MatchedOccasion, RelationshipConfig, validate_provinces
from .predictor import predict_relationship


ABLATIONS = (
    ("R-A", "R-A", False, None),
    ("R-B", "R-B", False, None),
    ("R-C", "R-C", True, None),
    ("R-C_guard_off", "R-C", False, None),
    ("R-C_guard_on", "R-C", True, "R-C"),
)


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def archived_rows_to_model_results(
    rows: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date,
) -> list[dict]:
    """Reconstruct only the archived Top-5 available on one prediction date."""
    province_scope = validate_provinces(provinces)
    allowed = set(province_scope)
    results: list[dict] = []
    for row in rows:
        if _as_date(row.get("prediction_date")) != target_date:
            continue
        province = str(row.get("province") or "")
        if province not in allowed:
            continue
        top_pairs = [
            (row.get(f"pair_{index}"), row.get(f"score_{index}"))
            for index in range(1, 6)
            if row.get(f"pair_{index}") is not None
        ]
        results.append(
            {
                "model_name": row.get("model_name"),
                "model_version": row.get("model_version"),
                "created_at": row.get("created_at"),
                "province": province,
                "status": row.get("status"),
                "top_pairs": top_pairs,
            }
        )
    results.sort(key=lambda item: (str(item["model_name"]), str(item["province"])))
    return results


def _weight_snapshot(
    snapshots: Optional[Mapping[object, Mapping[str, float]]],
    target_date: date,
) -> Optional[Mapping[str, float]]:
    """Resolve only a weight map explicitly keyed to the current fold date."""
    if not snapshots:
        return None
    return snapshots.get(target_date) or snapshots.get(target_date.isoformat())


def _production_baseline_folds(
    rows: Iterable[Mapping[str, object]],
    targets: Sequence[date],
    actual_by_date: Mapping[date, MatchedOccasion],
) -> list[dict[str, object]]:
    """Evaluate canonical archived XSMN/all ensemble rows on eligible dates."""
    candidates: dict[date, list[Mapping[str, object]]] = defaultdict(list)
    target_set = set(targets)
    for row in rows:
        try:
            target = _as_date(row.get("prediction_date"))
        except (TypeError, ValueError):
            continue
        if (
            target not in target_set
            or str(row.get("region") or "").upper() != "XSMN"
            or str(row.get("province") or "").lower() != "all"
            or not str(row.get("model_version") or "").lower().startswith("ensemble")
        ):
            continue
        candidates[target].append(row)

    folds: list[dict[str, object]] = []
    for target in sorted(candidates):
        row = max(
            candidates[target],
            key=lambda item: (
                str(item.get("created_at") or ""),
                int(item.get("id") or -1),
            ),
        )
        pairs: list[int] = []
        for index in range(1, 4):
            try:
                pair = int(row.get(f"pair_{index}"))
            except (TypeError, ValueError):
                continue
            if 0 <= pair <= 99 and pair not in pairs:
                pairs.append(pair)
        if len(pairs) != 3:
            continue
        matched = [pair for pair in pairs if pair in actual_by_date[target].merged_tails]
        folds.append(
            {
                "target_date": target.isoformat(),
                "top_3": pairs,
                "matched": matched,
                "hit_count": len(matched),
                "combo_hit": len(matched) >= 2,
            }
        )
    return folds


def walk_forward_backtest(
    model_prediction_rows: Iterable[Mapping[str, object]],
    matched_occasions: Iterable[MatchedOccasion],
    provinces: Sequence[str],
    config: Optional[RelationshipConfig] = None,
    *,
    family_weight_snapshots: Optional[
        Mapping[object, Mapping[str, float]]
    ] = None,
    production_prediction_rows: Iterable[Mapping[str, object]] = (),
    max_folds: Optional[int] = None,
) -> dict:
    """Evaluate all variants with history strictly earlier than each fold."""
    province_scope = validate_provinces(provinces)
    config = config or RelationshipConfig()
    archived = tuple(dict(row) for row in model_prediction_rows)
    occasions = tuple(
        sorted(
            (
                occasion
                for occasion in matched_occasions
                if set(occasion.tails_by_province) == set(province_scope)
            ),
            key=lambda occasion: occasion.draw_date,
        )
    )
    actual_by_date = {occasion.draw_date: occasion for occasion in occasions}
    archived_dates = {
        _as_date(row.get("prediction_date"))
        for row in archived
        if str(row.get("province") or "") in set(province_scope)
    }
    targets = sorted(archived_dates & set(actual_by_date))
    if max_folds is not None:
        if isinstance(max_folds, bool) or max_folds < 1:
            raise ValueError("max_folds must be positive")
        targets = targets[:max_folds]

    folds: dict[str, list[dict[str, object]]] = defaultdict(list)
    abstentions: dict[str, Counter[str]] = defaultdict(Counter)
    for target in targets:
        runtime_results = archived_rows_to_model_results(
            archived,
            province_scope,
            target,
        )
        training = tuple(
            occasion for occasion in occasions if occasion.draw_date < target
        )
        actual = actual_by_date[target].merged_tails
        fold_weights = _weight_snapshot(family_weight_snapshots, target)
        for label, variant, guard, _alias_of in ABLATIONS:
            result = predict_relationship(
                runtime_results,
                training,
                province_scope,
                target,
                config,
                family_weights=fold_weights,
                variant=variant,
                apply_anchor_guard=guard,
            )
            if result.get("status") != "success":
                abstentions[label][str(result.get("status") or "error")] += 1
                continue
            top_3 = tuple(int(pair) for pair in result["top_3"])
            matched = tuple(pair for pair in top_3 if pair in actual)
            folds[label].append(
                {
                    "target_date": target.isoformat(),
                    "top_3": list(top_3),
                    "matched": list(matched),
                    "hit_count": len(matched),
                    "combo_hit": len(matched) >= 2,
                    "relationship_score": result.get("relationship_score"),
                    "source_top_5": result.get("run_metadata", {}).get(
                        "source_top_5", []
                    ),
                }
            )

    eligible_days = len(targets)
    reports: dict[str, dict[str, object]] = {}
    production_folds = _production_baseline_folds(
        production_prediction_rows,
        targets,
        actual_by_date,
    )
    production_by_date = {
        str(fold["target_date"]): fold for fold in production_folds
    }
    for label, _, _, alias_of in ABLATIONS:
        variant_folds = folds[label]
        evaluated = len(variant_folds)
        hit_days = sum(bool(fold["combo_hit"]) for fold in variant_folds)
        variant_by_date = {
            str(fold["target_date"]): fold for fold in variant_folds
        }
        paired_dates = sorted(set(variant_by_date) & set(production_by_date))
        relationship_wins = sum(
            bool(variant_by_date[day]["combo_hit"])
            and not bool(production_by_date[day]["combo_hit"])
            for day in paired_dates
        )
        production_wins = sum(
            bool(production_by_date[day]["combo_hit"])
            and not bool(variant_by_date[day]["combo_hit"])
            for day in paired_dates
        )
        reports[label] = {
            "alias_of": alias_of,
            "eligible_days": eligible_days,
            "evaluated_days": evaluated,
            "coverage": evaluated / eligible_days if eligible_days else 0.0,
            "abstention_days": eligible_days - evaluated,
            "abstention_statuses": dict(sorted(abstentions[label].items())),
            "hit_days_at_least_2of3": hit_days,
            "hit_rate_at_least_2of3": hit_days / evaluated if evaluated else 0.0,
            "mean_hit_count": (
                sum(int(fold["hit_count"]) for fold in variant_folds) / evaluated
                if evaluated
                else 0.0
            ),
            "paired_vs_production": {
                "paired_days": len(paired_dates),
                "dates": paired_dates,
                "relationship_hit_days": sum(
                    bool(variant_by_date[day]["combo_hit"])
                    for day in paired_dates
                ),
                "production_hit_days": sum(
                    bool(production_by_date[day]["combo_hit"])
                    for day in paired_dates
                ),
                "relationship_wins": relationship_wins,
                "production_wins": production_wins,
                "ties": len(paired_dates) - relationship_wins - production_wins,
                "hit_day_delta": relationship_wins - production_wins,
            },
            "folds": variant_folds,
        }

    comparable_labels = ("R-A", "R-B", "R-C_guard_off", "R-C_guard_on")
    paired_variant_dates = sorted(
        set.intersection(
            *(
                {str(fold["target_date"]) for fold in folds[label]}
                for label in comparable_labels
            )
        )
    ) if comparable_labels else []
    for label in comparable_labels:
        by_date = {str(fold["target_date"]): fold for fold in folds[label]}
        reports[label]["paired_variant_metrics"] = {
            "paired_days": len(paired_variant_dates),
            "hit_days_at_least_2of3": sum(
                bool(by_date[day]["combo_hit"]) for day in paired_variant_dates
            ),
            "mean_hit_count": (
                sum(int(by_date[day]["hit_count"]) for day in paired_variant_dates)
                / len(paired_variant_dates)
                if paired_variant_dates
                else 0.0
            ),
        }
    production_evaluated = len(production_folds)
    production_hits = sum(bool(fold["combo_hit"]) for fold in production_folds)
    return {
        "model_name": "relationship",
        "model_version": "relationship_v1",
        "score_semantics": "ranking_score_uncalibrated",
        "provinces": list(province_scope),
        "config": config.to_dict(),
        "family_weight_provenance": (
            "per_target_snapshot" if family_weight_snapshots else "uniform"
        ),
        "eligible_dates": [target.isoformat() for target in targets],
        "paired_variant_dates": paired_variant_dates,
        "production_baseline": {
            "eligible_days": eligible_days,
            "evaluated_days": production_evaluated,
            "coverage": (
                production_evaluated / eligible_days if eligible_days else 0.0
            ),
            "hit_days_at_least_2of3": production_hits,
            "hit_rate_at_least_2of3": (
                production_hits / production_evaluated
                if production_evaluated
                else 0.0
            ),
            "folds": production_folds,
        },
        "variants": reports,
    }
