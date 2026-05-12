"""
prediction_repo.py — Shared prediction save/upsert logic.

Extracted from prediction scripts to eliminate code duplication.
duplicated _save_prediction implementations (DRY principle).

All prediction scripts should use this single repository for DB writes.
"""

from typing import Optional
from src.database.supabase_client import LotteryDB


RUNTIME_ONLY_FIELDS = ("scoring_log",)
ENSEMBLE_AUDIT_FIELDS = ("ensemble_method", "contributing_models", "final_scores")


def _strip_fields(record: dict, fields: tuple[str, ...]) -> dict:
    """Return a copy without fields that should not be sent to Supabase."""
    cleaned = record.copy()
    for field in fields:
        cleaned.pop(field, None)
    return cleaned


def _is_missing_ensemble_metadata_column(error: Exception) -> bool:
    """Detect old production schemas missing migration 06 ensemble columns."""
    error_str = str(error).lower()
    return (
        "prediction_results" in error_str
        and (
            "schema cache" in error_str
            or "could not find" in error_str
            or "pgrst204" in error_str
            or "column" in error_str
        )
        and any(field in error_str for field in ENSEMBLE_AUDIT_FIELDS)
    )


def _write_prediction_record(db: LotteryDB, record: dict, existing: list) -> None:
    """Execute the insert/update for prediction_results."""
    if existing:
        db.supabase.table("prediction_results").update(record) \
            .eq("id", existing[0]["id"]).execute()
    else:
        db.supabase.table("prediction_results").insert(record).execute()


def save_prediction(db: LotteryDB, result: dict) -> None:
    """Save hoặc update prediction_results, xử lý NULL province đúng cách.

    Dùng check-then-update/insert thay vì upsert on_conflict vì
    Supabase không hỗ trợ COALESCE trong on_conflict parameter.

    Province convention:
      - XSMB: province = NULL (single station, no province needed)
      - XSMN per-province: province = slug (e.g. 'tp-hcm')
      - XSMN global ensemble: province = 'all' (aggregated across provinces)

    Args:
        db: LotteryDB instance
        result: dict with prediction data ready for DB insert
    """
    region    = result["region"]
    province  = result.get("province")
    pred_date = result["prediction_date"]

    # Strip runtime-only fields before save. Ensemble audit fields are real DB
    # columns added by migration 06 and should be persisted when available.
    db_record = _strip_fields(result, RUNTIME_ONLY_FIELDS)

    # Check existing
    q = db.supabase.table("prediction_results").select("id") \
        .eq("prediction_date", pred_date).eq("region", region)
    q = q.is_("province", "null") if province is None else q.eq("province", province)
    existing = q.execute().data

    try:
        _write_prediction_record(db, db_record, existing)
    except Exception as e:
        if not _is_missing_ensemble_metadata_column(e):
            raise

        fallback_record = _strip_fields(db_record, ENSEMBLE_AUDIT_FIELDS)
        print(
            "  ⚠️  prediction_results missing ensemble metadata columns "
            "(run migration 06). Retrying without audit fields."
        )
        _write_prediction_record(db, fallback_record, existing)

    if existing:
        print(f"  ↩️  Updated prediction: {region}/{province or 'all'}")
    else:
        print(f"  ✅ Inserted prediction: {region}/{province or 'all'}")


def save_model_prediction(db: LotteryDB, log: dict) -> None:
    """Save model_predictions log (upsert).

    Logs individual sub-model outputs for the model_predictions table.
    Includes error handling for missing table (PGRST205).

    Args:
        db: LotteryDB instance
        log: dict with model prediction log data
    """
    pred_date  = log["prediction_date"]
    region     = log["region"]
    province   = log.get("province")
    model_name = log["model_name"]

    try:
        q = db.supabase.table("model_predictions").select("id") \
            .eq("prediction_date", pred_date) \
            .eq("region", region) \
            .eq("model_name", model_name)
        q = q.is_("province", "null") if province is None else q.eq("province", province)
        existing = q.execute().data

        if existing:
            db.supabase.table("model_predictions").update(log) \
                .eq("id", existing[0]["id"]).execute()
        else:
            db.supabase.table("model_predictions").insert(log).execute()
    except Exception as e:
        error_str = str(e)
        if "PGRST205" in error_str or "model_predictions" in error_str:
            print(f"  ⚠️  model_predictions table missing (run migration 06). Error: {e}")
        else:
            raise
