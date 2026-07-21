"""Application service for running CMR without production persistence."""

from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from .domain import CMRConfig
from .predictor import predict_coupled
from .repository import load_tail_history


def generate_shadow_prediction(
    db: Any,
    provinces: Sequence[str],
    target_date: date,
    config: Optional[CMRConfig] = None,
) -> dict:
    """Read pre-target history and return an audit-ready CMR shadow result."""
    if len(provinces) != 2 or len(set(provinces)) != 2:
        raise ValueError("CMR requires exactly two distinct provinces")
    province_pair = (str(provinces[0]), str(provinces[1]))
    rows = load_tail_history(db, province_pair, target_date)
    return predict_coupled(rows, province_pair, target_date, config)
