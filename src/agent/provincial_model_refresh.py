"""Refresh and audit the four state-less XSMN provincial model families."""

from datetime import date, timedelta
from typing import Any, Callable

from src.xsmn_ensemble.data_utils import _load_tails_by_draws
from src.xsmn_ensemble.model_cdm import predict_cdm
from src.xsmn_ensemble.model_frequency import predict_frequency
from src.xsmn_ensemble.model_gap import predict_gap
from src.xsmn_ensemble.model_markov import predict_markov


RULE_FAMILIES: dict[str, Callable[..., dict[str, Any]]] = {
    "frequency": predict_frequency,
    "gap": predict_gap,
    "markov": predict_markov,
    "cdm": predict_cdm,
}
EXPECTED_MODEL_NAMES = {
    "frequency": "frequency",
    "gap": "gap_overdue",
    "markov": "markov",
    "cdm": "cdm",
}


def _iso_draw_date(value: Any) -> str:
    """Normalize pandas/date/string draw values to an ISO date."""
    if hasattr(value, "date"):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def refresh_rule_families(
    db: Any,
    province: str,
    weekday: int,
    target_date: date,
    *,
    n_draws: int = 250,
    families: dict[str, Callable[..., dict[str, Any]]] | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Recompute rule-family state after one completed provincial draw.

    The scorers normally exclude ``target_date`` because it is the prediction
    date. Refresh therefore evaluates the next occurrence (target + 7 days),
    which preserves the weekday and makes the just-finished draw the newest
    history row without changing any scoring algorithm.
    """
    selected = RULE_FAMILIES if families is None else families
    cutoff = target_date + timedelta(days=1)
    try:
        history = _load_tails_by_draws(
            db,
            "XSMN",
            province,
            n_draws,
            before_date=cutoff,
            target_weekday=weekday,
        )
    except Exception as exc:
        return {
            family: {
                "status": "failed",
                "latest_history_date": None,
                "n_draws_used": 0,
                "error": f"History load failed: {exc}",
            }
            for family in selected
        }
    latest = _iso_draw_date(history.iloc[-1]["draw_date"]) if not history.empty else None
    draw_count = len(history)

    if latest != target_date.isoformat():
        message = (
            f"History cutoff mismatch: latest={latest or 'none'}, "
            f"expected={target_date.isoformat()}"
        )
        return {
            family: {
                "status": "failed",
                "latest_history_date": latest,
                "n_draws_used": draw_count,
                "error": message,
            }
            for family in selected
        }

    refresh_date = target_date + timedelta(days=7)
    updates: dict[str, dict[str, Any]] = {}
    for family, predictor in selected.items():
        try:
            result = predictor(
                db=db,
                province=province,
                target_date=refresh_date,
                n_draws=n_draws,
                top_n=5,
                region="XSMN",
            )
            result_draws = int(result.get("n_draws_used") or 0)
            top_pairs = result.get("top_pairs")
            valid_result = (
                result.get("status") == "success"
                and result.get("model_name") == EXPECTED_MODEL_NAMES[family]
                and isinstance(top_pairs, list)
                and bool(top_pairs)
                and result_draws > 0
            )
            status = "refreshed" if valid_result else "failed"
            validation_error = None if valid_result else (
                result.get("error_message")
                or (
                    "Invalid refresh result: "
                    f"model_name={result.get('model_name')}, "
                    f"top_pairs={len(top_pairs) if isinstance(top_pairs, list) else 'invalid'}, "
                    f"n_draws_used={result_draws}"
                )
            )
            updates[family] = {
                "status": status,
                "latest_history_date": latest,
                "n_draws_used": result_draws,
                "error": validation_error,
            }
        except Exception as exc:  # one rule family must not block the others
            updates[family] = {
                "status": "failed",
                "latest_history_date": latest,
                "n_draws_used": draw_count,
                "error": str(exc),
            }
    return updates
