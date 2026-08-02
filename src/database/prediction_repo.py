"""
prediction_repo.py — Shared prediction save/upsert logic.

Extracted from prediction scripts to eliminate code duplication.
duplicated _save_prediction implementations (DRY principle).

All prediction scripts should use this single repository for DB writes.
"""

from datetime import date
import os
import re
from typing import Any, Optional, TYPE_CHECKING
from src.database.supabase_client import LotteryDB

if TYPE_CHECKING:
    from src.xsmb_combo.domain import ComboSelectorResult


RUNTIME_ONLY_FIELDS = ("scoring_log", "candidate_log")
ENSEMBLE_AUDIT_FIELDS = (
    "ensemble_method", "contributing_models", "final_scores", "run_metadata",
)
SHADOW_MODEL_NAMES = frozenset({
    "cmr_shadow",
    "ddt_shadow",
    "relationship",
    "xsmb_combo_shadow",
})
SHADOW_SUCCESS_STATUSES = frozenset({"success", "uncalibrated"})
SHADOW_TRACKING_FIELDS = (
    "prediction_mode",
    "model_version",
    "score_semantics",
    "run_metadata",
    "hit_count",
    "combo_hit",
    "verified_at",
)


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

        missing_fields = (
            ("run_metadata",)
            if "run_metadata" in str(e).lower()
            else ENSEMBLE_AUDIT_FIELDS
        )
        fallback_record = _strip_fields(db_record, missing_fields)
        print(
            "  ⚠️  prediction_results missing ensemble metadata columns. "
            "Apply pending migrations; retrying with compatible fields."
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


def _safe_reason(value: object) -> Optional[str]:
    """Return a compact redacted persistence-safe reason."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    text = re.sub(r"https?://\S+", "[redacted-url]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(bearer|token|authorization|apikey|api_key)\s*[:=]?\s*\S+",
        r"\1=[redacted]",
        text,
    )
    for key in (
        "SUPABASE_SERVICE_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SUPABASE_DB_URL",
        "SUPABASE_DB_PASSWORD",
    ):
        secret = os.getenv(key, "")
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:240] or None


def sanitize_prediction_reason(value: object) -> Optional[str]:
    """Expose the shared credential-redacting reason sanitizer to orchestrators."""
    return _safe_reason(value)


def _canonical_shadow_status(status: str) -> str:
    """Fit producer statuses into ``model_predictions.status VARCHAR(20)``."""
    if len(status) <= 20:
        return status
    if status.startswith("insufficient_"):
        return "insufficient"
    return "error"


def _shadow_pairs_and_scores(payload: dict, model_name: str) -> tuple[list[int], list[float]]:
    """Extract a valid unique Top 3 from either canonical shadow payload."""
    pairs: list[int] = []
    scores: list[float] = []
    evidence = payload.get("selected_evidence") or []
    pair_key = "number" if model_name == "cmr_shadow" else "pair"
    if model_name == "cmr_shadow":
        score_keys = ("estimated_hit_likelihood_uncalibrated",)
    elif model_name == "relationship":
        score_keys = ("ranking_score_uncalibrated", "score")
    else:
        score_keys = ("probability", "estimated_likelihood_uncalibrated")
    for item in evidence:
        try:
            pair = int(item[pair_key])
            score = next(float(item[key]) for key in score_keys if item.get(key) is not None)
        except (KeyError, TypeError, ValueError, StopIteration):
            continue
        if 0 <= pair <= 99 and pair not in pairs:
            pairs.append(pair)
            scores.append(score)
        if len(pairs) == 3:
            break
    return pairs, scores


def normalize_shadow_prediction(
    payload: dict,
    *,
    model_name: str,
    target_date: date | str,
    provinces: list[str] | tuple[str, ...],
    execution_source: str,
    runtime_ms: Optional[int] = None,
    config_metadata: Optional[dict] = None,
) -> dict:
    """Normalize an XSMN shadow output to the model_predictions contract."""
    if model_name not in SHADOW_MODEL_NAMES:
        raise ValueError(f"unsupported shadow model: {model_name}")
    producer_status = str(payload.get("status") or "error")
    status = producer_status
    pairs, scores = _shadow_pairs_and_scores(payload, model_name)
    if status in SHADOW_SUCCESS_STATUSES and len(pairs) != 3:
        status, pairs, scores = "error", [], []
        reason = "invalid_shadow_top_3"
    elif (
        model_name == "relationship"
        and status in SHADOW_SUCCESS_STATUSES
        and len({pair % 10 for pair in pairs}) != 3
    ):
        status, pairs, scores = "error", [], []
        reason = "invalid_relationship_unit_digits"
    else:
        reason = payload.get("reason")
    status = _canonical_shadow_status(status)
    semantics = payload.get("score_semantics") or (
        "estimated_hit_likelihood_uncalibrated"
    )
    slots: dict[str, Any] = {}
    for index in range(5):
        slots[f"pair_{index + 1}"] = pairs[index] if index < len(pairs) else None
        slots[f"score_{index + 1}"] = scores[index] if index < len(scores) else None
    payload_metadata = payload.get("run_metadata")
    run_metadata = (
        dict(payload_metadata) if isinstance(payload_metadata, dict) else {}
    )
    existing_config = run_metadata.get("config")
    run_metadata.update({
        "provinces": list(provinces),
        "data_cutoff": payload.get("data_cutoff"),
        "execution_source": execution_source,
        "runtime_ms": runtime_ms,
        "producer_status": producer_status,
        "config": (
            config_metadata
            if config_metadata is not None
            else existing_config if isinstance(existing_config, dict)
            else payload.get("config") if isinstance(payload.get("config"), dict)
            else {}
        ),
    })
    return {
        "prediction_date": (
            target_date.isoformat() if isinstance(target_date, date) else str(target_date)
        ),
        "region": "XSMN",
        "province": "all",
        "model_name": model_name,
        "model_type": "shadow",
        **slots,
        "execution_time_ms": runtime_ms,
        "error_message": _safe_reason(reason),
        "status": status,
        "prediction_mode": "shadow",
        "model_version": str(
            payload.get("model_version")
            or payload.get("model_name")
            or model_name
        ),
        "score_semantics": str(semantics),
        "run_metadata": run_metadata,
        "hit": None,
        "matched_pairs": None,
        "hit_count": None,
        "combo_hit": None,
        "verified_at": None,
    }


def get_shadow_prediction(
    db: LotteryDB,
    model_name: str,
    target_date: date | str,
    *,
    region: str = "XSMN",
    province: Optional[str] = "all",
) -> Optional[dict]:
    """Read one canonical shadow row while preserving XSMN/all defaults."""
    date_value = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
    try:
        rows = db.supabase.table("model_predictions").select("*") \
            .eq("prediction_date", date_value).eq("region", region)
        rows = (
            rows.is_("province", "null")
            if province is None
            else rows.eq("province", province)
        )
        rows = rows.eq("model_name", model_name).execute().data
        return rows[0] if rows else None
    except Exception as exc:
        error = str(exc).upper()
        if "PGRST205" in error or "42P01" in error:
            return None
        raise


def _same_shadow_prediction(existing: dict, incoming: dict) -> bool:
    """Return whether a retry keeps the canonical Top 3 and province scope."""
    existing_pairs = {
        existing.get(f"pair_{index}") for index in range(1, 4)
    }
    incoming_pairs = {
        incoming.get(f"pair_{index}") for index in range(1, 4)
    }
    if existing_pairs != incoming_pairs:
        return False
    old_metadata = existing.get("run_metadata")
    new_metadata = incoming.get("run_metadata")
    old_scope = old_metadata.get("provinces") if isinstance(old_metadata, dict) else None
    new_scope = new_metadata.get("provinces") if isinstance(new_metadata, dict) else None
    return not old_scope or not new_scope or list(old_scope) == list(new_scope)


def _prepare_shadow_retry(existing: Optional[dict], incoming: dict) -> dict:
    """Preserve verification only when a successful retry predicts the same set."""
    prepared = incoming.copy()
    if (
        existing
        and incoming.get("status") in SHADOW_SUCCESS_STATUSES
        and _same_shadow_prediction(existing, incoming)
    ):
        for field in (
            "hit",
            "matched_pairs",
            "hit_count",
            "combo_hit",
            "verified_at",
        ):
            if field in existing:
                prepared[field] = existing.get(field)
    return prepared


def save_shadow_prediction(db: LotteryDB, record: dict) -> bool:
    """Persist a shadow row idempotently; a failure never downgrades success."""
    region = str(record.get("region") or "XSMN")
    province = record.get("province", "all")
    existing = get_shadow_prediction(
        db,
        str(record["model_name"]),
        str(record["prediction_date"]),
        region=region,
        province=province,
    )
    if (
        existing
        and existing.get("status") in SHADOW_SUCCESS_STATUSES
        and record.get("status") not in SHADOW_SUCCESS_STATUSES
    ):
        return False
    if (
        existing
        and existing.get("verified_at")
        and not _same_shadow_prediction(existing, record)
    ):
        # A verified ledger row is immutable.  A regenerated Top 3 belongs to
        # a new model version/date, never an overwrite of settled evidence.
        return False

    def write(value: dict, current: Optional[dict]) -> bool:
        if current:
            update_value = value.copy()
            same_prediction = _same_shadow_prediction(current, value)
            if current.get("verified_at") and not same_prediction:
                return False
            if (
                value.get("status") in SHADOW_SUCCESS_STATUSES
                and same_prediction
            ):
                # A verification may complete after the retry read. Omitting
                # lifecycle fields prevents the retry from clearing it.
                for field in (
                    "hit",
                    "matched_pairs",
                    "hit_count",
                    "combo_hit",
                    "verified_at",
                ):
                    update_value.pop(field, None)
            query = db.supabase.table("model_predictions").update(update_value) \
                .eq("id", current["id"])
            if not same_prediction:
                # Close the read/update race with verification.  Once
                # verified_at is set, a retry with a different Top 3 cannot
                # replace the settled ledger row.
                query = query.is_("verified_at", "null")
            if value.get("status") not in SHADOW_SUCCESS_STATUSES:
                query = query.neq("status", "success")
            response = query.execute()
            if not same_prediction and not (getattr(response, "data", None) or []):
                latest = get_shadow_prediction(
                    db,
                    str(value["model_name"]),
                    str(value["prediction_date"]),
                    region=str(value.get("region") or region),
                    province=value.get("province", province),
                )
                return bool(latest and _same_shadow_prediction(latest, value))
            return True
        try:
            db.supabase.table("model_predictions").insert(value).execute()
            return True
        except Exception as exc:
            if "23505" not in str(exc).upper():
                raise
            raced = get_shadow_prediction(
                db,
                str(value["model_name"]),
                str(value["prediction_date"]),
                region=str(value.get("region") or region),
                province=value.get("province", province),
            )
            if (
                raced
                and raced.get("status") in SHADOW_SUCCESS_STATUSES
                and value.get("status") not in SHADOW_SUCCESS_STATUSES
            ):
                return False
            if not raced:
                raise
            return write(_prepare_shadow_retry(raced, value), raced)

    try:
        saved = write(_prepare_shadow_retry(existing, record), existing)
    except Exception as exc:
        error = str(exc).lower()
        if not any(field in error for field in SHADOW_TRACKING_FIELDS):
            if "pgrst205" in error or "42p01" in error:
                print("  ⚠️  model_predictions missing; shadow result was not saved")
                return False
            raise
        print("  ⚠️  Shadow tracking migration pending; saving legacy-compatible row")
        legacy_record = _strip_fields(
            _prepare_shadow_retry(existing, record),
            SHADOW_TRACKING_FIELDS,
        )
        saved = write(legacy_record, existing)
    return saved


def normalize_xsmb_combo_shadow(
    result: "ComboSelectorResult",
    *,
    target_date: date | str,
    execution_source: str,
) -> dict:
    """Normalize combo v6 to an auditable XSMB shadow ledger row.

    The single aggregate objective is stored in ``score_1`` only. It must not
    be repeated as if each selected pair had an independently calibrated
    probability.
    """
    date_value = (
        target_date.isoformat()
        if isinstance(target_date, date)
        else str(target_date)
    )
    status = (
        result.status.value
        if hasattr(result.status, "value")
        else str(result.status)
    )
    pairs = list(result.top_pairs) if status == "success" else []
    if status == "success" and len(pairs) != 3:
        status = "error"
        pairs = []

    diagnostics = "; ".join(result.diagnostics)
    slots: dict[str, Any] = {}
    for index in range(5):
        slots[f"pair_{index + 1}"] = (
            int(pairs[index]) if index < len(pairs) else None
        )
        slots[f"score_{index + 1}"] = (
            float(result.objective_score)
            if index == 0 and pairs
            else None
        )

    return {
        "prediction_date": date_value,
        "region": "XSMB",
        "province": None,
        "model_name": "xsmb_combo_shadow",
        "model_type": "shadow",
        **slots,
        "execution_time_ms": None,
        "error_message": _safe_reason(
            diagnostics or (None if status == "success" else status)
        ),
        "status": status,
        "prediction_mode": "shadow",
        "model_version": result.selector_version,
        "score_semantics": result.score_semantics,
        "run_metadata": {
            "data_cutoff": date_value,
            "data_cutoff_rule": "draw_date < target_date",
            "execution_source": execution_source,
            "objective": result.objective,
            "objective_score": result.objective_score,
            "fusion_role": "production_weighted_tie_break",
            "expected_winning_circles": result.expected_winning_circles,
            "candidate_pool_size": len(result.candidate_pool),
            "evaluated_triples": result.evaluated_triples,
            "contributing_models": list(result.contributing_models),
            "skipped_models": list(result.skipped_models),
            "active_weights": dict(result.active_weights),
            "source_families": dict(result.source_families),
            "joint_pair_evidence": [
                {
                    "pair_a": edge.pair_a,
                    "pair_b": edge.pair_b,
                    "joint_score_uncalibrated": edge.probability,
                }
                for edge in result.joint_pair_evidence
            ],
        },
        "hit": None,
        "matched_pairs": None,
        "hit_count": None,
        "combo_hit": None,
        "verified_at": None,
    }
