"""Default-off integration helpers for the additive XSMB combo selector."""

from __future__ import annotations

import os
from datetime import date
from typing import Callable, Mapping, Sequence

from src.xsmb_combo.adapters import adapt_legacy_model_results
from src.xsmb_combo.domain import ComboSelectorResult
from src.xsmb_combo.selector import select_combo
from src.xsmb_ensemble.data_utils import _load_tails_by_draws


MODE_ENV_VAR = "XSMB_COMBO_SELECTOR_MODE"
VALID_MODES = {"off", "shadow"}


def get_combo_selector_mode(value: str | None = None) -> str:
    """Resolve selector mode; unknown values fail closed to ``off``."""
    raw_value = value if value is not None else os.getenv(MODE_ENV_VAR, "off")
    normalized = str(raw_value).strip().lower()
    return normalized if normalized in VALID_MODES else "off"


def _history_before_target(
    history_frame,
    target_date: date,
) -> Sequence[frozenset[int]]:
    """Validate the data cutoff and return draw-level unique tail sets."""
    if history_frame.empty:
        return ()
    for draw_date in history_frame["draw_date"]:
        resolved = draw_date.date() if hasattr(draw_date, "date") else date.fromisoformat(str(draw_date))
        if resolved >= target_date:
            raise ValueError(
                f"history leakage: draw {resolved} is not before target {target_date}"
            )
    return tuple(frozenset(int(pair) for pair in tails) for tails in history_frame["tail_set"])


def run_xsmb_combo_shadow(
    db,
    model_results: Sequence[Mapping],
    target_date: date,
    *,
    weights: Mapping[str, float] | None = None,
    history_draws: int = 180,
    candidate_pool_size: int = 10,
    objective: str = "combo_probability",
    minimum_history: int = 30,
) -> ComboSelectorResult:
    """Load pre-target XSMB history and run the combo selector without writes."""
    history = _load_tails_by_draws(
        db,
        region="XSMB",
        province=None,
        n_draws=history_draws,
        before_date=target_date,
    )
    historical_tail_sets = _history_before_target(history, target_date)
    adapted = adapt_legacy_model_results(model_results)
    return select_combo(
        adapted,
        historical_tail_sets,
        weights=weights,
        candidate_pool_size=candidate_pool_size,
        objective=objective,
        minimum_history=minimum_history,
    )


def maybe_run_xsmb_combo_shadow(
    db,
    model_results: Sequence[Mapping],
    target_date: date,
    *,
    weights: Mapping[str, float] | None = None,
    mode: str | None = None,
    logger: Callable[[str], None] = print,
) -> ComboSelectorResult | None:
    """Run shadow mode safely; failures never interrupt the legacy pipeline."""
    if get_combo_selector_mode(mode) != "shadow":
        return None
    try:
        result = run_xsmb_combo_shadow(
            db,
            model_results,
            target_date,
            weights=weights,
        )
        if result.top_pairs:
            pairs = ", ".join(f"{pair:02d}" for pair in result.top_pairs)
            logger(
                "  🧪 XSMB combo shadow: "
                f"[{pairs}] | objective={result.objective_score:.4f} "
                f"| circles={result.expected_winning_circles:.4f}"
            )
        else:
            logger(f"  🧪 XSMB combo shadow: {result.status.value}")
        return result
    except Exception as exc:
        logger(f"  ⚠️  XSMB combo shadow failed; legacy output preserved: {exc}")
        return None
