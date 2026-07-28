"""Compatibility adapters for existing XSMB model result dictionaries."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from src.xsmb_combo.domain import AdapterResult, PAIR_COUNT, PairScoreVector


def adapt_legacy_model_results(
    model_results: Iterable[Any],
) -> AdapterResult:
    """Convert legacy ``top_pairs`` outputs into 100-pair evidence vectors.

    Invalid models are skipped rather than raised so the additive shadow path
    remains at least as fault tolerant as the existing ensemble.
    """
    vectors: list[PairScoreVector] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for index, result in enumerate(model_results):
        if not isinstance(result, Mapping):
            model_name = f"unknown_{index}"
            skipped.append(model_name)
            warnings.append(
                f"{model_name}: model result is not a mapping"
            )
            continue

        model_name = str(result.get("model_name") or f"unknown_{index}")
        if result.get("status") != "success":
            skipped.append(model_name)
            continue

        raw_vector = result.get("score_vector")
        vector_values: list[float] | None = None
        if isinstance(raw_vector, Mapping):
            try:
                if {int(key) for key in raw_vector} == set(range(PAIR_COUNT)):
                    vector_values = [
                        float(
                            raw_vector[pair]
                            if pair in raw_vector
                            else raw_vector[str(pair)]
                        )
                        for pair in range(PAIR_COUNT)
                    ]
            except (KeyError, TypeError, ValueError):
                vector_values = None
        elif isinstance(raw_vector, (list, tuple)) and len(raw_vector) == PAIR_COUNT:
            try:
                vector_values = [float(value) for value in raw_vector]
            except (TypeError, ValueError):
                vector_values = None
        if (
            vector_values is None
            or not all(
                math.isfinite(value) and value >= 0.0
                for value in vector_values
            )
        ):
            vector_values = None
            if raw_vector is not None:
                warnings.append(
                    f"{model_name}: invalid score_vector; used legacy top_pairs"
                )
        elif sum(vector_values) <= 1e-12:
            vector_values = None
            warnings.append(
                f"{model_name}: zero-mass score_vector; used legacy top_pairs"
            )

        sanitized: dict[int, float] = {}
        ordered_pairs: list[int] = []
        invalid_entries = 0
        raw_top_pairs = result.get("top_pairs")
        if isinstance(raw_top_pairs, (list, tuple)):
            for entry in raw_top_pairs:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    invalid_entries += 1
                    continue
                try:
                    pair = int(entry[0])
                    score = abs(float(entry[1]))
                except (TypeError, ValueError):
                    invalid_entries += 1
                    continue
                if pair < 0 or pair >= PAIR_COUNT or not math.isfinite(score):
                    invalid_entries += 1
                    continue
                if pair not in sanitized:
                    ordered_pairs.append(pair)
                    sanitized[pair] = score
                else:
                    sanitized[pair] = max(sanitized[pair], score)
        else:
            warnings.append(f"{model_name}: top_pairs is not a sequence")

        if not sanitized and vector_values is None:
            skipped.append(model_name)
            warnings.append(f"{model_name}: no valid pair/score entries")
            continue

        if sanitized and sum(sanitized.values()) <= 1e-12:
            count = len(ordered_pairs)
            sanitized = {
                pair: float(count - rank)
                for rank, pair in enumerate(ordered_pairs)
            }

        source_family = str(result.get("source_family") or model_name)
        score_kind = str(
            result.get("score_semantics")
            or "relative_evidence_uncalibrated"
        )
        coverage_kind = "legacy_top_n"
        scores = [0.0] * PAIR_COUNT
        if vector_values is not None:
            scores = vector_values
            ordered_pairs = list(range(PAIR_COUNT))
            coverage_kind = "full_100"
        else:
            for pair, score in sanitized.items():
                scores[pair] = score

        vectors.append(
            PairScoreVector(
                model_name=model_name,
                scores=tuple(scores),
                source_pairs=tuple(ordered_pairs),
                source_family=source_family,
                score_kind=score_kind,
                coverage_kind=coverage_kind,
            )
        )
        if invalid_entries:
            warnings.append(
                f"{model_name}: ignored {invalid_entries} invalid top_pairs entries"
            )

    return AdapterResult(
        vectors=tuple(vectors),
        skipped_models=tuple(skipped),
        warnings=tuple(warnings),
    )
