"""Deterministic, leakage-safe evidence packet for ``LLM_Gen``."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json
import math
from numbers import Integral
from typing import Iterable, Mapping, Optional, Sequence


def _date_text(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def _validate_provinces(provinces: Sequence[str]) -> tuple[str, str]:
    if isinstance(provinces, (str, bytes)):
        raise ValueError("llm_gen requires exactly two distinct provinces")
    normalized = tuple(
        str(value).strip()
        for value in provinces
        if value is not None and str(value).strip()
    )
    if len(normalized) != 2 or len(set(normalized)) != 2:
        raise ValueError("llm_gen requires exactly two distinct provinces")
    return normalized[0], normalized[1]


def _safe_score(value: object) -> Optional[float]:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return round(score, 12) if math.isfinite(score) else None


def _normalize_source_top_two(values: object) -> list[dict[str, object]]:
    """Validate only raw rank 1-2; rank 3+ can never be promoted."""
    if not isinstance(values, (list, tuple)):
        return []
    normalized: list[dict[str, object]] = []
    seen: set[int] = set()
    for source_rank, item in enumerate(values[:2], start=1):
        if isinstance(item, Mapping):
            raw_pair = item.get("pair", item.get("number"))
            raw_score = item.get("score", item.get("ranking_score_uncalibrated"))
        elif isinstance(item, (list, tuple)) and item:
            raw_pair = item[0]
            raw_score = item[1] if len(item) > 1 else None
        else:
            continue
        if isinstance(raw_pair, bool) or not isinstance(raw_pair, Integral):
            continue
        pair = int(raw_pair)
        if not 0 <= pair <= 99 or pair in seen:
            continue
        seen.add(pair)
        normalized.append(
            {
                "pair": pair,
                "source_rank": source_rank,
                "source_score_uncalibrated": _safe_score(raw_score),
            }
        )
    return normalized


def _digit_features(pair: int) -> dict[str, object]:
    tens, unit = divmod(pair, 10)
    return {
        "tens_digit": tens,
        "unit_digit": unit,
        "digit_sum": tens + unit,
        "is_even": pair % 2 == 0,
        "is_double": tens == unit,
        "reversed_pair": unit * 10 + tens,
    }


def _normalize_history_sets(
    values: Optional[Iterable[Iterable[int]]],
) -> tuple[frozenset[int], ...]:
    normalized = []
    for value in values or ():
        pairs = frozenset(
            int(pair)
            for pair in value
            if isinstance(pair, (int, float, str))
            and str(pair).lstrip("-").isdigit()
            and 0 <= int(pair) <= 99
        )
        normalized.append(pairs)
    return tuple(normalized)


def build_evidence_packet(
    model_results: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date | str,
    *,
    effective_weights: Optional[Mapping[str, float]] = None,
    production_top_pairs: Optional[Sequence[object]] = None,
    recent_tails_by_province: Optional[Mapping[str, Sequence[int]]] = None,
    recent_province_tails: Optional[Mapping[str, set[int]]] = None,
    combo_history_tail_sets: Optional[Iterable[Iterable[int]]] = None,
) -> dict[str, object]:
    """Build a canonical packet without mutating any source result.

    The source grain is ``model_name@province``. Only raw rank 1-2 is
    eligible, family votes are deduplicated across provinces, and province
    coverage remains a separate feature.
    """
    province_scope = _validate_provinces(provinces)
    allowed = set(province_scope)
    sources: list[dict[str, object]] = []
    skipped_sources: list[str] = []
    seen_sources: set[str] = set()

    for result in model_results:
        family = str(result.get("model_name") or "").strip()
        province = str(result.get("province") or "").strip()
        if not family or province not in allowed:
            continue
        source_id = f"{family}@{province}"
        pairs = _normalize_source_top_two(result.get("top_pairs"))
        if str(result.get("status") or "") != "success" or not pairs:
            skipped_sources.append(source_id)
            continue
        if source_id in seen_sources:
            skipped_sources.append(f"{source_id}:duplicate")
            continue
        seen_sources.add(source_id)
        sources.append(
            {
                "source_id": source_id,
                "model_family": family,
                "model_version": (
                    str(result.get("model_version"))
                    if result.get("model_version") is not None
                    else None
                ),
                "province": province,
                "eligible_top_2": pairs,
            }
        )

    sources.sort(key=lambda item: (str(item["model_family"]), str(item["province"])))
    active_families = sorted({str(item["model_family"]) for item in sources})

    weights: dict[str, float] = {}
    supplied_weights = effective_weights or {}
    for family in active_families:
        if family not in supplied_weights:
            weights[family] = 1.0
            continue
        score = _safe_score(supplied_weights.get(family))
        weights[family] = score if score is not None and score >= 0 else 0.0
    weight_total = sum(weights.values())

    family_ranks: dict[int, dict[str, int]] = defaultdict(dict)
    provinces_by_pair: dict[int, set[str]] = defaultdict(set)
    source_ids_by_pair: dict[int, set[str]] = defaultdict(set)
    source_scores_by_pair: dict[int, list[float]] = defaultdict(list)
    for source in sources:
        family = str(source["model_family"])
        province = str(source["province"])
        source_id = str(source["source_id"])
        for candidate in source["eligible_top_2"]:  # type: ignore[index]
            pair = int(candidate["pair"])
            rank = int(candidate["source_rank"])
            previous = family_ranks[pair].get(family)
            if previous is None or rank < previous:
                family_ranks[pair][family] = rank
            provinces_by_pair[pair].add(province)
            source_ids_by_pair[pair].add(source_id)
            score = candidate.get("source_score_uncalibrated")
            if score is not None:
                source_scores_by_pair[pair].append(float(score))

    history_sets = _normalize_history_sets(combo_history_tail_sets)
    recent_by_province = {
        province: tuple(int(pair) for pair in (recent_tails_by_province or {}).get(province, ()))
        for province in province_scope
    }
    latest_by_province = {
        province: frozenset(
            int(pair) for pair in (recent_province_tails or {}).get(province, set())
        )
        for province in province_scope
    }

    candidates = []
    for pair in sorted(family_ranks):
        ranks = family_ranks[pair]
        voting_families = sorted(ranks)
        recent_occurrences = sum(
            tails.count(pair) for tails in recent_by_province.values()
        )
        latest_provinces = sorted(
            province for province, tails in latest_by_province.items() if pair in tails
        )
        history_hits = sum(pair in tail_set for tail_set in history_sets)
        scores = source_scores_by_pair[pair]
        candidates.append(
            {
                "pair": pair,
                "family_vote_count": len(voting_families),
                "active_family_count": len(active_families),
                "family_vote_ratio": round(
                    len(voting_families) / max(len(active_families), 1), 12
                ),
                "credibility_weighted_vote": round(
                    sum(weights[family] for family in voting_families)
                    / weight_total if weight_total > 0 else 0.0,
                    12,
                ),
                "province_count": len(provinces_by_pair[pair]),
                "provinces": sorted(provinces_by_pair[pair]),
                "voting_families": voting_families,
                "best_rank_by_family": {
                    family: ranks[family] for family in voting_families
                },
                "source_ids": sorted(source_ids_by_pair[pair]),
                "mean_source_score_uncalibrated": (
                    round(sum(scores) / len(scores), 12) if scores else None
                ),
                "recent_same_weekday_tail_occurrences": recent_occurrences,
                "appeared_in_latest_provinces": latest_provinces,
                "matched_history_hit_count": history_hits,
                "matched_history_count": len(history_sets),
                "matched_history_frequency": round(
                    history_hits / len(history_sets), 12
                ) if history_sets else 0.0,
                "digit_features": _digit_features(pair),
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(item["family_vote_count"]),
            -int(item["province_count"]),
            min(item["best_rank_by_family"].values()),  # type: ignore[union-attr]
            int(item["pair"]),
        )
    )
    candidate_pairs = {int(item["pair"]) for item in candidates}

    cooccurrence = []
    ordered_pairs = sorted(candidate_pairs)
    for index, left in enumerate(ordered_pairs):
        for right in ordered_pairs[index + 1 :]:
            joint_hits = sum(
                left in tail_set and right in tail_set for tail_set in history_sets
            )
            if joint_hits:
                cooccurrence.append(
                    {
                        "pair_a": left,
                        "pair_b": right,
                        "joint_hit_count": joint_hits,
                        "history_count": len(history_sets),
                        "joint_frequency": round(joint_hits / len(history_sets), 12),
                    }
                )
    cooccurrence.sort(
        key=lambda item: (
            -int(item["joint_hit_count"]),
            int(item["pair_a"]),
            int(item["pair_b"]),
        )
    )

    production = []
    for rank, item in enumerate((production_top_pairs or ())[:3], start=1):
        if isinstance(item, Mapping):
            raw_pair = item.get("pair", item.get("number"))
            raw_score = item.get("score")
        elif isinstance(item, (list, tuple)) and item:
            raw_pair = item[0]
            raw_score = item[1] if len(item) > 1 else None
        else:
            raw_pair, raw_score = item, None
        try:
            pair = int(raw_pair)
        except (TypeError, ValueError):
            continue
        if pair in candidate_pairs:
            production.append(
                {
                    "pair": pair,
                    "rank": rank,
                    "score_uncalibrated": _safe_score(raw_score),
                }
            )

    return {
        "packet_version": "llm_gen_evidence_v1",
        "region": "XSMN",
        "scope": "all",
        "target_date": _date_text(target_date),
        "data_cutoff": _date_text(target_date),
        "history_cutoff_rule": "draw_date < target_date",
        "provinces": list(province_scope),
        "top_pairs_per_source": 2,
        "active_model_families": active_families,
        "effective_weights": {
            family: round(
                weights[family] / weight_total if weight_total > 0 else 0.0,
                12,
            )
            for family in active_families
        },
        "sources": sources,
        "skipped_sources": sorted(set(skipped_sources)),
        "candidate_pool": candidates,
        "production_top_3": production,
        "cooccurrence_top": cooccurrence[:30],
    }


def compute_input_hash(packet: Mapping[str, object]) -> str:
    """Hash canonical JSON so an identical rerun can reuse provider output."""
    canonical = json.dumps(
        packet,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
