"""
master_retrain_agent.py
Bộ não chính (Master Retrain Agent) — chạy sau khi đã có verify report.

Flow:
  1. Đọc prediction_results đã verify mới nhất hoặc ngày truyền vào.
  2. Phân loại single vs multi/ensemble prediction.
  3. Với mỗi đài bị MISS: decision_engine.analyze() → quyết định
  4. Nếu should_retrain và prediction trainable: trigger train_xgb.py qua subprocess
  4. Log tất cả quyết định vào agent_actions table
  5. Gửi Telegram summary về hành động đã thực hiện

Usage độc lập (dry-run):
  python src/agent/master_retrain_agent.py --date 2026-02-28 --dry-run
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.agent.decision_engine import DecisionEngine
from src.agent.hyperparameter_strategy import recommend_params, build_train_args, describe_strategy


# ─── Config ──────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.dirname(__file__))  # src/
TRAIN_SCRIPT = os.path.join(os.path.dirname(SCRIPT_DIR), "src", "scripts", "train_xgb.py")
PYTHON_EXEC = sys.executable  # dùng cùng Python environment

# Mapping province → weekday (từ verify_v3.py)
# Dùng để biết weekday model nào cần retrain
# Slug format: hyphen (khớp với DB)
XSMN_WEEKDAY_MAP = {
    0: ["tp-hcm", "dong-thap", "ca-mau"],
    1: ["ben-tre", "vung-tau", "bac-lieu"],
    2: ["dong-nai", "can-tho", "soc-trang"],
    3: ["tay-ninh", "an-giang", "binh-thuan"],
    4: ["vinh-long", "binh-duong", "tra-vinh"],
    5: ["tp-hcm", "long-an", "binh-phuoc", "hau-giang"],
    6: ["tien-giang", "kien-giang", "da-lat"],
}


def _get_weekday_for_province(province: str, target_weekday: int) -> Optional[int]:
    """Trả về weekday model cần check/retrain cho tỉnh XSMN."""
    # Chuẩn hóa về hyphen-slug (khớp với DB)
    normalized = province.replace("_", "-")
    stations = XSMN_WEEKDAY_MAP.get(target_weekday, [])
    if normalized in stations:
        return target_weekday
    return None


def _prediction_scope(prediction: dict) -> str:
    """Return 'multi' for ensemble rows, otherwise 'single'."""
    model_version = str(prediction.get("model_version") or "")
    if model_version.startswith("ensemble") or prediction.get("ensemble_method"):
        return "multi"
    return "single"


def _is_directly_trainable_prediction(region: str, province: Optional[str], model_scope: str) -> bool:
    """
    Whether this verified prediction maps to one train_xgb.py target.

    XSMN global ensemble rows use province='all' and combine many provinces plus
    rule-based sub-models. They are evaluated here, but there is no direct
    XSMN/all XGBoost training target in pair_features; per-province XSMN single
    rows and XSMB rows remain trainable.
    """
    if region.upper() == "XSMN" and model_scope == "multi" and province in (None, "all"):
        return False
    return True


def _latest_verified_date(db: LotteryDB) -> Optional[date]:
    """Return the most recent prediction_date that has verified results."""
    rows = (
        db.supabase.table("prediction_results")
        .select("prediction_date")
        .not_.is_("hit", "null")
        .order("prediction_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None
    return date.fromisoformat(rows[0]["prediction_date"])


def _log_action(db: LotteryDB, action_date: date, region: str, province: Optional[str],
                 weekday: Optional[int], action_type: str, reason: str,
                 strategy: Optional[str], old_auc: Optional[float],
                 old_hit_rate: Optional[float], old_params: dict, new_params: dict):
    """Ghi hành động agent vào agent_actions table."""
    try:
        db.supabase.table("agent_actions").insert({
            "action_date": action_date.isoformat(),
            "region": region,
            "province": province,
            "weekday": weekday,
            "action_type": action_type,
            "reason": reason,
            "strategy": strategy,
            "old_metric_auc": old_auc,
            "old_hit_rate": old_hit_rate,
            "old_params": json.dumps(old_params) if old_params else None,
            "new_params": json.dumps(new_params) if new_params else None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"  ⚠️  Không ghi được agent_actions: {e}")


def _trigger_retrain(region: str, province: Optional[str], weekday: Optional[int],
                     new_params: dict, dry_run: bool) -> bool:
    """
    Trigger train_xgb.py qua subprocess.
    Returns True nếu thành công (hoặc dry_run).
    """
    args = build_train_args(region, province, weekday, new_params)
    cmd = [PYTHON_EXEC, TRAIN_SCRIPT] + args

    label = f"{region}/{province or 'all'}"
    print(f"\n  🚀 Trigger retrain: {label}")
    print(f"     CMD: {' '.join(cmd)}")

    if dry_run:
        print(f"  🔵 [DRY-RUN] Không thực sự chạy train")
        return True

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 phút timeout
        )
        if result.returncode == 0:
            print(f"  ✅ Train thành công: {label}")
            print(result.stdout[-500:] if result.stdout else "")
            return True
        else:
            print(f"  ❌ Train thất bại (code={result.returncode}): {label}")
            print(result.stderr[-300:] if result.stderr else "")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ❌ Train timeout (>10 phút): {label}")
        return False
    except Exception as e:
        print(f"  ❌ Lỗi khi trigger train: {e}")
        return False


async def run_agent(
    db: LotteryDB,
    notifier: LotteryNotifier,
    verify_results: list[dict],
    target_date: date,
    dry_run: bool = False,
):
    """
    Entrypoint chính — gọi bởi workflow 04 sau khi verify report đã có.

    Args:
        verify_results: list của dict {"label", "hit", "pairs", "matched", "region", "province"}
        target_date: ngày đã verify
        dry_run: nếu True, chỉ in quyết định, không thực sự trigger train
    """
    if not verify_results:
        return

    engine = DecisionEngine()
    weekday = target_date.weekday()

    print(f"\n{'='*50}")
    print(f"🤖 Master Retrain Agent — {target_date} {'[DRY-RUN]' if dry_run else ''}")
    print(f"{'='*50}")

    actions_summary = []

    for r in verify_results:
        region = r.get("region", "")
        province = r.get("province")
        hit = r.get("hit", False)
        model_scope = r.get("model_scope") or _prediction_scope(r)
        scope_label = "MULTI" if model_scope == "multi" else "SINGLE"
        label = r.get("label", f"{region}/{province or 'all'}")
        display_label = f"{scope_label} {label}"

        # Xác định weekday model cần check
        if region.upper() == "XSMB":
            # XSMB giờ đã split theo weekday → pass weekday của ngày hiện tại
            station_weekday = weekday
        else:
            station_weekday = _get_weekday_for_province(province or "", weekday)

        # Phân tích quyết định
        decision = engine.analyze(
            region=region,
            province=province,
            weekday=station_weekday,
            hit_today=hit,
            db=db,
            target_date=target_date,
        )

        action_icon = {
            "no_action": "✅",
            "skipped": "⏭️",
            "retrain_triggered": "🔁",
        }.get(decision.action_type, "❓")

        print(f"\n  {action_icon} {display_label}: {decision.reason}")

        old_params = {}
        new_params = {}
        train_success = None
        action_type = decision.action_type
        reason = f"[{model_scope}] {decision.reason}"
        strategy = decision.strategy

        if decision.should_retrain:
            if not _is_directly_trainable_prediction(region, province, model_scope):
                action_type = "skipped"
                reason += " | Global XSMN multi ensemble is evaluated but not directly trainable; province-level XGB rows handle retrain."
                strategy = None
                print("     ⏭️  Global XSMN ensemble row is monitor-only for direct retrain.")
            else:
                old_params, new_params = recommend_params(
                    decision.strategy,
                    region=region,
                    consecutive_fails=decision.consecutive_fails,
                    old_auc=decision.old_metric_auc,
                    old_hit_rate=decision.old_hit_rate,
                )
                strategy_desc = describe_strategy(decision.strategy, old_params, new_params)
                print(f"     📊 Strategy: {strategy_desc}")

                train_success = _trigger_retrain(
                    region=region,
                    province=province,
                    weekday=station_weekday,
                    new_params=new_params,
                    dry_run=dry_run,
                )

        # Ghi log vào DB. Dry-run là chế độ kiểm tra thủ công, không tạo audit action.
        if dry_run:
            print("     🔵 [DRY-RUN] Skip writing agent_actions")
        else:
            _log_action(
                db=db,
                action_date=target_date,
                region=region,
                province=province,
                weekday=station_weekday,
                action_type=action_type,
                reason=reason,
                strategy=strategy,
                old_auc=decision.old_metric_auc,
                old_hit_rate=decision.old_hit_rate,
                old_params=old_params,
                new_params=new_params,
            )

        actions_summary.append({
            "label": display_label,
            "model_scope": model_scope,
            "action_type": action_type,
            "reason": reason,
            "strategy": strategy,
            "train_success": train_success,
            "consecutive_fails": decision.consecutive_fails,
        })

    # Gửi Telegram summary
    await _send_agent_report(notifier, actions_summary, target_date, dry_run)


async def _send_agent_report(
    notifier: LotteryNotifier,
    actions: list[dict],
    target_date: date,
    dry_run: bool,
):
    """Gửi Telegram report tổng hợp về hành động của agent."""
    retrain_list = [a for a in actions if a["action_type"] == "retrain_triggered"]
    skip_list    = [a for a in actions if a["action_type"] == "skipped"]
    single_count = sum(1 for a in actions if a.get("model_scope") == "single")
    multi_count = sum(1 for a in actions if a.get("model_scope") == "multi")

    # Chỉ gửi nếu có hành động đáng chú ý (có retrain hoặc có skip với lý do)
    if not retrain_list and not skip_list:
        print("\n🤖 Agent: Tất cả đài đều trúng — không cần hành động.")
        return

    date_str = target_date.strftime("%d/%m/%Y")
    dry_tag = " [DRY-RUN]" if dry_run else ""
    msg = f"🤖 <b>AGENT RETRAIN REPORT{dry_tag} — {date_str}</b>\n\n"
    msg += f"Scope: <code>{single_count}</code> single | <code>{multi_count}</code> multi\n\n"

    if retrain_list:
        msg += "🔁 <b>Đã trigger retrain:</b>\n"
        for a in retrain_list:
            status_icon = "✅" if a["train_success"] else "❌"
            msg += (
                f"  {status_icon} {a['label']}\n"
                f"     Strategy: <code>{a['strategy']}</code> | "
                f"Fail streak: {a['consecutive_fails']} kỳ\n"
            )
        msg += "\n"

    if skip_list:
        msg += "⏭️ <b>Đã bỏ qua (cooldown/metric OK):</b>\n"
        for a in skip_list:
            msg += f"  • {a['label']}: <i>{a['reason']}</i>\n"

    await notifier.send_message(msg, config_key="master_retrain_agent")


# ─── Standalone entrypoint ───────────────────────────────────────────────────

async def main():
    """
    Chạy agent độc lập (không cần verify_v3.py).
    Đọc kết quả verify từ DB cho ngày chỉ định.
    """
    parser = argparse.ArgumentParser(description="Master Retrain Agent")
    parser.add_argument("--date", type=str, help="Ngày target (YYYY-MM-DD). Mặc định = latest verified")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in quyết định, không thực sự retrain")
    args = parser.parse_args()

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="master_retrain_agent")

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        latest_date = _latest_verified_date(db)
        if latest_date is None:
            print("⚠️  Không có prediction đã verify để đánh giá retrain")
            return
        target_date = latest_date

    # Lấy kết quả verify từ DB
    preds = (
        db.supabase.table("prediction_results")
        .select("region,province,hit,pair_1,pair_2,pair_3,matched_pairs,model_version,ensemble_method")
        .eq("prediction_date", target_date.isoformat())
        .not_.is_("hit", "null")  # chỉ lấy row đã verify xong
        .execute()
        .data
    )

    if not preds:
        print(f"⚠️  Không có kết quả verify cho {target_date}")
        return

    # Chuẩn hóa format giống verify_results trong verify_v3.py
    verify_results = [
        {
            "label": f"{p['region']}/{p['province'] or 'all'}",
            "region": p["region"],
            "province": p["province"],
            "hit": p["hit"],
            "pairs": [p["pair_1"], p["pair_2"], p["pair_3"]],
            "matched": p.get("matched_pairs") or [],
            "model_version": p.get("model_version"),
            "ensemble_method": p.get("ensemble_method"),
            "model_scope": _prediction_scope(p),
        }
        for p in preds
    ]

    print(f"📋 Loaded {len(verify_results)} prediction results cho {target_date}")
    await run_agent(db, notifier, verify_results, target_date, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
