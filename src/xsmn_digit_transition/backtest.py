"""Walk-forward evidence and permutation controls for the PDA/DDT shadow model."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import random
from typing import Iterable, Mapping, Optional, Sequence

from .config import DigitTransitionConfig
from .domain import normalize_tail_rows, validate_provinces
from .service import predict_digit_transition


@dataclass(frozen=True)
class BacktestFold:
    target_date: date
    top_3: tuple[int, ...]
    hit_count: int
    province_hits: tuple[tuple[str, int], ...]
    score_semantics: str
    brier: float
    confidence: float
    route: str
    baseline_hits: tuple[tuple[str, int], ...]


def _row_date(row: Mapping[str, object]) -> date:
    value = row.get("draw_date")
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _baseline_top_three(
    training: tuple[Mapping[str, object], ...],
    provinces: tuple[str, ...],
    target: date,
) -> dict[str, tuple[int, ...]]:
    draws = normalize_tail_rows(training, list(provinces), before_date=target)
    frequency = [0] * 100
    head = [0] * 10
    unit = [0] * 10
    for province in provinces:
        for draw in draws[province]:
            for pair in draw.tails:
                frequency[pair] += 1
                head[pair // 10] += 1
                unit[pair % 10] += 1
    marginal = [head[pair // 10] * unit[pair % 10] for pair in range(100)]
    return {
        "frequency": tuple(sorted(range(100), key=lambda pair: (-frequency[pair], pair))[:3]),
        "marginal_only": tuple(sorted(range(100), key=lambda pair: (-marginal[pair], pair))[:3]),
    }


def walk_forward_backtest(
    rows: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    config: Optional[DigitTransitionConfig] = None,
    *,
    max_folds: Optional[int] = None,
) -> dict:
    """Evaluate merged Top 3 using only rows strictly before every fold date."""
    province_scope = validate_provinces(list(provinces))
    if len(province_scope) != 2:
        raise ValueError("DDT backtest requires exactly two provinces")
    config = config or DigitTransitionConfig()
    source = tuple(dict(row) for row in rows)
    far_future = date.max
    draws = normalize_tail_rows(source, list(province_scope), before_date=far_future)
    by_date = {
        province: {draw.draw_date: draw for draw in draws[province]}
        for province in province_scope
    }
    targets = sorted(set(by_date[province_scope[0]]) & set(by_date[province_scope[1]]))
    folds: list[BacktestFold] = []
    for target in targets:
        training = tuple(row for row in source if _row_date(row) < target)
        result = predict_digit_transition(
            training,
            province_scope,
            target,
            config,
            regional_rows=training,
        )
        if result.get("status") not in {"success", "uncalibrated"}:
            continue
        top_3 = tuple(int(pair) for pair in result["top_3"])
        actual_by_province = {
            province: frozenset(by_date[province][target].tails)
            for province in province_scope
        }
        actual_merged = actual_by_province[province_scope[0]] | actual_by_province[
            province_scope[1]
        ]
        audit = result["top_100_audit"]
        score_key = (
            "probability"
            if result["score_semantics"] == "merged_pair_hit_probability_calibrated"
            else "estimated_likelihood_uncalibrated"
        )
        brier = sum(
            (float(item[score_key]) - int(int(item["pair"]) in actual_merged)) ** 2
            for item in audit
        ) / 100.0
        baselines = _baseline_top_three(training, province_scope, target)
        folds.append(
            BacktestFold(
                target_date=target,
                top_3=top_3,
                hit_count=len(set(top_3) & actual_merged),
                province_hits=tuple(
                    (
                        province,
                        len(
                            {
                                int(item["pair"])
                                for item in result["per_province"][province]["top_pairs"][:3]
                            }
                            & actual_by_province[province]
                        ),
                    )
                    for province in province_scope
                ),
                score_semantics=result["score_semantics"],
                brier=brier,
                confidence=sum(
                    float(result["per_province"][province]["confidence"])
                    for province in province_scope
                )
                / len(province_scope),
                route=target.strftime("%A"),
                baseline_hits=tuple(
                    (name, len(set(pairs) & actual_merged))
                    for name, pairs in sorted(baselines.items())
                ),
            )
        )
        if max_folds is not None and len(folds) >= max_folds:
            break

    distribution = Counter(fold.hit_count for fold in folds)
    province_totals: dict[str, int] = defaultdict(int)
    for fold in folds:
        for province, hits in fold.province_hits:
            province_totals[province] += hits
    count = len(folds)
    baseline_names = sorted(
        {name for fold in folds for name, _ in fold.baseline_hits}
    )
    baseline_mean_hits = {
        name: (
            sum(dict(fold.baseline_hits)[name] for fold in folds) / count
            if count
            else 0.0
        )
        for name in baseline_names
    }
    route_groups: dict[str, list[BacktestFold]] = defaultdict(list)
    confidence_groups: dict[str, list[BacktestFold]] = defaultdict(list)
    for fold in folds:
        route_groups[fold.route].append(fold)
        bucket = "low" if fold.confidence < 1 / 3 else "medium" if fold.confidence < 2 / 3 else "high"
        confidence_groups[bucket].append(fold)
    model_mean_hits = sum(fold.hit_count for fold in folds) / count if count else 0.0
    return {
        "fold_count": count,
        "hit_count_distribution": {
            str(hit_count): distribution.get(hit_count, 0) for hit_count in range(4)
        },
        "mean_hits": model_mean_hits,
        "hit_at_least_2_rate": (
            sum(fold.hit_count >= 2 for fold in folds) / count if count else 0.0
        ),
        "mean_brier": sum(fold.brier for fold in folds) / count if count else None,
        "province_mean_hits": {
            province: province_totals[province] / count if count else 0.0
            for province in province_scope
        },
        "baseline_mean_hits": baseline_mean_hits,
        "lift_over_baseline": {
            name: model_mean_hits - value for name, value in baseline_mean_hits.items()
        },
        "route_metrics": {
            route: {
                "fold_count": len(group),
                "mean_hits": sum(fold.hit_count for fold in group) / len(group),
            }
            for route, group in sorted(route_groups.items())
        },
        "confidence_buckets": {
            bucket: {
                "fold_count": len(group),
                "mean_hits": sum(fold.hit_count for fold in group) / len(group),
                "mean_brier": sum(fold.brier for fold in group) / len(group),
            }
            for bucket, group in sorted(confidence_groups.items())
        },
        "folds": [
            {
                "target_date": fold.target_date.isoformat(),
                "top_3": list(fold.top_3),
                "hit_count": fold.hit_count,
                "province_hits": dict(fold.province_hits),
                "score_semantics": fold.score_semantics,
                "brier": fold.brier,
                "confidence": fold.confidence,
                "route": fold.route,
                "baseline_hits": dict(fold.baseline_hits),
            }
            for fold in folds
        ],
    }


def permute_rows(
    rows: Iterable[Mapping[str, object]],
    mode: str,
    *,
    seed: int = 0,
) -> list[dict]:
    """Create a deterministic negative control while preserving draw completeness."""
    source = [dict(row) for row in rows]
    if mode == "province_labels":
        provinces = sorted({str(row.get("province")) for row in source if row.get("province")})
        if len(provinces) != 2:
            raise ValueError("province_labels control requires exactly two provinces")
        dates = sorted({_row_date(row) for row in source})
        swap_dates = set(dates[::2])
        for row in source:
            if _row_date(row) in swap_dates:
                row["province"] = (
                    provinces[1]
                    if str(row.get("province")) == provinces[0]
                    else provinces[0]
                )
        return source

    if mode == "draw_order":
        dates_by_province: dict[str, list[date]] = defaultdict(list)
        for row in source:
            province = str(row.get("province"))
            draw_date = _row_date(row)
            if draw_date not in dates_by_province[province]:
                dates_by_province[province].append(draw_date)
        mappings: dict[str, dict[date, date]] = {}
        for offset, province in enumerate(sorted(dates_by_province)):
            original = sorted(dates_by_province[province])
            shuffled = list(original)
            random.Random(seed + offset).shuffle(shuffled)
            mappings[province] = dict(zip(original, shuffled))
        for row in source:
            province = str(row.get("province"))
            row["draw_date"] = mappings[province][_row_date(row)].isoformat()
        return source

    if mode == "head_unit_association":
        grouped: dict[tuple[str, date], list[dict]] = defaultdict(list)
        for row in source:
            grouped[(str(row.get("province")), _row_date(row))].append(row)
        for key in sorted(grouped):
            draw_rows = sorted(
                grouped[key],
                key=lambda row: (str(row.get("prize_code")), int(row.get("id", 0))),
            )
            units = [int(row["tail_2d"]) % 10 for row in draw_rows]
            rotated = units[1:] + units[:1]
            for row, unit in zip(draw_rows, rotated):
                row["tail_2d"] = (int(row["tail_2d"]) // 10) * 10 + unit
        return source

    raise ValueError("unknown permutation mode")


def evaluate_permutation_controls(
    rows: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    config: Optional[DigitTransitionConfig] = None,
    *,
    seed: int = 0,
    max_folds: Optional[int] = None,
) -> dict:
    """Run all negative controls and report signal lift over each permutation."""
    source = tuple(dict(row) for row in rows)
    observed = walk_forward_backtest(
        source, provinces, config, max_folds=max_folds
    )
    controls = {
        mode: walk_forward_backtest(
            permute_rows(source, mode, seed=seed),
            provinces,
            config,
            max_folds=max_folds,
        )
        for mode in ("province_labels", "draw_order", "head_unit_association")
    }
    return {
        "observed": observed,
        "controls": controls,
        "mean_hit_lift": {
            mode: observed["mean_hits"] - report["mean_hits"]
            for mode, report in controls.items()
        },
    }
