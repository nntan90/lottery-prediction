"""Coordinate XSMN provincial ML retraining and rule-family refresh after verify."""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.agent.decision_engine import DecisionEngine
from src.agent.hyperparameter_strategy import (
    build_lstm_train_args,
    build_train_args,
    recommend_params,
)
from src.agent.provincial_model_refresh import RULE_FAMILIES, refresh_rule_families
from src.bot.telegram_bot import LotteryNotifier
from src.database.supabase_client import LotteryDB


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
TRAIN_SCRIPTS = {
    "xgboost": os.path.join(PROJECT_ROOT, "src", "scripts", "train_xgb.py"),
    "lstm": os.path.join(PROJECT_ROOT, "src", "scripts", "train_lstm.py"),
}
TRAIN_TIMEOUTS = {"xgboost": 1800, "lstm": 1800}
PYTHON_EXEC = sys.executable
ML_FAMILIES = {"xgboost": "xgboost_core", "lstm": "lstm"}
SUCCESS_STATUSES = {"trained", "refreshed", "fresh", "newer"}
XSMN_WEEKDAY_MAP = {
    0: ["tp-hcm", "dong-thap", "ca-mau"],
    1: ["ben-tre", "vung-tau", "bac-lieu"],
    2: ["dong-nai", "can-tho", "soc-trang"],
    3: ["tay-ninh", "an-giang", "binh-thuan"],
    4: ["vinh-long", "binh-duong", "tra-vinh"],
    5: ["tp-hcm", "long-an", "binh-phuoc", "hau-giang"],
    6: ["tien-giang", "kien-giang", "da-lat"],
}


@dataclass(frozen=True)
class ProvincialTarget:
    """One complete province-weekday feature target."""

    province: str
    weekday: int
    pair_count: int
    complete: bool
    error: Optional[str] = None


def _get_weekday_for_province(province: str, target_weekday: int) -> Optional[int]:
    """Return the matching XSMN weekday for a province slug."""
    normalized = province.replace("_", "-")
    return target_weekday if normalized in XSMN_WEEKDAY_MAP.get(target_weekday, []) else None


def _prediction_scope(prediction: dict) -> str:
    """Return ``multi`` for ensemble rows, otherwise ``single``."""
    return "multi" if str(prediction.get("model_version") or "").startswith("ensemble") else "single"


def _is_directly_trainable_prediction(
    region: str,
    province: Optional[str],
    model_scope: str,
) -> bool:
    """Retain the legacy helper while provincial targets replace prediction-driven routing."""
    if region.upper() == "XSMB":
        return False
    return not (region.upper() == "XSMN" and model_scope == "multi" and province in (None, "all"))


def _latest_verified_date(db: LotteryDB) -> Optional[date]:
    """Return the most recent verified prediction date."""
    rows = (
        db.supabase.table("prediction_results")
        .select("prediction_date")
        .not_.is_("hit", "null")
        .order("prediction_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return date.fromisoformat(rows[0]["prediction_date"]) if rows else None


def _resolve_targets(db: LotteryDB, target_date: date) -> list[ProvincialTarget]:
    """Resolve scheduled provinces and require exactly 100 labeled unique pairs."""
    targets = []
    for province in XSMN_WEEKDAY_MAP.get(target_date.weekday(), []):
        try:
            rows = (
                db.supabase.table("pair_features")
                .select("pair,hit")
                .eq("region", "XSMN")
                .eq("province", province)
                .eq("feature_date", target_date.isoformat())
                .not_.is_("hit", "null")
                .execute()
                .data
            )
            pairs = {int(row["pair"]) for row in rows if row.get("pair") is not None}
            complete = len(rows) == 100 and pairs == set(range(100))
            error = None if complete else (
                f"Expected 100 labeled unique pairs, got rows={len(rows)}, unique={len(pairs)}"
            )
            targets.append(ProvincialTarget(province, target_date.weekday(), len(pairs), complete, error))
        except Exception as exc:
            targets.append(ProvincialTarget(province, target_date.weekday(), 0, False, str(exc)))
    return targets


def _is_ml_fresh(
    db: LotteryDB,
    province: str,
    weekday: int,
    family: str,
    target_date: date,
) -> bool:
    """Check artifact freshness at the full family/province/weekday grain."""
    try:
        rows = (
            db.supabase.table("model_registry")
            .select("version,train_end_date")
            .eq("region", "XSMN")
            .eq("province", province)
            .eq("weekday", weekday)
            .eq("model_name", ML_FAMILIES[family])
            .eq("status", "active")
            .eq("train_end_date", target_date.isoformat())
            .limit(1)
            .execute()
            .data
        )
        return bool(rows)
    except Exception as exc:
        print(f"     ⚠️ {family} freshness query failed: {exc}")
        return False


def _newer_ml_train_end(
    db: LotteryDB,
    province: str,
    weekday: int,
    family: str,
    target_date: date,
) -> Optional[str]:
    """Return a newer active cutoff so historical recovery cannot roll production back."""
    try:
        rows = (
            db.supabase.table("model_registry")
            .select("train_end_date")
            .eq("region", "XSMN")
            .eq("province", province)
            .eq("weekday", weekday)
            .eq("model_name", ML_FAMILIES[family])
            .eq("status", "active")
            .gt("train_end_date", target_date.isoformat())
            .order("train_end_date", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return rows[0]["train_end_date"] if rows else None
    except Exception as exc:
        print(f"     ⚠️ {family} newer-artifact query failed: {exc}")
        return None


def _previous_rule_updates(
    db: LotteryDB,
    province: str,
    weekday: int,
    target_date: date,
) -> dict[str, dict[str, Any]]:
    """Read successful rule freshness from prior idempotent coordinator attempts."""
    try:
        rows = (
            db.supabase.table("agent_actions")
            .select("new_params")
            .eq("action_date", target_date.isoformat())
            .eq("region", "XSMN")
            .eq("province", province)
            .eq("weekday", weekday)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
            .data
        )
    except Exception:
        return {}
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("new_params") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                continue
        if not isinstance(payload, dict):
            continue
        model_updates = payload.get("model_updates") or {}
        if not isinstance(model_updates, dict):
            continue
        for family, update in model_updates.items():
            if not isinstance(update, dict):
                continue
            if family in RULE_FAMILIES and family not in merged:
                if (
                    update.get("status") in SUCCESS_STATUSES
                    and update.get("latest_history_date") == target_date.isoformat()
                    and int(update.get("n_draws_used") or 0) > 0
                ):
                    merged[family] = update
    return merged


def _log_action(
    db: LotteryDB,
    action_date: date,
    province: str,
    weekday: int,
    action_type: str,
    reason: str,
    strategy: Optional[str],
    old_auc: Optional[float],
    old_hit_rate: Optional[float],
    old_params: dict,
    new_params: dict,
) -> bool:
    """Write one six-family provincial audit to the existing JSON fields."""
    try:
        db.supabase.table("agent_actions").insert({
            "action_date": action_date.isoformat(),
            "region": "XSMN",
            "province": province,
            "weekday": weekday,
            "action_type": action_type,
            "reason": reason,
            "strategy": strategy,
            "old_metric_auc": old_auc,
            "old_hit_rate": old_hit_rate,
            "old_params": json.dumps(old_params) if old_params else None,
            "new_params": json.dumps(new_params),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return True
    except Exception as exc:
        print(f"  ⚠️ Không ghi được agent_actions: {exc}")
        return False


def _run_train_process(family: str, args: list[str], dry_run: bool) -> bool:
    """Run one ML family independently so a failure cannot stop sibling families."""
    cmd = [PYTHON_EXEC, TRAIN_SCRIPTS[family], *args]
    print(f"     🚀 {family}: {' '.join(cmd)}")
    if dry_run:
        return True
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TRAIN_TIMEOUTS[family],
        )
    except subprocess.TimeoutExpired:
        print(f"     ❌ {family} timeout >{TRAIN_TIMEOUTS[family] // 60} phút")
        return False
    except Exception as exc:
        print(f"     ❌ {family}: {exc}")
        return False
    if result.returncode != 0:
        print(f"     ❌ {family} exit={result.returncode}: {(result.stderr or result.stdout)[-500:]}")
        return False
    print(f"     ✅ {family}: {(result.stdout or '')[-300:]}")
    return True


def _complete_training_queue(db: LotteryDB, province: str) -> bool:
    """Mark a provincial queue item done only after all six families and audit succeed."""
    try:
        (
            db.supabase.table("training_queue")
            .update({"status": "done", "completed_at": datetime.now(timezone.utc).isoformat()})
            .eq("region", "XSMN")
            .eq("province", province)
            .eq("status", "triggered")
            .execute()
        )
        return True
    except Exception as exc:
        print(f"     ⚠️ training_queue completion failed: {exc}")
        return False


def _verified_single_by_province(verify_results: list[dict]) -> dict[str, dict]:
    """Index verified single rows without making them target authority."""
    return {
        row["province"]: row
        for row in verify_results
        if row.get("region", "").upper() == "XSMN"
        and row.get("province") not in (None, "all")
        and (row.get("model_scope") or _prediction_scope(row)) == "single"
    }


async def run_agent(
    db: LotteryDB,
    notifier: LotteryNotifier,
    verify_results: list[dict],
    target_date: date,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Update all six model families for every complete scheduled XSMN target."""
    print(f"\n{'=' * 64}\n🤖 Provincial Model Coordinator — {target_date}\n{'=' * 64}")
    targets = _resolve_targets(db, target_date)
    verified = _verified_single_by_province(verify_results)
    engine = DecisionEngine()
    summaries: list[dict[str, Any]] = []

    for target in targets:
        print(f"\n  📍 XSMN/{target.province} [weekday={target.weekday}]")
        if not target.complete:
            updates = {
                family: {
                    "status": "failed",
                    "latest_history_date": None,
                    "n_draws_used": target.pair_count,
                    "error": target.error,
                }
                for family in (*ML_FAMILIES, *RULE_FAMILIES)
            }
            summary = {
                "province": target.province,
                "success": False,
                "action_type": "skipped",
                "model_updates": updates,
                "audit_ok": True,
                "queue_ok": True,
            }
            if not dry_run:
                summary["audit_ok"] = _log_action(
                    db, target_date, target.province, target.weekday,
                    "skipped", target.error or "Incomplete labels",
                    None, None, None, {}, {"target_date": target_date.isoformat(), "model_updates": updates},
                )
            summaries.append(summary)
            continue

        prediction = verified.get(target.province)
        if prediction:
            try:
                decision = engine.analyze(
                    "XSMN", target.province, target.weekday,
                    bool(prediction.get("hit")), db, target_date,
                )
                strategy = decision.strategy or "maintain"
                old_auc = decision.old_metric_auc
                old_hit_rate = decision.old_hit_rate
                consecutive_fails = decision.consecutive_fails
                reason = decision.reason
            except Exception as exc:
                strategy = "maintain"
                old_auc = old_hit_rate = None
                consecutive_fails = 0
                reason = f"Decision metrics unavailable; maintain strategy: {exc}"
        else:
            strategy = "maintain"
            old_auc = old_hit_rate = None
            consecutive_fails = 0
            reason = "Missing single prediction; default deterministic maintain strategy"

        old_params, xgb_params = recommend_params(
            strategy,
            region="XSMN",
            consecutive_fails=consecutive_fails,
            old_auc=old_auc,
            old_hit_rate=old_hit_rate,
        )
        xgb_params["_target_date"] = target_date.isoformat()
        updates: dict[str, dict[str, Any]] = {}

        for family in ML_FAMILIES:
            newer_train_end = _newer_ml_train_end(
                db, target.province, target.weekday, family, target_date
            )
            if newer_train_end:
                updates[family] = {
                    "status": "newer",
                    "train_end_date": newer_train_end,
                    "error": "Historical target skipped to preserve newer active artifact",
                }
                continue
            if _is_ml_fresh(db, target.province, target.weekday, family, target_date):
                updates[family] = {
                    "status": "fresh",
                    "train_end_date": target_date.isoformat(),
                    "error": None,
                }
                continue
            args = (
                build_train_args("XSMN", target.province, target.weekday, xgb_params)
                if family == "xgboost"
                else build_lstm_train_args("XSMN", target.province, target.weekday, target_date)
            )
            process_ok = _run_train_process(family, args, dry_run)
            registry_ok = dry_run or _is_ml_fresh(
                db, target.province, target.weekday, family, target_date
            )
            updates[family] = {
                "status": "trained" if registry_ok else "failed",
                "train_end_date": target_date.isoformat() if registry_ok else None,
                "error": (
                    "Subprocess exited nonzero after publishing a fresh artifact"
                    if registry_ok and not process_ok
                    else None
                ) if registry_ok else (
                    "Training subprocess failed" if not process_ok
                    else "Registry freshness check failed"
                ),
            }

        prior_rules = _previous_rule_updates(db, target.province, target.weekday, target_date)
        pending_rules = {
            family: predictor
            for family, predictor in RULE_FAMILIES.items()
            if family not in prior_rules
        }
        for family, previous in prior_rules.items():
            updates[family] = {**previous, "status": "fresh"}
        if pending_rules:
            updates.update(
                refresh_rule_families(
                    db, target.province, target.weekday, target_date,
                    families=pending_rules,
                )
            )

        success = all(
            updates.get(family, {}).get("status") in SUCCESS_STATUSES
            for family in (*ML_FAMILIES, *RULE_FAMILIES)
        )
        trained_ml = any(
            updates.get(family, {}).get("status") == "trained" for family in ML_FAMILIES
        )
        refreshed_rules = any(
            updates.get(family, {}).get("status") == "refreshed" for family in RULE_FAMILIES
        )
        action_type = (
            "retrain_triggered" if trained_ml
            else "refresh_triggered" if refreshed_rules
            else "no_action" if success
            else "retrain_failed"
        )
        print(f"     {'✅' if success else '❌'} updates={updates}")
        audit_payload = {
            "target_date": target_date.isoformat(),
            "model_updates": updates,
            "xgboost_params": xgb_params,
        }
        audit_ok = True
        queue_ok = True
        if not dry_run:
            audit_ok = _log_action(
                db, target_date, target.province, target.weekday, action_type,
                reason, strategy, old_auc, old_hit_rate, old_params, audit_payload,
            )
            if not audit_ok:
                success = False
            elif success and not _complete_training_queue(db, target.province):
                queue_ok = False
                success = False
        summaries.append({
            "province": target.province,
            "success": success,
            "action_type": action_type,
            "strategy": strategy,
            "model_updates": updates,
            "audit_ok": audit_ok,
            "queue_ok": queue_ok,
        })

    await _send_agent_report(notifier, summaries, target_date, dry_run)
    return summaries


async def _send_agent_report(
    notifier: LotteryNotifier,
    actions: list[dict[str, Any]],
    target_date: date,
    dry_run: bool,
) -> None:
    """Send a compact six-family freshness report."""
    if (
        not dry_run
        and actions
        and all(action.get("success") and action.get("action_type") == "no_action"
                for action in actions)
    ):
        print("ℹ️ Tất cả model đã fresh; bỏ qua Telegram recovery no-op")
        return

    dry_tag = " [DRY-RUN]" if dry_run else ""
    message = (
        f"🤖 <b>XSMN MODEL REFRESH{dry_tag} — {target_date.strftime('%d/%m/%Y')}</b>\n\n"
    )
    for action in actions:
        icon = "✅" if action["success"] else "❌"
        failed = [
            family for family, update in action["model_updates"].items()
            if update.get("status") not in SUCCESS_STATUSES
        ]
        suffix = (
            "audit failed" if action.get("audit_ok") is False
            else "queue completion failed" if action.get("queue_ok") is False
            else "đủ 6/6" if not failed
            else f"failed: {', '.join(failed)}"
        )
        message += f"{icon} <code>{action['province']}</code>: {suffix}\n"
    await notifier.send_message(message, config_key="master_retrain_agent")


async def main() -> None:
    """Run post-verify or scheduled idempotent recovery."""
    parser = argparse.ArgumentParser(description="XSMN Provincial Model Coordinator")
    parser.add_argument("--date", type=str, help="Target date YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="master_retrain_agent")
    target_date = date.fromisoformat(args.date) if args.date else _latest_verified_date(db)
    if target_date is None:
        print("⚠️ Không có ngày verify để refresh")
        raise SystemExit(1)

    preds = (
        db.supabase.table("prediction_results")
        .select("region,province,hit,model_version")
        .eq("prediction_date", target_date.isoformat())
        .not_.is_("hit", "null")
        .execute()
        .data
    )
    verify_results = [
        {
            **row,
            "model_scope": _prediction_scope(row),
            "label": f"{row['region']}/{row.get('province') or 'all'}",
        }
        for row in preds
    ]
    summaries = await run_agent(db, notifier, verify_results, target_date, args.dry_run)
    if not summaries or any(not item["success"] for item in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
