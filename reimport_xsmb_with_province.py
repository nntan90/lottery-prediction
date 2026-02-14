"""
XSMB Re-import Script - Add Province Data
Re-import all XSMB data from 2024-01-01 to present with province information
"""

import sys
sys.path.insert(0, '.')

from datetime import date, timedelta, datetime
from src.crawler.xsmb_crawler import XSMBCrawler
from src.database.supabase_client import LotteryDB
import time

def reimport_xsmb_with_province(start_date: date, end_date: date):
    """
    Re-import XSMB data with province information
    
    Args:
        start_date: Start date
        end_date: End date
    """
    crawler = XSMBCrawler()
    db = LotteryDB()
    
    total_days = (end_date - start_date).days + 1
    
    print(f"\n{'='*60}")
    print(f"🔄 XSMB Re-import with Province")
    print(f"{'='*60}\n")
    print(f"📅 Date range:")
    print(f"   From: {start_date}")
    print(f"   To: {end_date}")
    print(f"   Total days: {total_days}\n")
    print(f"⚠️  Note: This will UPSERT data (update province if exists)\n")
    
    input("Press Enter to continue or Ctrl+C to cancel...")
    
    print(f"\n{'='*60}")
    print(f"📥 Re-importing XSMB from {start_date} to {end_date}")
    print(f"{'='*60}\n")
    
    success_count = 0
    fail_count = 0
    current_date = start_date
    
    while current_date <= end_date:
        day_num = (current_date - start_date).days + 1
        print(f"[{day_num}/{total_days}] {current_date.strftime('%d/%m/%Y')}...")
        
        try:
            # Crawl data with province
            result = crawler.fetch_results(current_date)
            
            if result:
                # Upsert to database
                response = db.save_draw(result)
                
                if response:
                    province = result.get('province', 'unknown')
                    print(f"  ✅ Updated with province: {province}")
                    success_count += 1
                else:
                    print(f"  ❌ Failed to save")
                    fail_count += 1
            else:
                print(f"  ⚠️ No data found")
                fail_count += 1
        
        except Exception as e:
            print(f"  ❌ Error: {e}")
            fail_count += 1
        
        # Rate limiting
        time.sleep(1.5)
        
        current_date += timedelta(days=1)
    
    print(f"\n{'='*60}")
    print(f"✅ Re-import completed!")
    print(f"{'='*60}")
    print(f"Success: {success_count}/{total_days}")
    print(f"Failed: {fail_count}/{total_days}")
    print(f"Success rate: {success_count/total_days*100:.1f}%\n")


if __name__ == "__main__":
    # Re-import from 2024-01-01 to present
    start = date(2024, 1, 1)
    end = datetime.now().date()
    
    reimport_xsmb_with_province(start, end)
