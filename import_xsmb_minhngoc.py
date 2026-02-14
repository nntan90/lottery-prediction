"""
XSMB Re-import with Province - Using MinhNgoc.net.vn
Re-crawl all XSMB data from 2024-01-01 with province information
"""

import os
os.environ['SUPABASE_URL'] = 'https://islcxaqdqhwgcqkdozeq.supabase.co'
os.environ['SUPABASE_SERVICE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlzbGN4YXFkcWh3Z2Nxa2RvemVxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDkwNjUwMCwiZXhwIjoyMDg2NDgyNTAwfQ.K9oBxjv77u-Rz1LBfy1UfPGRnxrRYvpdux3p8ChFpNU'

import sys
sys.path.insert(0, '.')

from datetime import date, timedelta, datetime
from src.crawler.xsmb_minhngoc_crawler import XSMBMinhNgocCrawler
from src.database.supabase_client import LotteryDB
import time

print("🔧 Initializing MinhNgoc crawler...")
crawler = XSMBMinhNgocCrawler()
db = LotteryDB()

start_date = date(2024, 1, 1)
end_date = datetime.now().date()
total_days = (end_date - start_date).days + 1

print(f"\n{'='*60}")
print(f"🔄 XSMB Import with Province (minhngoc.net.vn)")
print(f"{'='*60}")
print(f"📅 From: {start_date} → To: {end_date} ({total_days} days)")
print(f"⏱️  Estimated time: ~{total_days * 2 / 60:.0f} minutes")
print(f"📍 Province data: YES\n")

input("Press Enter to start or Ctrl+C to cancel...")

print(f"\n{'='*60}")
print(f"📥 Starting import...")
print(f"{'='*60}\n")

success_count = 0
fail_count = 0
current_date = start_date

while current_date <= end_date:
    day_num = (current_date - start_date).days + 1
    
    # Print every 50 days
    should_print = (day_num % 50 == 1 or day_num == total_days or day_num <= 5)
    
    if should_print:
        print(f"[{day_num}/{total_days}] {current_date.strftime('%d/%m/%Y')}...", end=' ', flush=True)
    
    try:
        result = crawler.fetch_results(current_date)
        
        if result:
            response = db.upsert_draw(result)
            
            if response:
                success_count += 1
                if should_print:
                    province = result.get('province', 'unknown')
                    special = result.get('special_prize', 'N/A')
                    print(f"✅ {province} (ĐB: {special})")
            else:
                fail_count += 1
                if should_print:
                    print(f"❌ Failed to save")
        else:
            fail_count += 1
            if should_print:
                print(f"⚠️ No data")
    
    except Exception as e:
        fail_count += 1
        if should_print:
            print(f"❌ {str(e)[:50]}")
    
    # Progress indicator every 50 days
    if day_num % 50 == 0 and not should_print:
        print(f"  ... {day_num}/{total_days} ({success_count} ✅, {fail_count} ❌)")
    
    time.sleep(2)  # Rate limiting - minhngoc might be slower
    current_date += timedelta(days=1)

print(f"\n{'='*60}")
print(f"✅ Import completed!")
print(f"{'='*60}")
print(f"Success: {success_count}/{total_days} ({success_count/total_days*100:.1f}%)")
print(f"Failed: {fail_count}/{total_days}")
print(f"\n🎉 XSMB data now has province information!")
