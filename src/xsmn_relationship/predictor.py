"""Pure consensus/co-occurrence scorer for the XSMN relationship shadow."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from itertools import combinations
import math
from typing import Iterable, Mapping, Optional, Sequence

from .domain import MatchedOccasion, RelationshipConfig, validate_provinces


MODEL_NAME = "relationship"
MODEL_VERSION = "relationship_v1"
SCORE_SEMANTICS = "ranking_score_uncalibrated"
VALID_VARIANTS = frozenset({"R-A", "R-B", "R-C"})


def _rounded(value: float) -> float:
    return round(float(value), 12)


def _safe_source_score(value: object) -> Optional[float]:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return _rounded(score) if math.isfinite(score) else None


def _normalize_top_pairs(values: object, top_k: int) -> list[dict[str, object]]:
    """Read, dedupe and truncate one source without mutating its result."""
    if not isinstance(values, (list, tuple)):
        return []
    normalized: list[dict[str, object]] = []
    seen: set[int] = set()
    for source_rank, item in enumerate(values[:top_k], start=1):
        if isinstance(item, Mapping):
            raw_pair = item.get("pair", item.get("number"))
            raw_score = item.get("score", item.get("probability"))
        elif isinstance(item, (list, tuple)) and item:
            raw_pair = item[0]
            raw_score = item[1] if len(item) > 1 else None
        else:
            continue
        try:
            pair = int(raw_pair)
        except (TypeError, ValueError):
            continue
        if not 0 <= pair <= 99 or pair in seen:
            continue
        seen.add(pair)
        normalized.append(
            {
                "pair": pair,
                "rank": source_rank,
                "source_score": _safe_source_score(raw_score),
            }
        )
    return normalized


def build_consensus_evidence(
    model_results: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    config: RelationshipConfig,
    family_weights: Optional[Mapping[str, float]] = None,
) -> dict:
    """Aggregate one independent vote per family and separate province support."""
    province_scope = validate_provinces(provinces)
    allowed = set(province_scope)
    expected_families: set[str] = set()
    sources: list[dict[str, object]] = []

    for result in model_results:
        province = str(result.get("province") or "")
        family = str(result.get("model_name") or "").strip()
        if province not in allowed or not family:
            continue
        expected_families.add(family)
        pairs = _normalize_top_pairs(
            result.get("top_pairs"), config.top_k_per_source
        )
        if str(result.get("status") or "") != "success" or not pairs:
            continue
        sources.append(
            {
                "model_family": family,
                "model_version": (
                    str(result.get("model_version"))
                    if result.get("model_version") is not None
                    else None
                ),
                "created_at": (
                    str(result.get("created_at"))
                    if result.get("created_at") is not None
                    else None
                ),
                "province": province,
                "top_5": pairs,
            }
        )

    sources.sort(
        key=lambda item: (
            str(item["model_family"]),
            str(item["province"]),
            tuple(
                (int(pair["pair"]), int(pair["rank"]))
                for pair in item["top_5"]  # type: ignore[index]
            ),
        )
    )
    active_families = sorted({str(source["model_family"]) for source in sources})
    skipped_families = sorted(expected_families - set(active_families))
    weights: dict[str, float] = {}
    for family in active_families:
        try:
            value = float((family_weights or {}).get(family, 1.0))
        except (TypeError, ValueError):
            value = 1.0
        weights[family] = value if math.isfinite(value) and value > 0 else 1.0
    total_weight = sum(weights.values())

    family_ranks: dict[int, dict[str, int]] = defaultdict(dict)
    pair_provinces: dict[int, set[str]] = defaultdict(set)
    for source in sources:
        family = str(source["model_family"])
        province = str(source["province"])
        for item in source["top_5"]:  # type: ignore[index]
            pair = int(item["pair"])
            rank = int(item["rank"])
            previous = family_ranks[pair].get(family)
            if previous is None or rank < previous:
                family_ranks[pair][family] = rank
            pair_provinces[pair].add(province)

    family_count = len(active_families)
    nodes: dict[int, dict[str, object]] = {}
    for pair in sorted(family_ranks):
        ranks = family_ranks[pair]
        voting_families = sorted(ranks)
        vote_count = len(voting_families)
        vote_ratio = vote_count / family_count if family_count else 0.0
        credibility_vote = (
            sum(weights[family] for family in voting_families) / total_weight
            if total_weight
            else 0.0
        )
        rank_score = sum(
            (config.top_k_per_source - rank + 1) / config.top_k_per_source
            for rank in ranks.values()
        ) / vote_count
        province_coverage = len(pair_provinces[pair]) / len(province_scope)
        node_score = (vote_ratio + credibility_vote + rank_score + province_coverage) / 4.0
        nodes[pair] = {
            "pair": pair,
            "family_vote_count": vote_count,
            "active_family_count": family_count,
            "family_vote_ratio": _rounded(vote_ratio),
            "credibility_weighted_vote": _rounded(credibility_vote),
            "rank_score": _rounded(rank_score),
            "province_coverage": _rounded(province_coverage),
            "node_score": _rounded(node_score),
            "voting_families": voting_families,
            "provinces": sorted(pair_provinces[pair]),
            "best_rank_by_family": {
                family: ranks[family] for family in voting_families
            },
        }

    return {
        "active_model_families": active_families,
        "skipped_model_families": skipped_families,
        "family_weights": {family: _rounded(weights[family]) for family in active_families},
        "source_top_5": sources,
        "nodes": nodes,
    }


def _anchor_order(nodes: Mapping[int, Mapping[str, object]]) -> list[int]:
    return sorted(
        nodes,
        key=lambda pair: (
            -float(nodes[pair]["family_vote_ratio"]),
            -float(nodes[pair]["credibility_weighted_vote"]),
            -float(nodes[pair]["rank_score"]),
            -float(nodes[pair]["province_coverage"]),
            pair,
        ),
    )


def select_anchor(
    nodes: Mapping[int, Mapping[str, object]],
    recent_occasions: Sequence[MatchedOccasion],
    config: RelationshipConfig,
    *,
    apply_guard: bool = True,
) -> tuple[Optional[int], list[dict[str, object]]]:
    """Select the first consensus anchor that passes the two-occasion guard."""
    recent = tuple(recent_occasions)[-config.recent_anchor_lookback :]
    audit: list[dict[str, object]] = []
    for pair in _anchor_order(nodes):
        vote_ratio = float(nodes[pair]["family_vote_ratio"])
        if vote_ratio < config.min_anchor_vote_ratio:
            audit.append(
                {
                    "pair": pair,
                    "accepted": False,
                    "reason": "below_min_anchor_vote_ratio",
                    "family_vote_ratio": vote_ratio,
                    "recent_hits": None,
                }
            )
            continue
        hit_dates = [
            occasion.draw_date.isoformat()
            for occasion in recent
            if pair in occasion.merged_tails
        ]
        rejected = apply_guard and len(hit_dates) >= config.reject_anchor_if_hits
        audit.append(
            {
                "pair": pair,
                "accepted": not rejected,
                "reason": (
                    "consecutive_merged_hit_2of2" if rejected else "eligible"
                ),
                "family_vote_ratio": vote_ratio,
                "recent_hits": len(hit_dates),
                "hit_dates": hit_dates,
            }
        )
        if not rejected:
            return pair, audit
    return None, audit


def build_edge_evidence(
    left: int,
    right: int,
    history: Sequence[MatchedOccasion],
    provinces: Sequence[str],
    config: RelationshipConfig,
) -> dict[str, object]:
    """Calculate merged and cross-province pair evidence with shrinkage."""
    province_a, province_b = validate_provinces(provinces)
    count = len(history)
    if count < 1:
        raise ValueError("pair evidence requires history")
    n_left = sum(left in occasion.merged_tails for occasion in history)
    n_right = sum(right in occasion.merged_tails for occasion in history)
    n_joint = sum(
        left in occasion.merged_tails and right in occasion.merged_tails
        for occasion in history
    )
    cross_count = 0
    for occasion in history:
        tails_a = occasion.tails_by_province[province_a]
        tails_b = occasion.tails_by_province[province_b]
        cross_count += int(
            (left in tails_a and right in tails_b)
            or (right in tails_a and left in tails_b)
        )

    p_left = n_left / count
    p_right = n_right / count
    independent_joint = p_left * p_right
    joint_shrunk = (
        n_joint + config.prior_strength * independent_joint
    ) / (count + config.prior_strength)

    p_left_a = sum(
        left in occasion.tails_by_province[province_a] for occasion in history
    ) / count
    p_left_b = sum(
        left in occasion.tails_by_province[province_b] for occasion in history
    ) / count
    p_right_a = sum(
        right in occasion.tails_by_province[province_a] for occasion in history
    ) / count
    p_right_b = sum(
        right in occasion.tails_by_province[province_b] for occasion in history
    ) / count
    cross_forward = p_left_a * p_right_b
    cross_reverse = p_right_a * p_left_b
    independent_cross = min(
        1.0,
        cross_forward + cross_reverse - cross_forward * cross_reverse,
    )
    cross_shrunk = (
        cross_count + config.prior_strength * independent_cross
    ) / (count + config.prior_strength)
    eligible = n_joint >= config.min_pair_support_count
    merged_excess = (
        max(joint_shrunk - independent_joint, 0.0)
        / max(1.0 - independent_joint, 1e-12)
    )
    cross_excess = (
        max(cross_shrunk - independent_cross, 0.0)
        / max(1.0 - independent_cross, 1e-12)
    )
    association_strength = (
        (merged_excess + cross_excess) / 2.0 if eligible else 0.0
    )
    lift = (
        (n_joint * count) / (n_left * n_right)
        if n_left and n_right
        else 0.0
    )
    shrunk_lift = joint_shrunk / independent_joint if independent_joint else 0.0
    return {
        "pair": [min(left, right), max(left, right)],
        "history_count": count,
        "left_count": n_left,
        "right_count": n_right,
        "merged_joint_count": n_joint,
        "merged_support": _rounded(n_joint / count),
        "confidence_left_to_right": _rounded(n_joint / n_left if n_left else 0.0),
        "confidence_right_to_left": _rounded(n_joint / n_right if n_right else 0.0),
        "lift": _rounded(lift),
        "independent_joint_prior": _rounded(independent_joint),
        "merged_joint_shrunk": _rounded(joint_shrunk),
        "lift_shrunk": _rounded(shrunk_lift),
        "cross_province_joint_count": cross_count,
        "cross_province_support": _rounded(cross_count / count),
        "cross_province_prior": _rounded(independent_cross),
        "cross_province_joint_shrunk": _rounded(cross_shrunk),
        "merged_excess_over_prior": _rounded(merged_excess),
        "cross_excess_over_prior": _rounded(cross_excess),
        "support_eligible": eligible,
        "association_strength": _rounded(association_strength),
    }


def _direct_combo_evidence(
    combo: tuple[int, int, int],
    history: Sequence[MatchedOccasion],
    config: RelationshipConfig,
) -> dict[str, object]:
    count = len(history)
    hit_count = sum(
        len(set(combo) & occasion.merged_tails) >= 2 for occasion in history
    )
    marginals = [
        sum(pair in occasion.merged_tails for occasion in history) / count
        for pair in combo
    ]
    p_a, p_b, p_c = marginals
    independent_prior = min(
        1.0,
        p_a * p_b + p_a * p_c + p_b * p_c - 2.0 * p_a * p_b * p_c,
    )
    shrunk = (
        hit_count + config.prior_strength * independent_prior
    ) / (count + config.prior_strength)
    excess_over_prior = (
        max(shrunk - independent_prior, 0.0)
        / max(1.0 - independent_prior, 1e-12)
    )
    return {
        "hit_count_2of3": hit_count,
        "history_count": count,
        "rate_2of3": _rounded(hit_count / count),
        "independent_2of3_prior": _rounded(independent_prior),
        "rate_2of3_shrunk": _rounded(shrunk),
        "excess_over_prior": _rounded(excess_over_prior),
    }


def _base_result(
    target_date: date,
    provinces: tuple[str, str],
    config: RelationshipConfig,
    *,
    variant: str,
    apply_anchor_guard: bool,
) -> dict[str, object]:
    return {
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "mode": "shadow",
        "prediction_mode": "shadow",
        "score_semantics": SCORE_SEMANTICS,
        "target_date": target_date.isoformat(),
        "data_cutoff": target_date.isoformat(),
        "provinces": list(provinces),
        "variant": variant,
        "anchor_guard": apply_anchor_guard,
        "config": config.to_dict(),
    }


def _abstain(base: dict[str, object], status: str, reason: str, **audit: object) -> dict:
    metadata = {**audit, "status": status, "reason": reason}
    return {
        **base,
        "status": status,
        "reason": reason,
        "top_3": [],
        "selected_evidence": [],
        "run_metadata": metadata,
    }


def predict_relationship(
    model_results: Iterable[Mapping[str, object]],
    matched_history: Iterable[MatchedOccasion],
    provinces: Sequence[str],
    target_date: date,
    config: Optional[RelationshipConfig] = None,
    *,
    family_weights: Optional[Mapping[str, float]] = None,
    variant: str = "R-C",
    apply_anchor_guard: bool = True,
) -> dict:
    """Return one deterministic, audit-ready relationship Top-3 or abstention."""
    province_scope = validate_provinces(provinces)
    config = config or RelationshipConfig()
    if variant not in VALID_VARIANTS:
        raise ValueError(f"unsupported relationship variant: {variant}")
    base = _base_result(
        target_date,
        province_scope,
        config,
        variant=variant,
        apply_anchor_guard=apply_anchor_guard,
    )
    consensus = build_consensus_evidence(
        model_results,
        province_scope,
        config,
        family_weights,
    )
    common_audit = {
        "provinces": list(province_scope),
        "data_cutoff": target_date.isoformat(),
        "history_cutoff_rule": "draw_date < target_date",
        "active_model_families": consensus["active_model_families"],
        "skipped_model_families": consensus["skipped_model_families"],
        "family_weights": consensus["family_weights"],
        "source_top_5": consensus["source_top_5"],
        "config": config.to_dict(),
        "variant": variant,
        "anchor_guard": apply_anchor_guard,
    }
    if len(consensus["active_model_families"]) < config.min_active_model_families:
        return _abstain(
            base,
            "insufficient_active_models",
            "active_model_families_below_minimum",
            **common_audit,
        )

    history_by_date: dict[date, MatchedOccasion] = {}
    duplicate_dates: list[str] = []
    conflicting_dates: list[str] = []
    for occasion in matched_history:
        if (
            occasion.draw_date >= target_date
            or set(occasion.tails_by_province) != set(province_scope)
        ):
            continue
        existing = history_by_date.get(occasion.draw_date)
        if existing is None:
            history_by_date[occasion.draw_date] = occasion
            continue
        duplicate_dates.append(occasion.draw_date.isoformat())
        if existing.tails_by_province != occasion.tails_by_province:
            conflicting_dates.append(occasion.draw_date.isoformat())
    if conflicting_dates:
        return _abstain(
            base,
            "error",
            "conflicting_duplicate_history_date",
            **common_audit,
            conflicting_history_dates=sorted(set(conflicting_dates)),
        )
    history = tuple(
        history_by_date[draw_date]
        for draw_date in sorted(history_by_date)[-config.history_lookback_occurrences :]
    )
    if len(history) < config.recent_anchor_lookback:
        return _abstain(
            base,
            "insufficient_recent_history",
            "fewer_than_two_matched_occasions",
            **common_audit,
            matched_history_count=len(history),
        )
    if len(history) < config.min_history_occurrences:
        return _abstain(
            base,
            "insufficient_matched_draws",
            "matched_history_below_minimum",
            **common_audit,
            matched_history_count=len(history),
        )

    nodes: dict[int, dict[str, object]] = consensus["nodes"]
    recent = history[-config.recent_anchor_lookback :]
    anchor, anchor_audit = select_anchor(
        nodes,
        recent,
        config,
        apply_guard=apply_anchor_guard,
    )
    history_audit = {
        **common_audit,
        "matched_history_count": len(history),
        "matched_history_start": history[0].draw_date.isoformat(),
        "matched_history_end": history[-1].draw_date.isoformat(),
        "recent_matched_occasions": [
            {
                "draw_date": occasion.draw_date.isoformat(),
                "merged_tail_count": len(occasion.merged_tails),
            }
            for occasion in recent
        ],
        "anchor_audit": anchor_audit,
        "deduplicated_history_dates": sorted(set(duplicate_dates)),
    }
    if anchor is None:
        return _abstain(
            base,
            "no_eligible_anchor",
            "no_anchor_passed_vote_and_recency_rules",
            **history_audit,
        )

    candidate_pairs = sorted(pair for pair in nodes if pair != anchor)
    feasible_companions = [
        (left, right)
        for left, right in combinations(candidate_pairs, 2)
        if not config.require_distinct_unit_digits
        or len({anchor % 10, left % 10, right % 10}) == 3
    ]
    if not feasible_companions:
        return _abstain(
            base,
            "insufficient_candidate_diversity",
            "no_top_3_with_distinct_unit_digits",
            **history_audit,
            selected_anchor=anchor,
        )

    edge_cache: dict[tuple[int, int], dict[str, object]] = {}
    for left, right in combinations(sorted(nodes), 2):
        edge_cache[(left, right)] = build_edge_evidence(
            left,
            right,
            history,
            province_scope,
            config,
        )
    combo_records: list[dict[str, object]] = []
    for left, right in feasible_companions:
        combo = (anchor, left, right)
        edge_keys = [tuple(sorted(edge)) for edge in combinations(combo, 2)]
        edge_strengths = [
            float(edge_cache[key]["association_strength"])
            for key in edge_keys
        ]
        node_score = sum(float(nodes[pair]["node_score"]) for pair in combo) / 3.0
        triangle_score = sum(edge_strengths) / 3.0
        direct = _direct_combo_evidence(combo, history, config)
        combo_records.append(
            {
                "combo": combo,
                "node_score": node_score,
                "triangle_score": triangle_score,
                "triangle_min_edge": min(edge_strengths),
                "edge_keys": edge_keys,
                "edge_strengths": edge_strengths,
                "direct": direct,
            }
        )

    for record in combo_records:
        direct_score = float(record["direct"]["excess_over_prior"])  # type: ignore[index]
        record["direct_score"] = direct_score
        components = [
            (float(record["node_score"]), config.node_component_weight)
        ]
        if variant in {"R-B", "R-C"}:
            components.append(
                (float(record["triangle_score"]), config.edge_component_weight)
            )
        if variant == "R-C":
            components.append((direct_score, config.combo_component_weight))
        total_component_weight = sum(weight for _, weight in components)
        record["relationship_score"] = sum(
            value * weight for value, weight in components
        ) / total_component_weight

    def selection_key(record: Mapping[str, object]) -> tuple:
        numeric = tuple(-pair for pair in record["combo"])  # type: ignore[arg-type]
        score = float(record["relationship_score"])
        if variant == "R-A":
            return score, numeric
        if variant == "R-B":
            return score, float(record["triangle_min_edge"]), numeric
        return (
            score,
            int(record["direct"]["hit_count_2of3"]),  # type: ignore[index]
            float(record["triangle_min_edge"]),
            numeric,
        )

    selected = max(combo_records, key=selection_key)
    combo = tuple(int(pair) for pair in selected["combo"])
    selected_edges = []
    incident_scores: dict[int, list[float]] = defaultdict(list)
    for key, normalized_strength in zip(
        selected["edge_keys"], selected["edge_strengths"]
    ):
        evidence = dict(edge_cache[key])
        evidence["normalized_strength"] = _rounded(float(normalized_strength))
        selected_edges.append(evidence)
        for pair in key:
            incident_scores[pair].append(float(normalized_strength))

    companion_scores: dict[int, float] = {}
    for pair in combo:
        components = [
            (float(nodes[pair]["node_score"]), config.node_component_weight)
        ]
        if variant in {"R-B", "R-C"}:
            components.append(
                (
                    sum(incident_scores[pair]) / len(incident_scores[pair]),
                    config.edge_component_weight,
                )
            )
        if variant == "R-C":
            components.append(
                (float(selected["direct_score"]), config.combo_component_weight)
            )
        denominator = sum(weight for _, weight in components)
        companion_scores[pair] = sum(
            value * weight for value, weight in components
        ) / denominator
    ordered_companions = sorted(
        combo[1:], key=lambda pair: (-companion_scores[pair], pair)
    )
    ordered_top = (anchor, *ordered_companions)
    selected_evidence = [
        {
            "rank": rank,
            "pair": pair,
            "ranking_score_uncalibrated": _rounded(companion_scores[pair]),
            "role": "anchor" if pair == anchor else "companion",
            "node_evidence": nodes[pair],
        }
        for rank, pair in enumerate(ordered_top, start=1)
    ]
    selected_combo = {
        "pairs": list(ordered_top),
        "relationship_score": _rounded(float(selected["relationship_score"])),
        "node_score": _rounded(float(selected["node_score"])),
        "triangle_score": _rounded(float(selected["triangle_score"])),
        "triangle_min_edge": _rounded(float(selected["triangle_min_edge"])),
        "direct_score": _rounded(float(selected["direct_score"])),
        "direct_evidence": selected["direct"],
        "edges": selected_edges,
        "tie_break": (
            ["relationship_score", "numeric_tuple_ascending"]
            if variant == "R-A"
            else [
                "relationship_score",
                "triangle_min_edge",
                "numeric_tuple_ascending",
            ]
            if variant == "R-B"
            else [
                "relationship_score",
                "direct_hit_count_2of3",
                "triangle_min_edge",
                "numeric_tuple_ascending",
            ]
        ),
    }
    run_metadata = {
        **history_audit,
        "selected_anchor": anchor,
        "node_evidence": [nodes[pair] for pair in sorted(nodes)],
        "selected_combo": selected_combo,
        "candidate_count": len(nodes),
        "evaluated_combo_count": len(combo_records),
    }
    return {
        **base,
        "status": "success",
        "top_3": [f"{pair:02d}" for pair in ordered_top],
        "selected_evidence": selected_evidence,
        "relationship_score": selected_combo["relationship_score"],
        "run_metadata": run_metadata,
    }
