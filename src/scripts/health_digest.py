"""
health_digest.py — Daily Health Digest Reporter
Gửi bản tổng hợp hàng ngày về trạng thái toàn bộ pipeline qua Telegram.

Kiểm tra:
  1. Crawler logs: XSMB + XSMN đã crawl thành công chưa?
  2. Prediction results: có prediction cho ngày hôm nay không?
  3. Verification status: các prediction có được verify chưa?
  4. Model registry: model nào đang active, trained bao lâu rồi?
  5. Error rate: tỉ lệ lỗi crawl 7 ngày gần nhất

Usage:
  python src/scripts/health_digest.py
  python src/scripts/health_digest.py --date 2026-05-10
"""

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.xsmn_ensemble.resolve_provinces import get_target_provinces


def summarize_xsmn_model_runs(rows: list[dict], target_provinces: list[str]) -> dict:
    """Summarize the expected two-province by six-model execution matrix."""
    expected = len(target_provinces) * 6
    successful = {
        (row.get("province"), row.get("model_name"))
        for row in rows
        if row.get("status") == "success" and row.get("province") in target_provinces
    }
    missing_provinces = sorted(
        province
        for province in target_provinces
        if not any(item[0] == province for item in successful)
    )
    return {
        "expected": expected,
        "successful": len(successful),
        "missing_provinces": missing_provinces,
    }


def resolve_default_target_date(vn_now: datetime | None = None) -> date:
    """
    Resolve the operational date for the digest.

    GitHub scheduled workflows can be delayed. If the 23:00 VN digest slips past
    midnight, the calendar date has changed but the operational day to report is
    still the day that just ended. Before 06:00 VN, default to yesterday.
    """
    vn_now = vn_now or (datetime.utcnow() + timedelta(hours=7))
    if vn_now.hour < 6:
        return (vn_now.date() - timedelta(days=1))
    return vn_now.date()


async def build_digest(db: LotteryDB, target_date: date) -> str:
    """Build comprehensive health digest message."""
    date_str = target_date.strftime("%d/%m/%Y")
    sections = []
    warnings = []
    
    # ── 1. Crawler Status ──────────────────────────────────────────
    crawler_logs = db.supabase.table("crawler_logs") \
        .select("region,status,records_inserted,error_message") \
        .eq("crawl_date", target_date.isoformat()) \
        .execute().data

    xsmb_status = "❓ Không có log"
    xsmn_status = "❓ Không có log"
    
    for log in crawler_logs:
        icon = "✅" if log["status"] == "success" else "❌"
        records = log.get("records_inserted", 0)
        if log["region"] == "XSMB":
            xsmb_status = f"{icon} {records} record(s)"
            if log["status"] != "success":
                warnings.append(f"XSMB crawl failed: {log.get('error_message', 'unknown')}")
        elif log["region"] == "XSMN":
            xsmn_status = f"{icon} {records} record(s)"
            if log["status"] != "success":
                warnings.append(f"XSMN crawl failed: {log.get('error_message', 'unknown')}")

    if not crawler_logs:
        warnings.append("Không có crawler log nào cho ngày hôm nay!")

    sections.append(
        f"🕷️ <b>CRAWL</b>\n"
        f"   XSMB: {xsmb_status}\n"
        f"   XSMN: {xsmn_status}"
    )

    # ── 2. Prediction Status ───────────────────────────────────────
    predictions = db.supabase.table("prediction_results") \
        .select("region,province,pair_1,pair_2,pair_3,hit,model_version") \
        .eq("prediction_date", target_date.isoformat()) \
        .execute().data

    pred_lines = []
    verified_count = 0
    total_preds = len(predictions)
    hit_count = 0

    for p in predictions:
        region = p["region"]
        prov = p.get("province") or "all"
        pairs = f"{p['pair_1']:02d},{p['pair_2']:02d},{p['pair_3']:02d}"
        
        if p["hit"] is not None:
            verified_count += 1
            status = "✅" if p["hit"] else "❌"
            if p["hit"]:
                hit_count += 1
        else:
            status = "⏳"
        
        pred_lines.append(f"   {status} {region}/{prov}: [{pairs}] ({p.get('model_version', '?')})")

    if not predictions:
        warnings.append("Không có prediction nào cho ngày hôm nay!")
        pred_summary = "   ⚠️ Không có prediction"
    else:
        pred_summary = "\n".join(pred_lines)

    verify_status = f"{verified_count}/{total_preds} verified"
    if total_preds > 0 and verified_count > 0:
        hit_rate = hit_count / verified_count * 100
        verify_status += f" | {hit_count} hit ({hit_rate:.0f}%)"

    sections.append(
        f"🎯 <b>PREDICTIONS</b> ({verify_status})\n{pred_summary}"
    )

    xsmn_model_logs = db.supabase.table("model_predictions") \
        .select("province,model_name,status") \
        .eq("prediction_date", target_date.isoformat()) \
        .eq("region", "XSMN") \
        .execute().data or []
    xsmn_runs = summarize_xsmn_model_runs(
        xsmn_model_logs, get_target_provinces(target_date)
    )
    if xsmn_runs["successful"] < xsmn_runs["expected"]:
        warnings.append(
            "XSMN model runs incomplete: "
            f"{xsmn_runs['successful']}/{xsmn_runs['expected']} successful"
        )
    if xsmn_runs["missing_provinces"]:
        warnings.append(
            "XSMN missing model output for: "
            + ", ".join(xsmn_runs["missing_provinces"])
        )
    sections.append(
        "🤖 <b>XSMN MODEL RUNS</b>\n"
        f"   {xsmn_runs['successful']}/{xsmn_runs['expected']} successful"
    )

    # ── 3. Model Registry ─────────────────────────────────────────
    active_models = db.supabase.table("model_registry") \
        .select("region,province,weekday,version,metric_auc,metric_hit_rate,trained_at") \
        .eq("status", "active") \
        .order("trained_at", desc=True) \
        .limit(50) \
        .execute().data

    model_lines = []
    for m in active_models:
        region = m["region"]
        prov = m.get("province") or "all"
        wd = f"[wd{m['weekday']}]" if m.get("weekday") is not None else ""
        auc = f"AUC={m['metric_auc']:.3f}" if m.get("metric_auc") else "AUC=?"
        
        # Days since trained
        days_old = None
        if m.get("trained_at"):
            try:
                trained = datetime.fromisoformat(m["trained_at"].replace("Z", "+00:00"))
                now = datetime.now(trained.tzinfo) if trained.tzinfo else datetime.utcnow()
                days_old = (now - trained).days
                age = f"{days_old}d ago"
            except Exception:
                age = "?"
        else:
            age = "?"
        
        model_lines.append(f"   {region}/{prov}{wd}: {m['version']} ({auc}, {age})")
        
        # Warn on old models
        if isinstance(days_old, int) and days_old > 30:
            warnings.append(f"Model {region}/{prov}{wd} trained {days_old} days ago — consider retraining")

    sections.append(
        f"🧠 <b>ACTIVE MODELS</b> ({len(active_models)})\n" + "\n".join(model_lines[:5])
    )

    # ── 4. 7-Day Crawl Error Rate ─────────────────────────────────
    week_ago = (target_date - timedelta(days=7)).isoformat()
    week_logs = db.supabase.table("crawler_logs") \
        .select("status") \
        .gte("crawl_date", week_ago) \
        .lte("crawl_date", target_date.isoformat()) \
        .execute().data

    total_crawls = len(week_logs)
    failed_crawls = sum(1 for l in week_logs if l["status"] != "success")
    error_rate = (failed_crawls / total_crawls * 100) if total_crawls > 0 else 0

    if error_rate > 20:
        warnings.append(f"Crawl error rate {error_rate:.0f}% in last 7 days (>{20}% threshold)")

    sections.append(
        f"📊 <b>7-DAY HEALTH</b>\n"
        f"   Crawl jobs: {total_crawls - failed_crawls}/{total_crawls} success ({100-error_rate:.0f}%)"
    )

    # ── Build Final Message ───────────────────────────────────────
    msg = f"📋 <b>DAILY HEALTH DIGEST — {date_str}</b>\n\n"
    msg += "\n\n".join(sections)

    if warnings:
        msg += "\n\n⚠️ <b>WARNINGS</b>\n"
        for w in warnings:
            msg += f"   • {w}\n"
    else:
        msg += "\n\n✅ <i>All systems nominal</i>"

    return msg


async def main():
    parser = argparse.ArgumentParser(description="Daily Health Digest")
    parser.add_argument("--date", type=str, help="Date to check (YYYY-MM-DD). Default = today")
    args = parser.parse_args()

    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        target_date = resolve_default_target_date()

    db = LotteryDB()
    notifier = LotteryNotifier(db, default_config_key="health_digest")

    print(f"📋 Building health digest for {target_date}...")
    msg = await build_digest(db, target_date)
    
    print(msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    await notifier.send_message(msg)
    print("\n📱 Health digest sent to Telegram!")


if __name__ == "__main__":
    asyncio.run(main())
