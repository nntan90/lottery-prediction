"""
prediction_repo.py — Shared prediction save/upsert logic.

Extracted from predict_v3.py and predict_xsmn_ensemble.py to eliminate
duplicated _save_prediction implementations (DRY principle).

All prediction scripts should use this single repository for DB writes.
"""

from typing import Optional
from src.database.supabase_client import LotteryDB


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

    # Strip non-DB fields before save
    db_record = result.copy()
    NON_DB_FIELDS = [
        "ensemble_method", "contributing_models", "final_scores", "scoring_log",
    ]
    for field in NON_DB_FIELDS:
        db_record.pop(field, None)

    # Check existing
    q = db.supabase.table("prediction_results").select("id") \
        .eq("prediction_date", pred_date).eq("region", region)
    q = q.is_("province", "null") if province is None else q.eq("province", province)
    existing = q.execute().data

    if existing:
        db.supabase.table("prediction_results").update(db_record) \
            .eq("id", existing[0]["id"]).execute()
        print(f"  ↩️  Updated prediction: {region}/{province or 'all'}")
    else:
        db.supabase.table("prediction_results").insert(db_record).execute()
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
