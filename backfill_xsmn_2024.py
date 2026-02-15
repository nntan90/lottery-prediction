"""
Backfill XSMN Data (2024-01-01 to Present)
"""

import time
from datetime import date, timedelta, datetime
from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.supabase_client import LotteryDB

def backfill_xsmn():
    print("=" * 60)
    print("🚀 STARTING XSMN BACKFILL (2024-01-01 -> Present)")
    print("=" * 60)
    
    crawler = XSMNCrawler()
    db = LotteryDB()
    
    start_date = date(2024, 1, 1)
    # Set end date explicitly to today (UTC+7)
    vn_now = datetime.utcnow() + timedelta(hours=7)
    end_date = vn_now.date() 
    
    current_date = start_date
    total_days = (end_date - start_date).days + 1
    
    success_count = 0
    fail_count = 0
    
    while current_date <= end_date:
        day_idx = (current_date - start_date).days + 1
        print(f"\n📅 [{day_idx}/{total_days}] Processing: {current_date}")
        
        # Get correct provinces for this day
        provinces = crawler.get_provinces_for_date(current_date)
        print(f"   Target provinces: {provinces}")
        
        for province in provinces:
            try:
                # Crawl
                results = crawler.fetch_results(current_date, province=province)
                
                if results:
                    # Upsert to DB
                    db.upsert_draw(results)
                    success_count += 1
                    print(f"   ✅ {province}: Inserted successfully")
                else:
                    fail_count += 1
                    print(f"   ⚠️ {province}: No data found")
                
                # Small delay between provinces
                time.sleep(0.5)
                
            except Exception as e:
                fail_count += 1
                print(f"   ❌ {province} Error: {e}")
        
        # Move to next day
        current_date += timedelta(days=1)
        
        # Delay between days to be nice to the server
        time.sleep(1)

    print("\n" + "=" * 60)
    print("🎉 BACKFILL COMPLETED!")
    print(f"✅ Total Success: {success_count}")
    print(f"❌ Total Failed: {fail_count}")
    print("=" * 60)

if __name__ == "__main__":
    backfill_xsmn()
