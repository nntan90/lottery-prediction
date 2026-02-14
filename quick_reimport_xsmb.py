"""
XSMN Re-import Script - Add Province Data
Quick inline script to re-import XSMB with province
"""

import os
os.environ['SUPABASE_URL'] = 'https://islcxaqdqhwgcqkdozeq.supabase.co'
os.environ['SUPABASE_SERVICE_KEY'] = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImlzbGN4YXFkcWh3Z2Nxa2RvemVxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MDkwNjA4MywiZXhwIjoyMDg2NDgyMDgzfQ.Iq_MH_qKdHMi_yFVdlxOqJNhxJaLWRJCbvkFEQOxLgI'

import sys
sys.path.insert(0, '.')

from datetime import date, timedelta, datetime
from src.crawler.xsmb_crawler import XSMBCrawler
from src.database.supabase_client import LotteryDB
import time

print("🔧 Initializing...")
crawler = XSMBCrawler()
db = LotteryDB()

start_date = date(2024, 1, 1)
end_date = datetime.now().date()
total_days = (end_date - start_date).days + 1

print(f"\n{'='*60}")
print(f"🔄 XSMB Re-import with Province")
print(f"{'='*60}")
print(f"📅 From: {start_date} → To: {end_date} ({total_days} days)")
print(f"⏱️  Estimated time: ~{total_days * 1.5 / 60:.0f} minutes\n")

input("Press Enter to start or Ctrl+C to cancel...")

print(f"\n{'='*60}")
print(f"📥 Starting re-import...")
print(f"{'='*60}\n")

success_count = 0
fail_count = 0
current_date = start_date

while current_date <= end_date:
    day_num = (current_date - start_date).days + 1
    
    # Print every 50 days to reduce output
    should_print = (day_num % 50 == 1 or day_num == total_days or day_num <= 5)
    
    if should_print:
        print(f"[{day_num}/{total_days}] {current_date.strftime('%d/%m/%Y')}...", end=' ', flush=True)
    
    try:
        # Suppress crawler output
        import io
        import contextlib
        
        if not should_print:
            f = io.StringIO()
            with contextlib.redirect_stdout(f):
                result = crawler.fetch_results(current_date)
        else:
            result = crawler.fetch_results(current_date)
        
        if result:
            if not should_print:
                with contextlib.redirect_stdout(io.StringIO()):
                    response = db.upsert_draw(result)
            else:
                response = db.upsert_draw(result)
            
            if response:
                success_count += 1
                if should_print:
                    province = result.get('province', 'unknown')
                    print(f"✅ {province}")
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
    
    time.sleep(1.5)
    current_date += timedelta(days=1)

print(f"\n{'='*60}")
print(f"✅ Re-import completed!")
print(f"{'='*60}")
print(f"Success: {success_count}/{total_days} ({success_count/total_days*100:.1f}%)")
print(f"Failed: {fail_count}/{total_days}")
