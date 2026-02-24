"""
report_profit.py
Generate a profit and loss report for a given date range.

Usage:
  python src/scripts/report_profit.py --from-date DD-MM-YYYY --to-date DD-MM-YYYY
"""

import argparse
import asyncio
import sys
import os
import csv
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler

# Constants for Profit Calculation
XSMN_TIER_POINTS = [3, 2, 2] # pair_1: 3, pair_2: 2, pair_3: 2
XSMN_COST_PER_POINT = 14000
XSMN_REVENUE_PER_HIT_POINT = 70000

XSMB_TIER_POINTS = [2, 1, 1] # pair_1: 2, pair_2: 1, pair_3: 1
XSMB_COST_PER_POINT = 23000
XSMB_REVENUE_PER_HIT_POINT = 80000
XSMB_REV_REMAINING = 57000

XSMN_REV_REMAINING = 56000

async def generate_report(db: LotteryDB, notifier: LotteryNotifier, from_date: date, to_date: date):
    print(f"\n📊 Generating Profit Report from {from_date.strftime('%d-%m-%Y')} to {to_date.strftime('%d-%m-%Y')}...\n")

    # Fetch pre-calculated profit data
    profits = db.supabase.table("profit_tracking")\
        .select("*")\
        .gte("prediction_date", from_date.isoformat())\
        .lte("prediction_date", to_date.isoformat())\
        .order("prediction_date")\
        .execute().data

    if not profits:
        print(f"⚠️ No profit tracking data found in this date range.")
        return

    total_cost_overall = 0
    total_rev_overall = 0
    total_profit_overall = 0

    report_lines = []
    csv_data = [["Ngày", "Vùng", "Đài", "Vốn (VNĐ)", "Thu (VNĐ)", "Lợi nhuận (VNĐ)", "Chi tiết trúng"]]
    
    current_date = None
    daily_cost = 0
    daily_rev = 0

    for row in profits:
        p_date = row["prediction_date"]
        region = row["region"]
        province = row["province"]
        cost = row["total_cost"]
        rev = row["total_revenue"]
        prof = row["profit"]
        details = row.get("details", {}) or {}

        if p_date != current_date:
            if current_date is not None:
                d_prof = daily_rev - daily_cost
                sign = "🟢" if d_prof >= 0 else "🔴"
                report_lines.append(f"  └ <i>Lợi nhuận ngày:</i> {sign} {d_prof:,.0f} đ\n")
            current_date = p_date
            daily_cost = 0
            daily_rev = 0
            # format date for display
            d_obj = datetime.fromisoformat(p_date).date()
            report_lines.append(f"📅 <b>{d_obj.strftime('%d/%m/%Y')}</b>")

        daily_cost += cost
        daily_rev += rev
        total_cost_overall += cost
        total_rev_overall += rev
        total_profit_overall += prof

        lbl = XSMNCrawler().PROVINCE_MAP.get(province, province.upper() if province != 'all' else region.upper())
        status_icon = "✅" if rev > 0 else "❌"
        
        # Format matched info for reporting
        matched_details = []
        for pair, count in details.items():
            matched_details.append(f"{pair}({count} nháy)")
        
        match_str = ", ".join(matched_details) if matched_details else "—"
        
        # Append to CSV
        csv_data.append([
            d_obj.strftime("%d/%m/%Y"), 
            region.upper(), 
            lbl, 
            cost, 
            rev, 
            prof, 
            match_str
        ])
        
        # e.g., ✅ TPHCM: +56,000 [Trúng: 10(2 nháy)]
        report_lines.append(f" {status_icon} {lbl}: {prof:+,.0f} đ [Trúng: {match_str}]")

    # Print last day summary
    if current_date is not None:
        d_prof = daily_rev - daily_cost
        sign = "🟢" if d_prof >= 0 else "🔴"
        report_lines.append(f"  └ <i>Lợi nhuận ngày:</i> {sign} {d_prof:,.0f} đ\n")

    sign = "🟢 TỔNG LÃI" if total_profit_overall >= 0 else "🔴 TỔNG LỖ"
    
    summary_msg = (
        f"📊 <b>BÁO CÁO TÀI CHÍNH</b>\n"
        f"Từ <b>{from_date.strftime('%d/%m/%Y')}</b> đến <b>{to_date.strftime('%d/%m/%Y')}</b>\n\n"
    )
    
    # We might need to split message if it's too long for Telegram (usually 4096 chars limit)
    # But usually a month of data is fine if kept concise.
    details_msg = "\n".join(report_lines)
    
    footer_msg = (
        f"{"="*20}\n"
        f"💰 <b>Tổng vốn:</b> {total_cost_overall:,.0f} đ\n"
        f"💵 <b>Tổng thu:</b> {total_rev_overall:,.0f} đ\n"
        f"{sign}: <b>{abs(total_profit_overall):,.0f} đ</b>"
    )

    full_msg = summary_msg + details_msg + "\n" + footer_msg
    
    print(full_msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))

    # Save to CSV
    csv_filename = f"profit_report_{from_date.strftime('%Y%m%d')}_{to_date.strftime('%Y%m%d')}.csv"
    with open(csv_filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerows(csv_data)
        # Add summary row at bottom
        writer.writerow([])
        writer.writerow(["TỔNG CỘNG", "", "", total_cost_overall, total_rev_overall, total_profit_overall, sign])
    print(f"\n💾 Đã lưu báo cáo chi tiết vào file: {csv_filename}")

    # Logic to chunk message to avoid Telegram size limits
    max_len = 4000
    if len(full_msg) > max_len:
        # Send summary first, then chunk details
        await notifier.send_message(summary_msg)
        
        chunk = ""
        for line in report_lines:
            if len(chunk) + len(line) > max_len:
                await notifier.send_message(chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk:
            await notifier.send_message(chunk)
            
        await notifier.send_message(footer_msg)
    else:
        await notifier.send_message(full_msg)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", type=str, required=True, help="Từ ngày (DD-MM-YYYY)")
    parser.add_argument("--to-date", type=str, required=True, help="Đến ngày (DD-MM-YYYY)")
    args = parser.parse_args()

    try:
        from_date = datetime.strptime(args.from_date, "%d-%m-%Y").date()
        to_date = datetime.strptime(args.to_date, "%d-%m-%Y").date()
    except ValueError:
        print("Lỗi: Định dạng ngày phải là DD-MM-YYYY")
        sys.exit(1)

    if from_date > to_date:
        print("Lỗi: from-date phải nhỏ hơn hoặc bằng to-date")
        sys.exit(1)

    db = LotteryDB()
    notifier = LotteryNotifier()
    await generate_report(db, notifier, from_date, to_date)


if __name__ == "__main__":
    asyncio.run(main())
