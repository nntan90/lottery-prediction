"""
weekly_report.py — BÁO CÁO TOÀN HỆ THỐNG HÀNG TUẦN (Weekly System Report)

Tổng hợp toàn bộ hoạt động trong tuần vừa qua:
  1. Predictions: tổng số dự đoán, hit/miss breakdown theo đài
  2. Profit: tổng chi phí / doanh thu / lợi nhuận
  3. Model Health: retrain actions, strategy used, metric changes
  4. Crawler: success rate, records crawled
  5. System Stability: error rate, uptime

Output:
  - XML file: reports/weekly_YYYYMMDD.xml (lưu local + upload artifact)
  - Telegram: bảng tóm tắt ngắn gọn

Schedule:
  - Chạy tự động sáng Thứ 2, 8:00 VNT (01:00 UTC)
  - Tổng hợp dữ liệu từ Thứ 2 tuần trước → Chủ nhật vừa rồi

Usage:
  python src/scripts/weekly_report.py                           # tuần hiện tại
  python src/scripts/weekly_report.py --week-end 2026-05-11     # chỉ định ngày CN kết thúc tuần
"""

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier


SHADOW_MODEL_NAMES = frozenset({"cmr_shadow", "ddt_shadow"})
SHADOW_SUCCESS_STATUSES = frozenset({"success", "uncalibrated"})
PERFORMANCE_SCOPE_ORDER = (
    ("xsmb", "XSMB"),
    ("xsmn_consensus", "XSMN đồng thuận"),
    ("ddt", "DDT"),
    ("cmr", "CMR"),
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _week_range(week_end: date) -> tuple[date, date]:
    """Return (Monday, Sunday) for the week ending on week_end.

    If week_end is not a Sunday, rewind to the previous Sunday.
    """
    # Ensure week_end is Sunday
    if week_end.weekday() != 6:
        # Go back to previous Sunday
        week_end = week_end - timedelta(days=(week_end.weekday() + 1) % 7)
    week_start = week_end - timedelta(days=6)
    return week_start, week_end


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _iso(d: date) -> str:
    return d.isoformat()


# ── Data Collectors ──────────────────────────────────────────────────────────

def _collect_predictions(db: LotteryDB, start: date, end: date) -> list[dict]:
    """Fetch all prediction_results in the date range."""
    return (
        db.supabase.table("prediction_results")
        .select("*")
        .gte("prediction_date", _iso(start))
        .lte("prediction_date", _iso(end))
        .order("prediction_date")
        .execute()
        .data
    )


def _collect_shadow_predictions(
    db: LotteryDB,
    start: date,
    end: date,
) -> list[dict]:
    """Fetch canonical XSMN/all DDT and CMR shadow rows without failing the job."""
    try:
        return (
            db.supabase.table("model_predictions")
            .select("*")
            .gte("prediction_date", _iso(start))
            .lte("prediction_date", _iso(end))
            .eq("region", "XSMN")
            .eq("province", "all")
            .in_("model_name", sorted(SHADOW_MODEL_NAMES))
            .order("prediction_date")
            .execute()
            .data
        )
    except Exception as exc:
        print(
            "  ⚠️  model_predictions shadow query failed; "
            f"DDT/CMR weekly coverage will be 0: {exc}"
        )
        return []


def _collect_profit(db: LotteryDB, start: date, end: date) -> list[dict]:
    """Fetch profit_tracking records in the date range."""
    try:
        return (
            db.supabase.table("profit_tracking")
            .select("*")
            .gte("prediction_date", _iso(start))
            .lte("prediction_date", _iso(end))
            .execute()
            .data
        )
    except Exception as e:
        print(f"  ⚠️  profit_tracking query failed: {e}")
        return []


def _collect_crawler_logs(db: LotteryDB, start: date, end: date) -> list[dict]:
    """Fetch crawler_logs in the date range."""
    return (
        db.supabase.table("crawler_logs")
        .select("*")
        .gte("crawl_date", _iso(start))
        .lte("crawl_date", _iso(end))
        .execute()
        .data
    )


def _collect_agent_actions(db: LotteryDB, start: date, end: date) -> list[dict]:
    """Fetch agent_actions in the date range."""
    try:
        return (
            db.supabase.table("agent_actions")
            .select("*")
            .gte("action_date", _iso(start))
            .lte("action_date", _iso(end))
            .order("action_date")
            .execute()
            .data
        )
    except Exception as e:
        print(f"  ⚠️  agent_actions query failed: {e}")
        return []


def _collect_active_models(db: LotteryDB) -> list[dict]:
    """Fetch current active models from model_registry."""
    return (
        db.supabase.table("model_registry")
        .select("*")
        .eq("status", "active")
        .order("trained_at", desc=True)
        .execute()
        .data
    )


def _collect_training_queue(db: LotteryDB, start: date, end: date) -> list[dict]:
    """Fetch training_queue entries in the date range."""
    try:
        return (
            db.supabase.table("training_queue")
            .select("*")
            .gte("created_at", _iso(start))
            .lte("created_at", _iso(end) + "T23:59:59")
            .execute()
            .data
        )
    except Exception:
        return []


# ── Analysis ─────────────────────────────────────────────────────────────────

def _normalize_pair(value) -> Optional[int]:
    """Return a stored lottery pair as 0-99, rejecting lossy conversions."""
    if isinstance(value, bool):
        return None
    try:
        pair = int(value)
    except (TypeError, ValueError):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    if not 0 <= pair <= 99:
        return None
    return pair


def _top_three(row: dict) -> Optional[tuple[int, int, int]]:
    """Return the canonical unique Top 3 or ``None`` for incomplete data."""
    pairs = tuple(_normalize_pair(row.get(f"pair_{index}")) for index in range(1, 4))
    if any(pair is None for pair in pairs) or len(set(pairs)) != 3:
        return None
    return pairs  # type: ignore[return-value]


def _unique_matched_count(row: dict, top_three: tuple[int, int, int]) -> int:
    """Count unique legacy matches only when they belong to the stored Top 3."""
    matched = row.get("matched_pairs")
    if not isinstance(matched, (list, tuple, set)):
        return 0
    normalized = {
        pair
        for value in matched
        if (pair := _normalize_pair(value)) is not None and pair in top_three
    }
    return len(normalized)


def _integer_hit_count(value) -> Optional[int]:
    """Normalize a non-negative stored hit count without treating booleans as ints."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if 0 <= count <= 3 else None


def _has_verdict(row: dict, *, allow_legacy_hit: bool) -> bool:
    """Return whether a row contains measured verification evidence."""
    if isinstance(row.get("combo_hit"), bool):
        return True
    if _integer_hit_count(row.get("hit_count")) is not None:
        return True
    if isinstance(row.get("matched_pairs"), (list, tuple, set)):
        return True
    return allow_legacy_hit and isinstance(row.get("hit"), bool)


def _is_combo_hit(
    row: dict,
    top_three: tuple[int, int, int],
    *,
    allow_legacy_hit: bool,
) -> bool:
    """Resolve the >=2/3 verdict while keeping legacy ``hit`` production-only."""
    if row.get("combo_hit") is True:
        return True
    hit_count = _integer_hit_count(row.get("hit_count"))
    if hit_count is not None:
        return hit_count >= 2
    if isinstance(row.get("matched_pairs"), (list, tuple, set)):
        return _unique_matched_count(row, top_three) >= 2
    return allow_legacy_hit and row.get("hit") is True


def _canonical_scope(row: dict, *, shadow: bool) -> Optional[str]:
    """Classify one row into a canonical weekly ledger scope."""
    region = str(row.get("region") or "").upper()
    province = row.get("province")
    province_name = str(province or "").lower()
    if shadow:
        model_name = str(row.get("model_name") or "").lower()
        status = str(row.get("status") or "").lower()
        if (
            region != "XSMN"
            or province_name != "all"
            or model_name not in SHADOW_MODEL_NAMES
            or status not in SHADOW_SUCCESS_STATUSES
        ):
            return None
        return "ddt" if model_name == "ddt_shadow" else "cmr"

    model_version = str(row.get("model_version") or "").lower()
    if not model_version.startswith("ensemble"):
        return None
    if region == "XSMB" and province is None:
        return "xsmb"
    if region == "XSMN" and province_name == "all":
        return "xsmn_consensus"
    return None


def _row_choice_key(row: dict, *, allow_legacy_hit: bool) -> tuple:
    """Provide a stable preference for verified and most recently stored rows."""
    row_id = row.get("id")
    try:
        numeric_id = int(row_id)
    except (TypeError, ValueError):
        numeric_id = -1
    return (
        int(_has_verdict(row, allow_legacy_hit=allow_legacy_hit)),
        str(row.get("created_at") or ""),
        numeric_id,
        str(row.get("model_version") or ""),
        str(row.get("model_name") or ""),
        tuple(str(row.get(f"pair_{index}")) for index in range(1, 4)),
    )


def _seven_day_performance(
    predictions: list[dict],
    shadow_predictions: list[dict],
    start: date,
    end: date,
) -> dict:
    """Aggregate one deterministic >=2/3 verdict per scope and calendar day."""
    period_days = max((end - start).days + 1, 0)
    selected: dict[str, dict[str, tuple[dict, tuple[int, int, int], bool]]] = {
        key: {} for key, _ in PERFORMANCE_SCOPE_ORDER
    }

    for rows, shadow in ((predictions, False), (shadow_predictions, True)):
        for row in rows:
            scope = _canonical_scope(row, shadow=shadow)
            top_three = _top_three(row)
            day = str(row.get("prediction_date") or "")[:10]
            if scope is None or top_three is None:
                continue
            try:
                prediction_day = date.fromisoformat(day)
            except ValueError:
                continue
            if not start <= prediction_day <= end:
                continue
            candidate = (row, top_three, not shadow)
            current = selected[scope].get(day)
            if current is None or _row_choice_key(
                row,
                allow_legacy_hit=not shadow,
            ) > _row_choice_key(
                current[0],
                allow_legacy_hit=current[2],
            ):
                selected[scope][day] = candidate

    scopes = {}
    for key, label in PERFORMANCE_SCOPE_ORDER:
        rows = selected[key]
        verified_days = 0
        hit_days = 0
        for row, top_three, allow_legacy_hit in rows.values():
            if not _has_verdict(row, allow_legacy_hit=allow_legacy_hit):
                continue
            verified_days += 1
            if _is_combo_hit(
                row,
                top_three,
                allow_legacy_hit=allow_legacy_hit,
            ):
                hit_days += 1
        scopes[key] = {
            "label": label,
            "hit_days": hit_days,
            "prediction_days": len(rows),
            "verified_days": verified_days,
            "period_days": period_days,
        }
    return {"period_days": period_days, "scopes": scopes}


def _analyze(predictions, profits, crawler_logs, agent_actions, active_models, training_queue,
             start: date, end: date, shadow_predictions=None) -> dict:
    """Produce a structured analysis dict from raw data."""
    shadow_predictions = shadow_predictions or []

    # ── Predictions ─────────────────────────
    total_preds = len(predictions)
    verified = [p for p in predictions if p.get("hit") is not None]
    hits = [p for p in verified if p["hit"]]
    misses = [p for p in verified if not p["hit"]]
    unverified = total_preds - len(verified)

    # Per-region breakdown
    region_stats = {}
    for p in predictions:
        region = p["region"]
        if region not in region_stats:
            region_stats[region] = {"total": 0, "hit": 0, "miss": 0, "unverified": 0}
        region_stats[region]["total"] += 1
        if p.get("hit") is None:
            region_stats[region]["unverified"] += 1
        elif p["hit"]:
            region_stats[region]["hit"] += 1
        else:
            region_stats[region]["miss"] += 1

    # Per-day breakdown
    day_stats = {}
    for p in predictions:
        d = p["prediction_date"]
        if d not in day_stats:
            day_stats[d] = {"total": 0, "hit": 0, "miss": 0}
        day_stats[d]["total"] += 1
        if p.get("hit"):
            day_stats[d]["hit"] += 1
        elif p.get("hit") is False:
            day_stats[d]["miss"] += 1

    # ── Profit ──────────────────────────────
    total_cost = sum(p.get("cost", 0) for p in profits)
    total_revenue = sum(p.get("revenue", 0) for p in profits)
    total_profit = sum(p.get("profit", 0) for p in profits)
    total_hit_pairs = sum(1 for p in profits if p.get("hit_count", 0) > 0)
    total_tracked_pairs = len(profits)

    # Per-region profit
    region_profit = {}
    for p in profits:
        region = p.get("region", "unknown")
        if region not in region_profit:
            region_profit[region] = {"cost": 0, "revenue": 0, "profit": 0}
        region_profit[region]["cost"] += p.get("cost", 0)
        region_profit[region]["revenue"] += p.get("revenue", 0)
        region_profit[region]["profit"] += p.get("profit", 0)

    # ── Crawler ─────────────────────────────
    total_crawls = len(crawler_logs)
    success_crawls = sum(1 for c in crawler_logs if c.get("status") == "success")
    failed_crawls = total_crawls - success_crawls
    total_records = sum(c.get("records_inserted", 0) or 0 for c in crawler_logs)
    crawl_errors = [c for c in crawler_logs if c.get("status") != "success"]

    # ── Agent ───────────────────────────────
    total_actions = len(agent_actions)
    retrain_actions = [a for a in agent_actions if a.get("action_type") == "retrain_triggered"]
    skip_actions = [a for a in agent_actions if a.get("action_type") == "skipped"]
    no_actions = [a for a in agent_actions if a.get("action_type") == "no_action"]

    # Strategy distribution
    strategy_dist = {}
    for a in retrain_actions:
        s = a.get("strategy", "unknown")
        strategy_dist[s] = strategy_dist.get(s, 0) + 1

    # ── Models ──────────────────────────────
    model_count = len(active_models)
    model_ages = []
    for m in active_models:
        if m.get("trained_at"):
            try:
                trained = datetime.fromisoformat(m["trained_at"].replace("Z", "+00:00"))
                age = (datetime.now(trained.tzinfo) - trained).days
                model_ages.append(age)
            except Exception:
                pass
    avg_age = sum(model_ages) / len(model_ages) if model_ages else 0
    oldest_age = max(model_ages) if model_ages else 0

    return {
        "week_start": start,
        "week_end": end,
        "predictions": {
            "total": total_preds,
            "verified": len(verified),
            "hits": len(hits),
            "misses": len(misses),
            "unverified": unverified,
            "hit_rate": len(hits) / len(verified) * 100 if verified else 0,
            "by_region": region_stats,
            "by_day": day_stats,
        },
        "profit": {
            "total_cost": total_cost,
            "total_revenue": total_revenue,
            "total_profit": total_profit,
            "roi": (total_profit / total_cost * 100) if total_cost > 0 else 0,
            "hit_pairs": total_hit_pairs,
            "tracked_pairs": total_tracked_pairs,
            "by_region": region_profit,
        },
        "crawler": {
            "total_jobs": total_crawls,
            "success": success_crawls,
            "failed": failed_crawls,
            "success_rate": success_crawls / total_crawls * 100 if total_crawls > 0 else 0,
            "records_inserted": total_records,
            "errors": crawl_errors,
        },
        "agent": {
            "total_actions": total_actions,
            "retrain_count": len(retrain_actions),
            "skip_count": len(skip_actions),
            "no_action_count": len(no_actions),
            "retrain_details": retrain_actions,
            "strategy_distribution": strategy_dist,
        },
        "models": {
            "active_count": model_count,
            "avg_age_days": avg_age,
            "oldest_age_days": oldest_age,
            "models": active_models,
        },
        "training_queue": training_queue,
        "seven_day_performance": _seven_day_performance(
            predictions,
            shadow_predictions,
            start,
            end,
        ),
    }


# ── XML Builder ──────────────────────────────────────────────────────────────

def _build_xml(analysis: dict) -> Element:
    """Build XML ElementTree from analysis dict."""
    root = Element("WeeklyReport")
    root.set("generated_at", datetime.now(timezone.utc).isoformat())

    # ── Meta ──
    meta = SubElement(root, "ReportPeriod")
    SubElement(meta, "WeekStart").text = _iso(analysis["week_start"])
    SubElement(meta, "WeekEnd").text = _iso(analysis["week_end"])
    SubElement(meta, "GeneratedBy").text = "VietlottAI Weekly Report v1.0"

    # ── Predictions Summary ──
    pred = analysis["predictions"]
    pred_el = SubElement(root, "Predictions")
    SubElement(pred_el, "Total").text = str(pred["total"])
    SubElement(pred_el, "Verified").text = str(pred["verified"])
    SubElement(pred_el, "Hits").text = str(pred["hits"])
    SubElement(pred_el, "Misses").text = str(pred["misses"])
    SubElement(pred_el, "Unverified").text = str(pred["unverified"])
    SubElement(pred_el, "HitRatePercent").text = f"{pred['hit_rate']:.1f}"

    # By region
    by_region = SubElement(pred_el, "ByRegion")
    for region, stats in pred["by_region"].items():
        reg_el = SubElement(by_region, "Region", name=region)
        SubElement(reg_el, "Total").text = str(stats["total"])
        SubElement(reg_el, "Hit").text = str(stats["hit"])
        SubElement(reg_el, "Miss").text = str(stats["miss"])
        r_rate = stats["hit"] / (stats["hit"] + stats["miss"]) * 100 if (stats["hit"] + stats["miss"]) > 0 else 0
        SubElement(reg_el, "HitRate").text = f"{r_rate:.1f}"

    # By day
    by_day = SubElement(pred_el, "ByDay")
    for day_str, stats in sorted(pred["by_day"].items()):
        day_el = SubElement(by_day, "Day", date=str(day_str))
        SubElement(day_el, "Total").text = str(stats["total"])
        SubElement(day_el, "Hit").text = str(stats["hit"])
        SubElement(day_el, "Miss").text = str(stats["miss"])

    # ── Canonical seven-day performance ledger ──
    performance = analysis["seven_day_performance"]
    performance_el = SubElement(root, "SevenDayPerformance")
    SubElement(performance_el, "PeriodDays").text = str(performance["period_days"])
    SubElement(performance_el, "Criterion").text = "at_least_2_of_3"
    for scope_key, label in PERFORMANCE_SCOPE_ORDER:
        stats = performance["scopes"][scope_key]
        scope_el = SubElement(
            performance_el,
            "Scope",
            key=scope_key,
            label=label,
        )
        SubElement(scope_el, "HitDays").text = str(stats["hit_days"])
        SubElement(scope_el, "PredictionDays").text = str(stats["prediction_days"])
        SubElement(scope_el, "VerifiedDays").text = str(stats["verified_days"])
        SubElement(scope_el, "PeriodDays").text = str(stats["period_days"])

    # ── Profit ──
    profit = analysis["profit"]
    profit_el = SubElement(root, "Profit")
    SubElement(profit_el, "TotalCostVND").text = str(profit["total_cost"])
    SubElement(profit_el, "TotalRevenueVND").text = str(profit["total_revenue"])
    SubElement(profit_el, "TotalProfitVND").text = str(profit["total_profit"])
    SubElement(profit_el, "ROIPercent").text = f"{profit['roi']:.1f}"
    SubElement(profit_el, "HitPairs").text = str(profit["hit_pairs"])
    SubElement(profit_el, "TrackedPairs").text = str(profit["tracked_pairs"])

    for region, rp in profit["by_region"].items():
        reg_el = SubElement(profit_el, "Region", name=region)
        SubElement(reg_el, "Cost").text = str(rp["cost"])
        SubElement(reg_el, "Revenue").text = str(rp["revenue"])
        SubElement(reg_el, "Profit").text = str(rp["profit"])

    # ── Crawler ──
    crawler = analysis["crawler"]
    crawler_el = SubElement(root, "Crawler")
    SubElement(crawler_el, "TotalJobs").text = str(crawler["total_jobs"])
    SubElement(crawler_el, "Success").text = str(crawler["success"])
    SubElement(crawler_el, "Failed").text = str(crawler["failed"])
    SubElement(crawler_el, "SuccessRatePercent").text = f"{crawler['success_rate']:.1f}"
    SubElement(crawler_el, "RecordsInserted").text = str(crawler["records_inserted"])

    if crawler["errors"]:
        errors_el = SubElement(crawler_el, "Errors")
        for err in crawler["errors"]:
            err_el = SubElement(errors_el, "Error")
            SubElement(err_el, "Date").text = str(err.get("crawl_date", ""))
            SubElement(err_el, "Region").text = str(err.get("region", ""))
            SubElement(err_el, "Message").text = str(err.get("error_message", ""))

    # ── Agent ──
    agent = analysis["agent"]
    agent_el = SubElement(root, "RetrainAgent")
    SubElement(agent_el, "TotalActions").text = str(agent["total_actions"])
    SubElement(agent_el, "RetrainTriggered").text = str(agent["retrain_count"])
    SubElement(agent_el, "Skipped").text = str(agent["skip_count"])
    SubElement(agent_el, "NoAction").text = str(agent["no_action_count"])

    # Strategy distribution
    if agent["strategy_distribution"]:
        strat_el = SubElement(agent_el, "StrategyDistribution")
        for strategy, count in agent["strategy_distribution"].items():
            SubElement(strat_el, "Strategy", name=strategy).text = str(count)

    # Retrain details
    if agent["retrain_details"]:
        details_el = SubElement(agent_el, "RetrainDetails")
        for action in agent["retrain_details"]:
            act_el = SubElement(details_el, "Action")
            SubElement(act_el, "Date").text = str(action.get("action_date", ""))
            SubElement(act_el, "Region").text = str(action.get("region", ""))
            SubElement(act_el, "Province").text = str(action.get("province", ""))
            SubElement(act_el, "Strategy").text = str(action.get("strategy", ""))
            SubElement(act_el, "Reason").text = str(action.get("reason", ""))
            if action.get("old_metric_auc"):
                SubElement(act_el, "OldAUC").text = str(action["old_metric_auc"])
            if action.get("old_hit_rate"):
                SubElement(act_el, "OldHitRate").text = str(action["old_hit_rate"])
            if action.get("old_params"):
                SubElement(act_el, "OldParams").text = str(action["old_params"])
            if action.get("new_params"):
                SubElement(act_el, "NewParams").text = str(action["new_params"])

    # ── Models ──
    models = analysis["models"]
    models_el = SubElement(root, "ActiveModels")
    SubElement(models_el, "Count").text = str(models["active_count"])
    SubElement(models_el, "AvgAgeDays").text = f"{models['avg_age_days']:.0f}"
    SubElement(models_el, "OldestAgeDays").text = str(models["oldest_age_days"])

    for m in models["models"][:20]:  # cap at 20
        m_el = SubElement(models_el, "Model")
        SubElement(m_el, "Region").text = str(m.get("region", ""))
        SubElement(m_el, "Province").text = str(m.get("province", ""))
        SubElement(m_el, "Version").text = str(m.get("version", ""))
        SubElement(m_el, "AUC").text = str(m.get("metric_auc", ""))
        SubElement(m_el, "HitRate").text = str(m.get("metric_hit_rate", ""))
        SubElement(m_el, "TrainedAt").text = str(m.get("trained_at", ""))

    return root


def _save_xml(root: Element, output_dir: str, end_date: date) -> str:
    """Save XML report to file. Returns file path."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"weekly_{end_date.strftime('%Y%m%d')}.xml"
    filepath = os.path.join(output_dir, filename)

    indent(root, space="  ")
    tree = ElementTree(root)
    tree.write(filepath, encoding="unicode", xml_declaration=True)

    print(f"  📄 XML report saved: {filepath}")
    return filepath


# ── Telegram Builder ─────────────────────────────────────────────────────────

def _format_vnd(amount: int) -> str:
    """Format VND amount with K/M suffix."""
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    elif abs(amount) >= 1_000:
        return f"{amount / 1_000:.0f}K"
    return str(amount)


def _build_telegram_message(analysis: dict) -> str:
    """Build concise Telegram summary from analysis."""
    start = analysis["week_start"]
    end = analysis["week_end"]
    profit = analysis["profit"]
    crawler = analysis["crawler"]
    agent = analysis["agent"]
    models = analysis["models"]
    performance = analysis["seven_day_performance"]

    msg = (
        f"📊 <b>BÁO CÁO TUẦN — {_fmt(start)} → {_fmt(end)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    # ── 1. Canonical daily model performance ──
    period_days = performance["period_days"]
    msg += f"🎯 <b>HIỆU QUẢ {period_days} NGÀY — đạt khi ≥2/3</b>\n"
    for scope_key, label in PERFORMANCE_SCOPE_ORDER:
        stats = performance["scopes"][scope_key]
        if stats["verified_days"] == 0:
            icon = "⚪"
        elif stats["hit_days"] == 0:
            icon = "⚪" if scope_key in {"ddt", "cmr"} else "🔴"
        elif stats["hit_days"] / max(period_days, 1) >= 0.3:
            icon = "🟢"
        else:
            icon = "🟡"
        line = (
            f"{icon} {label}: "
            f"<b>{stats['hit_days']}/{period_days} ngày trúng</b>"
        )
        if scope_key in {"ddt", "cmr"} or stats["prediction_days"] != period_days:
            line += f" · chạy {stats['prediction_days']}/{period_days}"
        line += f" · verify {stats['verified_days']}/{period_days}\n"
        msg += line
    msg += "\n"

    # ── 2. Profit ──
    profit_icon = "💰" if profit["total_profit"] > 0 else "📉"
    msg += (
        f"{profit_icon} <b>TÀI CHÍNH</b>\n"
        f"   Chi: <code>{_format_vnd(profit['total_cost'])}</code> | "
        f"Thu: <code>{_format_vnd(profit['total_revenue'])}</code>\n"
        f"   Lợi nhuận: <b>{_format_vnd(profit['total_profit'])}</b> "
        f"(ROI: <code>{profit['roi']:.0f}%</code>)\n"
    )

    for region, rp in profit["by_region"].items():
        p_icon = "📈" if rp["profit"] > 0 else "📉"
        msg += f"   {p_icon} {region}: {_format_vnd(rp['profit'])}\n"

    msg += "\n"

    # ── 3. Crawler ──
    c_icon = "🟢" if crawler["success_rate"] >= 95 else "🟡" if crawler["success_rate"] >= 80 else "🔴"
    msg += (
        f"🕷️ <b>CRAWLER</b>\n"
        f"   {c_icon} {crawler['success']}/{crawler['total_jobs']} thành công "
        f"({crawler['success_rate']:.0f}%)\n"
        f"   Records: <code>{crawler['records_inserted']}</code>\n\n"
    )

    # ── 4. Agent ──
    msg += f"🤖 <b>RETRAIN AGENT</b>\n"
    if agent["retrain_count"] > 0:
        msg += f"   🔁 Retrain: <code>{agent['retrain_count']}</code> lần\n"
        for strategy, count in agent["strategy_distribution"].items():
            msg += f"      • <code>{strategy}</code>: {count}x\n"
    else:
        msg += f"   ✅ Không cần retrain\n"

    if agent["skip_count"] > 0:
        msg += f"   ⏭️ Bỏ qua: <code>{agent['skip_count']}</code> (cooldown/metric OK)\n"
    msg += "\n"

    # ── 5. Models ──
    msg += (
        f"🧠 <b>MODELS</b>\n"
        f"   Active: <code>{models['active_count']}</code> | "
        f"Tuổi TB: <code>{models['avg_age_days']:.0f}</code> ngày | "
        f"Cũ nhất: <code>{models['oldest_age_days']}</code> ngày\n\n"
    )

    # ── Footer ──
    msg += (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>📄 Chi tiết XML: reports/weekly_{end.strftime('%Y%m%d')}.xml</i>"
    )

    return msg


# ── Main ─────────────────────────────────────────────────────────────────────

async def generate_weekly_report(
    db: LotteryDB,
    notifier: LotteryNotifier,
    week_end: date,
    output_dir: str = "reports",
) -> str:
    """Generate full weekly report: XML file + Telegram summary.

    Args:
        db: LotteryDB instance
        notifier: LotteryNotifier instance
        week_end: Sunday that ends the reporting week
        output_dir: directory to save XML files

    Returns:
        Path to generated XML file
    """
    start, end = _week_range(week_end)
    print(f"\n📊 Generating weekly report: {_fmt(start)} → {_fmt(end)}")

    # ── Collect data ──
    print("  📥 Collecting predictions...")
    predictions = _collect_predictions(db, start, end)
    print(f"     → {len(predictions)} records")

    print("  📥 Collecting profit data...")
    profits = _collect_profit(db, start, end)
    print(f"     → {len(profits)} records")

    print("  📥 Collecting DDT/CMR shadow predictions...")
    shadow_predictions = _collect_shadow_predictions(db, start, end)
    print(f"     → {len(shadow_predictions)} records")

    print("  📥 Collecting crawler logs...")
    crawler_logs = _collect_crawler_logs(db, start, end)
    print(f"     → {len(crawler_logs)} records")

    print("  📥 Collecting agent actions...")
    agent_actions = _collect_agent_actions(db, start, end)
    print(f"     → {len(agent_actions)} records")

    print("  📥 Collecting active models...")
    active_models = _collect_active_models(db)
    print(f"     → {len(active_models)} models")

    print("  📥 Collecting training queue...")
    training_queue = _collect_training_queue(db, start, end)
    print(f"     → {len(training_queue)} entries")

    # ── Analyze ──
    print("  🔍 Analyzing data...")
    analysis = _analyze(
        predictions, profits, crawler_logs, agent_actions,
        active_models, training_queue, start, end, shadow_predictions,
    )

    # ── Generate XML ──
    print("  📄 Building XML report...")
    xml_root = _build_xml(analysis)
    xml_path = _save_xml(xml_root, output_dir, end)

    # ── Send Telegram ──
    print("  📱 Sending Telegram summary...")
    telegram_msg = _build_telegram_message(analysis)
    await notifier.send_message(telegram_msg)

    # Print plaintext for CI logs
    plain = telegram_msg
    for tag in ["<b>", "</b>", "<i>", "</i>", "<code>", "</code>"]:
        plain = plain.replace(tag, "")
    print(f"\n{plain}")

    print(f"\n✅ Weekly report complete!")
    return xml_path


async def main():
    parser = argparse.ArgumentParser(description="VietlottAI Weekly System Report")
    parser.add_argument(
        "--week-end", type=str,
        help="Ngày Chủ nhật kết thúc tuần (YYYY-MM-DD). Mặc định = Chủ nhật gần nhất",
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Thư mục lưu XML report (default: reports/)",
    )
    args = parser.parse_args()

    if args.week_end:
        week_end = date.fromisoformat(args.week_end)
    else:
        # VN timezone: UTC+7
        vn_now = datetime.utcnow() + timedelta(hours=7)
        today = vn_now.date()
        # Default: last Sunday (or today if today is Sunday)
        if today.weekday() == 6:
            week_end = today
        else:
            week_end = today - timedelta(days=(today.weekday() + 1) % 7)

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="weekly_report")
    await generate_weekly_report(db, notifier, week_end, args.output_dir)


if __name__ == "__main__":
    asyncio.run(main())
