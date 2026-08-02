"""Application boundary for the daily relationship shadow run."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from .domain import RelationshipConfig, validate_provinces
from .predictor import predict_relationship
from .repository import load_matched_history


def generate_relationship_shadow(
    db: Any,
    model_results: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date,
    config: Optional[RelationshipConfig] = None,
    *,
    family_weights: Optional[Mapping[str, float]] = None,
) -> dict:
    """Read matched pre-target history and run the pure relationship scorer."""
    province_scope = validate_provinces(provinces)
    config = config or RelationshipConfig()
    if not config.require_distinct_unit_digits:
        raise ValueError(
            "daily relationship shadow requires distinct unit digits"
        )
    history = load_matched_history(
        db,
        province_scope,
        target_date,
        limit=config.history_lookback_occurrences,
    )
    return predict_relationship(
        model_results,
        history,
        province_scope,
        target_date,
        config,
        family_weights=family_weights,
    )
