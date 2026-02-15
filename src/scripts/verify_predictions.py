"""
Verify Predictions Script
Runs after daily crawl to check if we won.
"""

import asyncio
import os
from datetime import datetime, timedelta, date

from src.database.supabase_client import LotteryDB
from src.utils.verification import verify_prediction
from src.bot.telegram_bot import LotteryNotifier

async def verify_daily_results():
    db = LotteryDB()
    notifier = LotteryNotifier()
    
    # Verify for TODAY (or specifically the crawl date)
    # Crawl runs at 19:00, so we check "today"
    # But strictly speaking, we should check the result date
    
    # Use Vietnam Time
    vn_now = datetime.utcnow() + timedelta(hours=7)
    check_date = vn_now.date()
    
    print(f"🕵️‍♀️ Verifying predictions for {check_date}...")
    
    regions = ['XSMB', 'XSMN']
    results_report = []
    
    for region in regions:
        # 1. Get Draw Result
        # For XSMN we need to check each province
        # But `db.get_draw_by_date` fetches single record. 
        # For XSMN, we need to iterate all provinces that have results today.
        
        # Helper to fetch all results for a date/region?
        # Our current client `get_draw_by_date` is specific.
        # Let's fetch all predictions for today first, then verify each.
        
        # 2. Get Predictions for today
        try:
            # We need to query predictions table again. 
            # Client `get_prediction_by_date` returns ONE.
            # We need ALL (especially for XSMN multiple provinces).
             query = db.supabase.table("predictions")\
                .select("*")\
                .eq("prediction_date", check_date.isoformat())\
                .eq("region", region)\
                .execute()
                
             predictions = query.data
        except Exception as e:
            print(f"❌ Error fetching predictions: {e}")
            continue

        if not predictions:
            print(f"⚠️ No predictions found for {region} on {check_date}")
            continue
            
        print(f"🔍 Found {len(predictions)} predictions for {region}")
        
        for pred in predictions:
            province = pred.get('province')
            
            # Get Verification Result from DB
            # We need the actual draw result for this province
            draw = db.get_draw_by_date(check_date, region, province)
            
            if not draw:
                print(f"⚠️ No draw result found for {region}-{province}")
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

    # Send Report
    if results_report:
        msg = f"🏆 <b>DAILY RESULT VERIFICATION ({check_date})</b>\n\n" + "\n".join(results_report)
        await notifier.send_message(msg)
    else:
        # Optional: Send "No wins" message? Or just silent.
        # User might want to know verification ran.
        print("No wins detected today.")
        # await notifier.send_message(f"📉 No wins detected for {check_date}")

if __name__ == "__main__":
    asyncio.run(verify_daily_results())
