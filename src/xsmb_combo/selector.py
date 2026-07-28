"""Exhaustive XSMB triple selection aligned with the ``>=2/3`` objective."""

from __future__ import annotations

import itertools
import math
from typing import Mapping, Sequence

from src.xsmb_combo.domain import (
    AdapterResult,
    ComboSelectorResult,
    JointPairEvidence,
    PAIR_COUNT,
    SelectorStatus,
)
from src.xsmb_combo.joint_probability import (
    InsufficientHistoryError,
    JointProbabilityEstimator,
)
from src.xsmb_combo.metrics import combo_probability_from_joint


SUPPORTED_OBJECTIVES = {"combo_probability", "expected_circles"}


def _fuse_relative_evidence(
    adapted: AdapterResult,
    weights: Mapping[str, float] | None,
) -> tuple[
    list[float],
    tuple[str, ...],
    tuple[tuple[str, float], ...],
]:
    fused = [0.0] * PAIR_COUNT
    active_vectors = [
        vector
        for vector in adapted.vectors
        if sum(vector.scores) > 1e-12
    ]
    if not active_vectors:
        return fused, (), ()

    raw_weights: dict[str, float] = {}
    for vector in active_vectors:
        weight = float((weights or {}).get(vector.model_name, 1.0))
        if not math.isfinite(weight):
            weight = 0.0
        raw_weights[vector.model_name] = max(weight, 0.0)
    weight_sum = sum(raw_weights.values())
    if weight_sum <= 1e-12:
        raw_weights = {vector.model_name: 1.0 for vector in active_vectors}
        weight_sum = float(len(active_vectors))

    for vector in active_vectors:
        model_total = sum(vector.scores)
        model_weight = raw_weights[vector.model_name] / weight_sum
        for pair, score in enumerate(vector.scores):
            if score > 0.0:
                fused[pair] += model_weight * (score / model_total)

    normalized_weights = tuple(
        (
            vector.model_name,
            raw_weights[vector.model_name] / weight_sum,
        )
        for vector in active_vectors
    )
    return (
        fused,
        tuple(vector.model_name for vector in active_vectors),
        normalized_weights,
    )


def select_combo(
    adapted: AdapterResult,
    historical_tail_sets: Sequence[frozenset[int]],
    *,
    weights: Mapping[str, float] | None = None,
    candidate_pool_size: int = 100,
    objective: str = "combo_probability",
    minimum_history: int = 30,
    prior_strength: float = 12.0,
) -> ComboSelectorResult:
    """Select the best triple from all available full-vector candidates.

    Full 100-pair vectors produce the complete ``C(100, 3)`` search. Legacy
    Top-N results remain supported and naturally restrict coverage to their
    union. Fusion evidence breaks exact joint-score ties; it is not a
    calibrated probability.
    """
    if objective not in SUPPORTED_OBJECTIVES:
        raise ValueError(f"unsupported objective: {objective}")
    if candidate_pool_size < 3:
        raise ValueError("candidate_pool_size must be at least 3")

    fused, contributing, active_weights = _fuse_relative_evidence(
        adapted,
        weights,
    )
    ranked = sorted(range(PAIR_COUNT), key=lambda pair: (-fused[pair], pair))
    covered_pairs = {
        pair
        for vector in adapted.vectors
        if vector.model_name in contributing
        for pair in vector.source_pairs
    }
    candidate_pool = tuple(
        pair for pair in ranked if pair in covered_pairs
    )[:candidate_pool_size]
    source_families = tuple(
        (vector.model_name, vector.source_family)
        for vector in adapted.vectors
        if vector.model_name in contributing
    )

    base_kwargs = {
        "objective": objective,
        "candidate_pool": candidate_pool,
        "contributing_models": contributing,
        "skipped_models": adapted.skipped_models,
        "diagnostics": adapted.warnings,
        "active_weights": active_weights,
        "source_families": source_families,
    }
    if len(candidate_pool) < 3:
        return ComboSelectorResult(
            status=SelectorStatus.INSUFFICIENT_CANDIDATES,
            diagnostics=adapted.warnings + (
                f"need at least 3 candidates, received {len(candidate_pool)}",
            ),
            **{key: value for key, value in base_kwargs.items() if key != "diagnostics"},
        )

    try:
        estimator = JointProbabilityEstimator(
            historical_tail_sets,
            minimum_history=minimum_history,
            prior_strength=prior_strength,
        )
    except InsufficientHistoryError as exc:
        return ComboSelectorResult(
            status=SelectorStatus.INSUFFICIENT_HISTORY,
            diagnostics=adapted.warnings + (str(exc),),
            **{key: value for key, value in base_kwargs.items() if key != "diagnostics"},
        )

    best_key: tuple[float, float, tuple[int, int, int]] | None = None
    best_payload: tuple[
        tuple[int, int, int],
        float,
        float,
        tuple[JointPairEvidence, ...],
        float,
    ] | None = None
    evaluated = 0

    for triple in itertools.combinations(candidate_pool, 3):
        evaluated += 1
        pair_edges = tuple(itertools.combinations(triple, 2))
        pair_probabilities = tuple(
            estimator.pair_probability(pair_a, pair_b)
            for pair_a, pair_b in pair_edges
        )
        triple_probability = estimator.triple_probability(*triple)
        combo_probability = combo_probability_from_joint(
            pair_probabilities, triple_probability
        )
        expected_circles = sum(pair_probabilities)
        objective_score = (
            combo_probability
            if objective == "combo_probability"
            else expected_circles
        )
        fusion_tiebreak = sum(fused[pair] for pair in triple)
        key = (objective_score, fusion_tiebreak, tuple(-pair for pair in triple))
        if best_key is None or key > best_key:
            best_key = key
            best_payload = (
                triple,
                combo_probability,
                expected_circles,
                tuple(
                    JointPairEvidence(pair_a, pair_b, probability)
                    for (pair_a, pair_b), probability in zip(
                        pair_edges, pair_probabilities
                    )
                ),
                min(triple_probability, min(pair_probabilities)),
            )

    if best_payload is None:
        return ComboSelectorResult(
            status=SelectorStatus.INSUFFICIENT_CANDIDATES,
            diagnostics=adapted.warnings + ("no triples were evaluated",),
            **{key: value for key, value in base_kwargs.items() if key != "diagnostics"},
        )

    selected, combo_probability, expected_circles, pair_evidence, triple_prob = (
        best_payload
    )
    ranked_selection = tuple(
        sorted(selected, key=lambda pair: (-fused[pair], pair))
    )
    objective_score = (
        combo_probability
        if objective == "combo_probability"
        else expected_circles
    )
    return ComboSelectorResult(
        status=SelectorStatus.SUCCESS,
        top_pairs=ranked_selection,
        objective=objective,
        objective_score=round(objective_score, 8),
        combo_probability=round(combo_probability, 8),
        expected_winning_circles=round(expected_circles, 8),
        candidate_pool=candidate_pool,
        contributing_models=contributing,
        skipped_models=adapted.skipped_models,
        evaluated_triples=evaluated,
        joint_pair_evidence=pair_evidence,
        triple_probability=round(triple_prob, 8),
        diagnostics=adapted.warnings,
        active_weights=active_weights,
        source_families=source_families,
    )
