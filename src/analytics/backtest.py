"""
Walk-forward backtest metrics for 2D tail predictions.

The report treats saved prediction_results as out-of-time predictions: each row
was generated before the draw and verified after tails_2d was available.
"""

from __future__ import annotations

import ast
import json
import math
from collections import defaultdict
from datetime import date
from typing import Any, Iterable


def _station_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("region") or "UNKNOWN"), str(row.get("province") or "all")


def _parse_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            try:
                value = ast.literal_eval(value)
            except Exception:
                value = [value]
    if isinstance(value, (set, tuple)):
        value = list(value)
    if not isinstance(value, list):
        value = [value]

    ints: list[int] = []
    for item in value:
        try:
            ints.append(int(item))
        except (TypeError, ValueError):
            continue
    return ints


def prediction_pairs(row: dict[str, Any], k: int = 3) -> list[int]:
    """Return valid predicted pairs in rank order."""
    pairs: list[int] = []
    for idx in range(1, k + 1):
        try:
            pair = int(row.get(f"pair_{idx}"))
        except (TypeError, ValueError):
            continue
        if 0 <= pair <= 99:
            pairs.append(pair)
    return pairs


def random_hit_probability(tail_count: int, picks: int) -> float:
    """Expected hit probability for random unique picks from 00-99."""
    tail_count = max(0, min(int(tail_count), 100))
    picks = max(0, min(int(picks), 100))
    if tail_count == 0 or picks == 0:
        return 0.0
    miss_count = 100 - tail_count
    if miss_count < picks:
        return 1.0
    return 1.0 - (math.comb(miss_count, picks) / math.comb(100, picks))


def random_at_least_hits_probability(tail_count: int, picks: int, min_hits: int) -> float:
    """Return the hypergeometric probability of at least ``min_hits`` hits."""
    tail_count = max(0, min(int(tail_count), 100))
    picks = max(0, min(int(picks), 100))
    min_hits = max(0, int(min_hits))
    if min_hits == 0:
        return 1.0
    if picks == 0 or tail_count == 0 or min_hits > min(picks, tail_count):
        return 0.0

    denominator = math.comb(100, picks)
    probability = 0.0
    for hits in range(min_hits, min(picks, tail_count) + 1):
        misses = picks - hits
        if misses <= 100 - tail_count:
            probability += (
                math.comb(tail_count, hits) * math.comb(100 - tail_count, misses)
            ) / denominator
    return probability


def _empty_prediction_bucket() -> dict[str, float]:
    return {
        "predictions": 0,
        "hit_1": 0,
        "hit_3": 0,
        "hit_at_least_2": 0,
        "hit_all_3": 0,
        "hit_count_0": 0,
        "hit_count_1": 0,
        "hit_count_2": 0,
        "hit_count_3": 0,
        "baseline_hit_1_sum": 0.0,
        "baseline_hit_3_sum": 0.0,
        "baseline_hit_at_least_2_sum": 0.0,
        "baseline_hit_all_3_sum": 0.0,
    }


def _finalize_prediction_bucket(bucket: dict[str, float]) -> dict[str, float]:
    total = int(bucket["predictions"])
    if total == 0:
        return {
            "predictions": 0,
            "hit_1_rate": 0.0,
            "hit_3_rate": 0.0,
            "hit_at_least_2_rate": 0.0,
            "hit_all_3_rate": 0.0,
            "baseline_hit_1_rate": 0.0,
            "baseline_hit_3_rate": 0.0,
            "baseline_hit_at_least_2_rate": 0.0,
            "baseline_hit_all_3_rate": 0.0,
            "lift_hit_1": 0.0,
            "lift_hit_3": 0.0,
            "lift_hit_at_least_2": 0.0,
            "lift_hit_all_3": 0.0,
            "hit_count_distribution": {"0": 0, "1": 0, "2": 0, "3": 0},
        }

    hit_1_rate = bucket["hit_1"] / total
    hit_3_rate = bucket["hit_3"] / total
    baseline_1 = bucket["baseline_hit_1_sum"] / total
    baseline_3 = bucket["baseline_hit_3_sum"] / total
    hit_at_least_2_rate = bucket["hit_at_least_2"] / total
    hit_all_3_rate = bucket["hit_all_3"] / total
    baseline_at_least_2 = bucket["baseline_hit_at_least_2_sum"] / total
    baseline_all_3 = bucket["baseline_hit_all_3_sum"] / total

    return {
        "predictions": total,
        "hit_1": int(bucket["hit_1"]),
        "hit_3": int(bucket["hit_3"]),
        "hit_at_least_2": int(bucket["hit_at_least_2"]),
        "hit_all_3": int(bucket["hit_all_3"]),
        "hit_1_rate": round(hit_1_rate, 4),
        "hit_3_rate": round(hit_3_rate, 4),
        "hit_at_least_2_rate": round(hit_at_least_2_rate, 4),
        "hit_all_3_rate": round(hit_all_3_rate, 4),
        "baseline_hit_1_rate": round(baseline_1, 4),
        "baseline_hit_3_rate": round(baseline_3, 4),
        "baseline_hit_at_least_2_rate": round(baseline_at_least_2, 4),
        "baseline_hit_all_3_rate": round(baseline_all_3, 4),
        "lift_hit_1": round(hit_1_rate / baseline_1, 4) if baseline_1 else 0.0,
        "lift_hit_3": round(hit_3_rate / baseline_3, 4) if baseline_3 else 0.0,
        "lift_hit_at_least_2": round(hit_at_least_2_rate / baseline_at_least_2, 4)
        if baseline_at_least_2 else 0.0,
        "lift_hit_all_3": round(hit_all_3_rate / baseline_all_3, 4)
        if baseline_all_3 else 0.0,
        "hit_count_distribution": {
            str(hit_count): int(bucket[f"hit_count_{hit_count}"])
            for hit_count in range(4)
        },
    }


def summarize_predictions(predictions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute rank metrics plus the production XSMN >=2/3 combo KPI."""
    overall = _empty_prediction_bucket()
    by_region: dict[str, dict[str, float]] = defaultdict(_empty_prediction_bucket)
    by_station: dict[str, dict[str, float]] = defaultdict(_empty_prediction_bucket)

    for row in predictions:
        pairs = prediction_pairs(row)
        tail_set = set(_parse_int_list(row.get("tail_set")))
        if not pairs or not tail_set:
            continue

        hit_count = sum(pair in tail_set for pair in pairs)
        hit_1 = int(pairs[0] in tail_set)
        hit_3 = int(hit_count >= 1)
        hit_at_least_2 = int(hit_count >= 2)
        hit_all_3 = int(hit_count == len(pairs) and len(pairs) == 3)
        baseline_1 = random_hit_probability(len(tail_set), 1)
        baseline_3 = random_hit_probability(len(tail_set), len(pairs))
        baseline_at_least_2 = random_at_least_hits_probability(
            len(tail_set), len(pairs), min_hits=2
        )
        baseline_all_3 = random_at_least_hits_probability(
            len(tail_set), len(pairs), min_hits=3
        )

        region, province = _station_key(row)
        keys = [
            overall,
            by_region[region],
            by_station[f"{region}/{province}"],
        ]
        for bucket in keys:
            bucket["predictions"] += 1
            bucket["hit_1"] += hit_1
            bucket["hit_3"] += hit_3
            bucket["hit_at_least_2"] += hit_at_least_2
            bucket["hit_all_3"] += hit_all_3
            bucket[f"hit_count_{min(hit_count, 3)}"] += 1
            bucket["baseline_hit_1_sum"] += baseline_1
            bucket["baseline_hit_3_sum"] += baseline_3
            bucket["baseline_hit_at_least_2_sum"] += baseline_at_least_2
            bucket["baseline_hit_all_3_sum"] += baseline_all_3

    return {
        "overall": _finalize_prediction_bucket(overall),
        "by_region": {
            key: _finalize_prediction_bucket(value)
            for key, value in sorted(by_region.items())
        },
        "by_station": {
            key: _finalize_prediction_bucket(value)
            for key, value in sorted(by_station.items())
        },
    }


def summarize_profit(profit_rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute cost, revenue, profit, and ROI from profit_tracking rows."""
    def empty() -> dict[str, float]:
        return {"records": 0, "cost": 0.0, "revenue": 0.0, "profit": 0.0}

    overall = empty()
    by_region: dict[str, dict[str, float]] = defaultdict(empty)
    by_station: dict[str, dict[str, float]] = defaultdict(empty)

    for row in profit_rows:
        cost = float(row.get("cost") or 0)
        revenue = float(row.get("revenue") or 0)
        profit = float(row.get("profit") or (revenue - cost))
        region, province = _station_key(row)
        for bucket in (overall, by_region[region], by_station[f"{region}/{province}"]):
            bucket["records"] += 1
            bucket["cost"] += cost
            bucket["revenue"] += revenue
            bucket["profit"] += profit

    def finalize(bucket: dict[str, float]) -> dict[str, float]:
        cost = bucket["cost"]
        return {
            "records": int(bucket["records"]),
            "cost": round(cost, 2),
            "revenue": round(bucket["revenue"], 2),
            "profit": round(bucket["profit"], 2),
            "roi": round(bucket["profit"] / cost, 4) if cost else 0.0,
        }

    return {
        "overall": finalize(overall),
        "by_region": {k: finalize(v) for k, v in sorted(by_region.items())},
        "by_station": {k: finalize(v) for k, v in sorted(by_station.items())},
    }


def summarize_rolling(
    predictions: Iterable[dict[str, Any]],
    windows: tuple[int, ...] = (7, 30, 90),
) -> dict[str, Any]:
    """Compute latest rolling Hit@3 by station using draw counts."""
    rows_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        if not prediction_pairs(row) or not _parse_int_list(row.get("tail_set")):
            continue
        region, province = _station_key(row)
        rows_by_station[f"{region}/{province}"].append(row)

    output: dict[str, Any] = {}
    for station, rows in sorted(rows_by_station.items()):
        rows = sorted(rows, key=lambda r: str(r.get("prediction_date") or ""))
        station_metrics: dict[str, Any] = {}
        for window in windows:
            sample = rows[-window:]
            hits = 0
            combo_hits = 0
            for row in sample:
                pairs = prediction_pairs(row)
                tail_set = set(_parse_int_list(row.get("tail_set")))
                hit_count = sum(pair in tail_set for pair in pairs)
                hits += int(hit_count >= 1)
                combo_hits += int(hit_count >= 2)
            station_metrics[f"last_{window}"] = {
                "draws": len(sample),
                "hit_3": hits,
                "hit_3_rate": round(hits / len(sample), 4) if sample else 0.0,
                "hit_at_least_2": combo_hits,
                "hit_at_least_2_rate": round(combo_hits / len(sample), 4) if sample else 0.0,
            }
        output[station] = station_metrics
    return output


def summarize_model_contribution(
    predictions: Iterable[dict[str, Any]],
    model_predictions: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Estimate each sub-model's overlap with final picks and matched pairs."""
    final_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    fallback_by_date_region: dict[tuple[str, str], dict[str, Any]] = {}

    for row in predictions:
        pairs = prediction_pairs(row)
        if not pairs:
            continue
        region, province = _station_key(row)
        key = (str(row.get("prediction_date")), region, province)
        final_by_key[key] = row
        fallback_by_date_region[(str(row.get("prediction_date")), region)] = row

    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "logs": 0,
            "successful_logs": 0,
            "overlap_pairs": 0,
            "hit_overlap_pairs": 0,
        }
    )

    for log in model_predictions:
        model_name = str(log.get("model_name") or "unknown")
        bucket = buckets[model_name]
        bucket["logs"] += 1
        if log.get("status") != "success":
            continue
        model_pairs = set(prediction_pairs(log, k=5))
        if not model_pairs:
            continue
        bucket["successful_logs"] += 1

        region, province = _station_key(log)
        date_str = str(log.get("prediction_date"))
        final = final_by_key.get((date_str, region, province))
        if final is None:
            final = final_by_key.get((date_str, region, "all"))
        if final is None:
            final = fallback_by_date_region.get((date_str, region))
        if final is None:
            continue

        final_pairs = set(prediction_pairs(final))
        matched_pairs = set(_parse_int_list(final.get("matched_pairs")))
        overlap = model_pairs & final_pairs
        bucket["overlap_pairs"] += len(overlap)
        bucket["hit_overlap_pairs"] += len(overlap & matched_pairs)

    output: dict[str, Any] = {}
    for model_name, bucket in sorted(buckets.items()):
        successful = bucket["successful_logs"]
        output[model_name] = {
            "logs": int(bucket["logs"]),
            "successful_logs": int(successful),
            "avg_final_overlap": round(bucket["overlap_pairs"] / successful, 4) if successful else 0.0,
            "hit_overlap_pairs": int(bucket["hit_overlap_pairs"]),
        }
    return output


def build_backtest_report(
    predictions: list[dict[str, Any]],
    model_predictions: list[dict[str, Any]] | None = None,
    profit_rows: list[dict[str, Any]] | None = None,
    windows: tuple[int, ...] = (7, 30, 90),
) -> dict[str, Any]:
    """Build the full backtest report payload."""
    model_predictions = model_predictions or []
    profit_rows = profit_rows or []
    return {
        "prediction_metrics": summarize_predictions(predictions),
        "profit_metrics": summarize_profit(profit_rows),
        "rolling_hit_3": summarize_rolling(predictions, windows=windows),
        "model_contribution": summarize_model_contribution(predictions, model_predictions),
    }


def format_backtest_report(report: dict[str, Any]) -> str:
    """Format a concise plain-text report for CLI and Telegram-friendly logs."""
    overall = report["prediction_metrics"]["overall"]
    profit = report["profit_metrics"]["overall"]
    lines = [
        "2D Tail Saved Out-of-Time Evaluation",
        "",
        (
            "Overall: "
            f"n={overall['predictions']} | "
            f"Hit@1={overall['hit_1_rate']:.1%} "
            f"(random {overall['baseline_hit_1_rate']:.1%}, lift {overall['lift_hit_1']:.2f}x) | "
            f"Hit@3={overall['hit_3_rate']:.1%} "
            f"(random {overall['baseline_hit_3_rate']:.1%}, lift {overall['lift_hit_3']:.2f}x) | "
            f">=2/3={overall['hit_at_least_2_rate']:.1%} "
            f"(random {overall['baseline_hit_at_least_2_rate']:.1%}, "
            f"lift {overall['lift_hit_at_least_2']:.2f}x)"
        ),
        (
            "ROI: "
            f"cost={profit['cost']:,.0f} | revenue={profit['revenue']:,.0f} | "
            f"profit={profit['profit']:,.0f} | roi={profit['roi']:.1%}"
        ),
        "",
        "By region:",
    ]

    for region, metrics in report["prediction_metrics"]["by_region"].items():
        lines.append(
            f"- {region}: n={metrics['predictions']} | "
            f"Hit@3={metrics['hit_3_rate']:.1%} | "
            f">=2/3={metrics['hit_at_least_2_rate']:.1%} | "
            f"random>=2/3={metrics['baseline_hit_at_least_2_rate']:.1%} | "
            f"lift>=2/3={metrics['lift_hit_at_least_2']:.2f}x"
        )

    if report["model_contribution"]:
        lines.extend(["", "Model contribution:"])
        for model, metrics in report["model_contribution"].items():
            lines.append(
                f"- {model}: logs={metrics['logs']} | success={metrics['successful_logs']} | "
                f"avg_final_overlap={metrics['avg_final_overlap']:.2f} | "
                f"hit_overlap_pairs={metrics['hit_overlap_pairs']}"
            )

    return "\n".join(lines)


def parse_iso_date(value: str) -> date:
    """Parse YYYY-MM-DD and return a date."""
    return date.fromisoformat(value)
