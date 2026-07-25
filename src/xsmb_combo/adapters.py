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

        raw_top_pairs = result.get("top_pairs")
        if not isinstance(raw_top_pairs, (list, tuple)):
            skipped.append(model_name)
            warnings.append(f"{model_name}: top_pairs is not a sequence")
            continue

        sanitized: dict[int, float] = {}
        ordered_pairs: list[int] = []
        invalid_entries = 0
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

        if not sanitized:
            skipped.append(model_name)
            warnings.append(f"{model_name}: no valid pair/score entries")
            continue

        if sum(sanitized.values()) <= 1e-12:
            count = len(ordered_pairs)
            sanitized = {
                pair: float(count - rank)
                for rank, pair in enumerate(ordered_pairs)
            }

        scores = [0.0] * PAIR_COUNT
        for pair, score in sanitized.items():
            scores[pair] = score

        vectors.append(
            PairScoreVector(
                model_name=model_name,
                scores=tuple(scores),
                source_pairs=tuple(ordered_pairs),
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
