"""
Verify Predictions Script
Runs after daily crawl to check if we won.
Supports checking a specific date via CLI args.
"""

import asyncio
import os
import argparse
from datetime import datetime, timedelta, date

from src.database.supabase_client import LotteryDB
from src.utils.verification import verify_prediction
from src.bot.telegram_bot import LotteryNotifier

async def verify_daily_results(check_date: date = None):
    db = LotteryDB()
    notifier = LotteryNotifier()
    
    # 1. Determine Date
    if not check_date:
        # Use Vietnam Time (UTC+7)
        vn_now = datetime.utcnow() + timedelta(hours=7)
        check_date = vn_now.date()
        print(f"🕒 Auto-detected date (Vietnam Time): {check_date}")
    else:
        print(f"🕒 Using specific date: {check_date}")
        
    print(f"🕵️‍♀️ Verifying predictions for {check_date}...")
    
    regions = ['XSMB', 'XSMN']
    results_report = []
    has_any_prediction = False
    
    for region in regions:
        # 2. Get Predictions for date
        try:
             query = db.supabase.table("predictions")\
                .select("*")\
                .eq("prediction_date", check_date.isoformat())\
                .eq("region", region)\
                .execute()
                
             predictions = query.data
        except Exception as e:
            print(f"❌ Error fetching predictions for {region}: {e}")
            continue

        if not predictions:
            print(f"⚠️ No predictions found for {region} on {check_date}")
            # If explicit run, maybe notify? For now, just log.
            # Update: User wants notification on failure to find prediction?
            # actually the error "No prediction found" came from somewhere else.
            # Let's add a note to the report if verified explicitly.
            continue
            
        has_any_prediction = True
        print(f"🔍 Found {len(predictions)} predictions for {region}")
        
        for pred in predictions:
            province = pred.get('province')
            
            # Get Verification Result from DB
            draw = db.get_draw_by_date(check_date, region, province)
            
            if not draw:
                print(f"⚠️ No draw result found for {region}-{province} on {check_date}")
                continue
                
            # VERIFY!
            verification = verify_prediction(pred, draw)
            
            is_correct = verification['is_correct']
            win_info = verification['win_prize']
            
            # Update Prediction in DB
            try:
                db.supabase.table("predictions").update({
                    "is_correct": is_correct,
                    "win_prize": win_info,
                    "check_time": datetime.now().isoformat()
                }).eq("id", pred['id']).execute()
                
                status_icon = "🎉" if is_correct else "❌"
                print(f"   {status_icon} {region}-{province}: Correct={is_correct}")
                
                if is_correct:
                    count = win_info['count']
                    matches = ", ".join(win_info['matches'])
                    results_report.append(f"🎉 <b>{region} - {province or ''}</b>: WIN x{count} ({matches})")
                
            except Exception as e:
                print(f"❌ Error updating prediction verify status: {e}")

    # Send Result Report
    msg_header = f"🏆 <b>KẾT QUẢ DỰ ĐOÁN ({check_date})</b>\n\n"
    
    
    if results_report:
        msg_body = "\n".join(results_report)
        await notifier.send_message(msg_header + msg_body)
        print("✅ Sent success report to Telegram")
    elif not has_any_prediction:
         print("⚠️ No predictions found to verify.")
         # Optional: Send a message saying "No predictions to verify"
         msg_body = "⚠️ Không tìm thấy dữ liệu dự đoán cho ngày hôm nay."
         await notifier.send_message(f"{msg_header}{msg_body}")
    else:
        # No wins but we HAD predictions
        print("📉 No wins detected today.")
        msg_body = "📉 Không có dự đoán nào chính xác hôm nay."
        await notifier.send_message(f"{msg_header}{msg_body}")
        print("✅ Sent 'No win' report to Telegram")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Verify daily lottery predictions')
    parser.add_argument('--date', type=str, help='Date to verify (YYYY-MM-DD)')
    args = parser.parse_args()
    
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, '%Y-%m-%d').date()
        except ValueError:
            print("❌ Invalid date format. Use YYYY-MM-DD")
            exit(1)
            
    asyncio.run(verify_daily_results(target_date))
