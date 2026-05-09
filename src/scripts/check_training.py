"""
check_training.py
Đánh giá điều kiện train lại model cho mỗi station.
Chạy mỗi Chủ Nhật 21:00 VN từ 04-check-training.yml.

Điều kiện trigger (A OR B OR C):
  A: new_draws >= 50 AND new_draws >= 0.2 * train_draws
  B: hit_rate_recent <= hit_rate_train - 0.05
  C: manual_request = true (bản ghi trong training_queue)
"""

import asyncio
import os
import subprocess
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier

RECENT_WINDOW = 30   # số kỳ gần nhất để tính hit_rate_recent
PERF_DELTA    = 0.05 # ngưỡng drop cho phép (5%)
MIN_NEW_DRAWS = 50   # số kỳ mới tối thiểu để trigger group A
MIN_NEW_RATIO = 0.20 # tỉ lệ so với train_draws để trigger group A


def get_recent_hit_rate(db: LotteryDB, region: str, province: str | None, window: int = 30) -> float | None:
    """Tính hit_rate của N kỳ gần nhất từ prediction_results."""
    query = db.supabase.table("prediction_results")\
        .select("hit")\
        .eq("region", region)\
        .not_.is_("hit", "null")\
        .order("prediction_date", desc=True)\
        .limit(window)

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    rows = query.execute().data
    if not rows:
        return None
    hits = sum(1 for r in rows if r["hit"])
    return hits / len(rows)


def count_new_draws(db: LotteryDB, region: str, province: str | None, since_date: date) -> int:
    """Đếm số kỳ mới trong lottery_draws kể từ since_date."""
    query = db.supabase.table("lottery_draws")\
        .select("id", count="exact")\
        .eq("region", region)\
        .gt("draw_date", since_date.isoformat())

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    result = query.execute()
    return result.count if result.count else 0


def has_manual_request(db: LotteryDB, region: str, province: str | None) -> bool:
    """Kiểm tra có bản ghi manual pending hay không."""
    query = db.supabase.table("training_queue")\
        .select("id")\
        .eq("region", region)\
        .eq("trigger_reason", "manual")\
        .eq("status", "pending")

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    return len(query.execute().data) > 0


def trigger_training(region: str, province: str | None, weekday: int | None = None):
    """Trigger 05-train-model.yml qua gh CLI."""
    prov_arg = province if province else "all"
    cmd = [
        "gh", "workflow", "run", "05-train-model.yml",
        "-f", f"region={region}",
        "-f", f"province={prov_arg}",
    ]
    if weekday is not None:
        cmd += ["-f", f"weekday={weekday}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            wd_label = f" [wd={weekday}]" if weekday is not None else ""
            print(f"  ✅ Triggered 05-train-model for {region}/{prov_arg}{wd_label}")
            return True
        else:
            print(f"  ❌ gh workflow run failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"  ❌ Exception triggering workflow: {e}")
        return False


async def main():
    db = LotteryDB()
    notifier = LotteryNotifier()

    # Lấy tất cả model active (bao gồm cả weekday)
    models = db.supabase.table("model_registry")\
        .select("region,province,weekday,version,train_end_date,train_draws,metric_hit_rate")\
        .eq("status", "active")\
        .execute().data

    if not models:
        print("⚠️ Không có model active trong registry.")
        return

    triggered_list = []

    for m in models:
        region   = m["region"]
        province = m["province"]
        weekday  = m.get("weekday")  # None = legacy model (not weekday-specific)
        DOW_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
        wd_label = f" [{DOW_NAMES[weekday]}]" if weekday is not None else ""
        label    = f"{region}/{province or 'all'}{wd_label}"

        train_draws     = m.get("train_draws") or 0
        hit_rate_train  = m.get("metric_hit_rate") or 0.0
        train_end       = date.fromisoformat(m["train_end_date"]) if m.get("train_end_date") else date.today() - timedelta(days=90)

        new_draws       = count_new_draws(db, region, province, train_end)
        hit_rate_recent = get_recent_hit_rate(db, region, province, RECENT_WINDOW)
        manual_req      = has_manual_request(db, region, province)

        # Điều kiện
        group_a = (new_draws >= MIN_NEW_DRAWS and new_draws >= MIN_NEW_RATIO * train_draws)
        group_b = (hit_rate_recent is not None and hit_rate_recent <= hit_rate_train - PERF_DELTA)
        group_c = manual_req

        print(f"\n📊 {label}")
        recent_str = f"{hit_rate_recent:.2%}" if hit_rate_recent is not None else "N/A"
        print(f"  new_draws={new_draws} | train_draws={train_draws} | hit_train={hit_rate_train:.2%} | hit_recent={recent_str}")
        print(f"  Group A={group_a} | B={group_b} | C={group_c}")

        if not (group_a or group_b or group_c):
            print(f"  ⏩ Skip — điều kiện chưa thỏa")
            continue

        # Xác định trigger_reason
        reason = "new_data" if group_a else ("perf_drop" if group_b else "manual")

        # Insert training_queue (if not already pending/triggered)
        tq_query = db.supabase.table("training_queue")\
            .select("id")\
            .eq("region", region)\
            .in_("status", ["pending", "triggered"])
        if province:
            tq_query = tq_query.eq("province", province)
        else:
            tq_query = tq_query.is_("province", "null")
        existing = tq_query.execute().data

        if not existing:
            db.supabase.table("training_queue").insert({
                "region":          region,
                "province":        province,
                "trigger_reason":  reason,
                "new_draws":       new_draws,
                "train_draws":     train_draws,
                "hit_rate_train":  hit_rate_train,
                "hit_rate_recent": hit_rate_recent,
                "status":          "triggered",
                "notified_at":     "now()",
            }).execute()

        # Trigger workflow (trưyền thêm weekday nếu có)
        ok = trigger_training(region, province, weekday)
        if not ok:
            continue

        triggered_list.append({
            "label":  label,
            "reason": reason,
            "new_draws": new_draws,
            "hit_train": hit_rate_train,
            "hit_recent": hit_rate_recent,
        })

    # Telegram summary
    if triggered_list:
        msg = f"🔔 <b>Auto Training Triggered — {date.today()}</b>\n\n"
        for t in triggered_list:
            reason_icon = {"new_data": "📦", "perf_drop": "📉", "manual": "👤"}.get(t["reason"], "🔔")
            recent_str = f"{t['hit_recent']:.0%}" if t['hit_recent'] is not None else "N/A"
            msg += (
                f"{reason_icon} <b>{t['label']}</b> — {t['reason']}\n"
                f"   Kỳ mới: {t['new_draws']} | "
                f"Hit: {t['hit_train']:.0%} → {recent_str}\n\n"
            )
        msg += "⏳ Training đang chạy trên GitHub Actions..."
        await notifier.send_message(msg)
    else:
        print("\nℹ️ Không có model nào cần train lại.")


if __name__ == "__main__":
    asyncio.run(main())
