"""Interpretable nearest-case predictor for an XSMN province pair."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from typing import Iterable, Mapping, Optional

from .domain import CMRConfig, DrawSnapshot, normalize_tail_rows
from .fingerprint import CoupledFingerprint, SimilarityBreakdown, build_fingerprint, coupled_similarity


@dataclass(frozen=True)
class HistoricalCase:
    target_date: date
    anchor_a: DrawSnapshot
    anchor_b: DrawSnapshot
    label_a: frozenset[int]
    label_b: frozenset[int]
    fingerprint: CoupledFingerprint

    @property
    def label_all(self) -> frozenset[int]:
        return self.label_a | self.label_b


@dataclass(frozen=True)
class RankedNeighbor:
    case: HistoricalCase
    similarity: SimilarityBreakdown


def _latest_before(draws: tuple[DrawSnapshot, ...], target_date: date) -> Optional[DrawSnapshot]:
    dates = [draw.draw_date for draw in draws]
    index = bisect_left(dates, target_date) - 1
    return draws[index] if index >= 0 else None


def _draw_by_date(draws: tuple[DrawSnapshot, ...]) -> dict[date, DrawSnapshot]:
    return {draw.draw_date: draw for draw in draws}


def build_historical_cases(
    draws_by_province: Mapping[str, tuple[DrawSnapshot, ...]],
    provinces: tuple[str, str],
    target_date: date,
) -> tuple[Optional[CoupledFingerprint], Optional[DrawSnapshot], Optional[DrawSnapshot], list[HistoricalCase]]:
    """Create leakage-safe historical contexts and same-province next labels."""
    province_a, province_b = provinces
    draws_a = tuple(draw for draw in draws_by_province.get(province_a, ()) if draw.draw_date < target_date)
    draws_b = tuple(draw for draw in draws_by_province.get(province_b, ()) if draw.draw_date < target_date)
    anchor_a = _latest_before(draws_a, target_date)
    anchor_b = _latest_before(draws_b, target_date)
    if anchor_a is None or anchor_b is None:
        return None, anchor_a, anchor_b, []

    current_fingerprint = build_fingerprint(anchor_a, anchor_b, target_date)
    by_date_a = _draw_by_date(draws_a)
    by_date_b = _draw_by_date(draws_b)
    paired_dates = sorted(set(by_date_a) & set(by_date_b))

    cases: list[HistoricalCase] = []
    for case_date in paired_dates:
        if case_date >= target_date or case_date.weekday() != target_date.weekday():
            continue
        case_anchor_a = _latest_before(draws_a, case_date)
        case_anchor_b = _latest_before(draws_b, case_date)
        if case_anchor_a is None or case_anchor_b is None:
            continue
        cases.append(
            HistoricalCase(
                target_date=case_date,
                anchor_a=case_anchor_a,
                anchor_b=case_anchor_b,
                label_a=by_date_a[case_date].tails,
                label_b=by_date_b[case_date].tails,
                fingerprint=build_fingerprint(case_anchor_a, case_anchor_b, case_date),
            )
        )

    return current_fingerprint, anchor_a, anchor_b, cases


def select_top_three(
    scores: Mapping[int, float],
    direct_overlap: frozenset[int],
) -> Optional[tuple[int, int, int]]:
    """Maximize summed score while admitting at most one overlap candidate."""
    feasible = [
        combo
        for combo in combinations(sorted(scores), 3)
        if len(set(combo) & direct_overlap) <= 1
    ]
    if not feasible:
        return None
    chosen = max(
        feasible,
        key=lambda combo: (sum(scores[number] for number in combo), tuple(-number for number in combo)),
    )
    return tuple(sorted(chosen, key=lambda number: (-scores[number], number)))


def _base_result(
    target_date: date,
    provinces: tuple[str, str],
    config: CMRConfig,
) -> dict:
    return {
        "model_name": "coupled_motif_retrieval_v1",
        "mode": "shadow",
        "target_date": target_date.isoformat(),
        "provinces": list(provinces),
        "score_semantics": "estimated_hit_likelihood_uncalibrated",
        "config": {
            "top_k": config.top_k,
            "min_neighbors": config.min_neighbors,
            "shrinkage_alpha": config.shrinkage_alpha,
            "context_weight": config.context_weight,
            "evidence_cases": config.evidence_cases,
        },
    }


def _insufficient(base: dict, reason: str, **details: object) -> dict:
    return {**base, "status": "insufficient_evidence", "reason": reason, **details, "top_3": []}


def predict_coupled(
    rows: Iterable[Mapping[str, object]],
    provinces: tuple[str, str],
    target_date: date,
    config: Optional[CMRConfig] = None,
) -> dict:
    """Predict three merged tails from coupled historical motifs.

    The function is pure with respect to external systems: it reads only the
    supplied rows and returns an audit-ready dictionary without persistence.
    """
    if len(provinces) != 2 or len(set(provinces)) != 2:
        raise ValueError("CMR requires exactly two distinct provinces")
    config = config or CMRConfig()
    base = _base_result(target_date, provinces, config)
    draws = normalize_tail_rows(rows, provinces=provinces, before_date=target_date)
    current, anchor_a, anchor_b, cases = build_historical_cases(draws, provinces, target_date)
    if current is None or anchor_a is None or anchor_b is None:
        return _insufficient(base, "missing_complete_anchor")

    anchor_payload = {
        provinces[0]: anchor_a.draw_date.isoformat(),
        provinces[1]: anchor_b.draw_date.isoformat(),
    }
    ranked = [
        RankedNeighbor(case=case, similarity=coupled_similarity(current, case.fingerprint, config.context_weight))
        for case in cases
    ]
    ranked = [neighbor for neighbor in ranked if neighbor.similarity.score > 0.0]
    ranked.sort(key=lambda item: (-item.similarity.score, -item.case.target_date.toordinal()))
    neighbors = ranked[: config.top_k]
    if len(neighbors) < config.min_neighbors:
        return _insufficient(
            base,
            "not_enough_historical_neighbors",
            anchors=anchor_payload,
            available_neighbors=len(neighbors),
        )

    direct_overlap = anchor_a.tails & anchor_b.tails
    candidates = set(direct_overlap)
    for neighbor in neighbors:
        candidates.update(neighbor.case.label_all)

    prior = sum(len(case.label_all) / 100.0 for case in cases) / len(cases)
    total_weight = sum(neighbor.similarity.score for neighbor in neighbors)
    squared_weight = sum(neighbor.similarity.score ** 2 for neighbor in neighbors)
    effective_neighbors = total_weight ** 2 / squared_weight if squared_weight else 0.0

    scores: dict[int, float] = {}
    evidence: dict[int, dict] = {}
    for number in sorted(candidates):
        support_a = sum(
            neighbor.similarity.score for neighbor in neighbors if number in neighbor.case.label_a
        )
        support_b = sum(
            neighbor.similarity.score for neighbor in neighbors if number in neighbor.case.label_b
        )
        support_merged = sum(
            neighbor.similarity.score for neighbor in neighbors if number in neighbor.case.label_all
        )
        score = (
            config.shrinkage_alpha * prior + support_merged
        ) / (config.shrinkage_alpha + total_weight)
        scores[number] = score

        nearest_cases = []
        for neighbor in neighbors[: config.evidence_cases]:
            top_blocks = sorted(
                neighbor.similarity.prize_scores.items(),
                key=lambda item: (-item[1], item[0]),
            )[:3]
            nearest_cases.append(
                {
                    "target_date": neighbor.case.target_date.isoformat(),
                    "anchor_dates": [
                        neighbor.case.anchor_a.draw_date.isoformat(),
                        neighbor.case.anchor_b.draw_date.isoformat(),
                    ],
                    "similarity": neighbor.similarity.score,
                    "hit_a": number in neighbor.case.label_a,
                    "hit_b": number in neighbor.case.label_b,
                    "top_relation_blocks": [code for code, _ in top_blocks],
                }
            )
        evidence[number] = {
            "number": f"{number:02d}",
            "estimated_hit_likelihood_uncalibrated": score,
            "is_direct_overlap": number in direct_overlap,
            "weighted_support_merged": support_merged,
            "weighted_support_a": support_a,
            "weighted_support_b": support_b,
            "effective_neighbor_count": effective_neighbors,
            "nearest_cases": nearest_cases,
        }

    selected = select_top_three(scores, direct_overlap)
    if selected is None:
        return _insufficient(
            base,
            "not_enough_candidates_under_overlap_constraint",
            anchors=anchor_payload,
            direct_overlap=[f"{number:02d}" for number in sorted(direct_overlap)],
            candidate_count=len(candidates),
        )

    selected_evidence = []
    for rank, number in enumerate(selected, start=1):
        selected_evidence.append({"rank": rank, **evidence[number]})

    return {
        **base,
        "status": "success",
        "anchors": anchor_payload,
        "anchor_ages": list(current.anchor_ages),
        "historical_case_count": len(cases),
        "neighbor_count": len(neighbors),
        "neighbor_weight_sum": total_weight,
        "prior_merged_prevalence": prior,
        "direct_overlap": [f"{number:02d}" for number in sorted(direct_overlap)],
        "candidate_count": len(candidates),
        "top_3": [f"{number:02d}" for number in selected],
        "selected_evidence": selected_evidence,
    }
