"""
Manual Crawl Script - XSMB & XSMN
Crawl data từ 01/01/2025 đến hiện tại và upsert vào Supabase
"""

from datetime import datetime, timedelta, date
from src.crawler.xsmb_crawler import XSMBCrawler
from src.crawler.xsmn_crawler import XSMNCrawler
from src.database.supabase_client import LotteryDB
import time

# XSMN provinces to crawl
XSMN_PROVINCES = [
    'tp-hcm',      # TP.HCM (Thứ 2, Thứ 7)
    'dong-thap',   # Đồng Tháp (Thứ 2)
    'ca-mau',      # Cà Mau (Thứ 2)
    'ben-tre',     # Bến Tre (Thứ 3)
    'vung-tau',    # Vũng Tàu (Thứ 3)
    'bac-lieu',    # Bạc Liêu (Thứ 3)
    'dong-nai',    # Đồng Nai (Thứ 4)
    'can-tho',     # Cần Thơ (Thứ 4)
    'soc-trang',   # Sóc Trăng (Thứ 4)
    'tay-ninh',    # Tây Ninh (Thứ 5)
    'an-giang',    # An Giang (Thứ 5)
    'binh-thuan',  # Bình Thuận (Thứ 5)
    'vinh-long',   # Vĩnh Long (Thứ 6)
    'binh-duong',  # Bình Dương (Thứ 6)
    'tra-vinh',    # Trà Vinh (Thứ 6)
    'long-an',     # Long An (Thứ 7)
    'binh-phuoc',  # Bình Phước (Thứ 7)
    'hau-giang',   # Hậu Giang (Thứ 7)
    'tien-giang',  # Tiền Giang (CN)
    'kien-giang',  # Kiên Giang (CN)
    'da-lat'       # Đà Lạt (CN)
]

def crawl_xsmb(start_date: date, end_date: date):
    """Crawl XSMB data from start_date to end_date"""
    print("\n" + "=" * 60)
    print("🔍 CRAWLING XSMB")
    print("=" * 60)
    
    crawler = XSMBCrawler()
    db = LotteryDB()
    
    current_date = start_date
    success_count = 0
    failed_count = 0
    total_days = (end_date - start_date).days + 1
    
    while current_date <= end_date:
        day_index = (current_date - start_date).days + 1
        print(f"\n[{day_index}/{total_days}] Crawling XSMB {current_date}...")
        
        try:
            results = crawler.fetch_results(current_date)
            
            if results:
                # Upsert instead of insert
                db.upsert_draw(results)
                success_count += 1
                print(f"  ✅ Upserted: {results['special_prize']}")
            else:
                failed_count += 1
                print(f"  ❌ No data found")
        
        except Exception as e:
            failed_count += 1
            print(f"  ❌ Error: {e}")
        
        current_date += timedelta(days=1)
        
        # Rate limiting
        if day_index % 10 == 0:
            time.sleep(2)
        else:
            time.sleep(1)
    
    print("\n" + "=" * 60)
    print(f"📊 XSMB Summary:")
    print(f"  ✅ Success: {success_count}/{total_days}")
    print(f"  ❌ Failed: {failed_count}/{total_days}")
    print(f"  📈 Success rate: {success_count/total_days*100:.1f}%")
    print("=" * 60)
    
    return success_count


def crawl_xsmn(start_date: date, end_date: date):
    """Crawl XSMN data from start_date to end_date for all provinces"""
    print("\n" + "=" * 60)
    print("🔍 CRAWLING XSMN")
    print("=" * 60)
    
    crawler = XSMNCrawler()
    db = LotteryDB()
    
    current_date = start_date
    total_success = 0
    total_failed = 0
    total_days = (end_date - start_date).days + 1
    
    while current_date <= end_date:
        day_index = (current_date - start_date).days + 1
        print(f"\n[{day_index}/{total_days}] Crawling XSMN {current_date}...")
        
        day_success = 0
        day_failed = 0
        
        for province in XSMN_PROVINCES:
            try:
                results = crawler.fetch_results(current_date, province)
                
                if results:
                    # Upsert instead of insert
                    db.upsert_draw(results)
                    day_success += 1
                    print(f"  ✅ {province}: {results['special_prize']}")
                else:
                    day_failed += 1
                    print(f"  ⚠️ {province}: No data")
            
            except Exception as e:
                day_failed += 1
                print(f"  ❌ {province}: {e}")
            
            # Rate limiting between provinces
            time.sleep(0.5)
        
        total_success += day_success
        total_failed += day_failed
        
        print(f"  📊 Day summary: {day_success} success, {day_failed} failed")
        
        current_date += timedelta(days=1)
        
        # Rate limiting between days
        time.sleep(2)
    
    print("\n" + "=" * 60)
    print(f"📊 XSMN Summary:")
    print(f"  ✅ Success: {total_success}")
    print(f"  ❌ Failed: {total_failed}")
    print(f"  📈 Success rate: {total_success/(total_success+total_failed)*100:.1f}%")
    print("=" * 60)
    
    return total_success


def main():
    """Main function"""
    print("\n🚀 Manual Crawl Tool - XSMB & XSMN")
    print("=" * 60)
    
    # Date range: 2025-01-01 to today
    start_date = date(2025, 1, 1)
    end_date = datetime.now().date()
    
    total_days = (end_date - start_date).days + 1
    
    print(f"\n📅 Date range:")
    print(f"   From: {start_date}")
    print(f"   To: {end_date}")
    print(f"   Total days: {total_days}")
    print()
    print("⚠️  Note: This will UPSERT data (update if exists, insert if new)")
    print()
    
    choice = input("Crawl which region? (1=XSMB, 2=XSMN, 3=BOTH): ").strip()
    
    if choice == '1':
        crawl_xsmb(start_date, end_date)
    elif choice == '2':
        crawl_xsmn(start_date, end_date)
    elif choice == '3':
        print("\n🔄 Crawling both regions...")
        xsmb_count = crawl_xsmb(start_date, end_date)
        xsmn_count = crawl_xsmn(start_date, end_date)
        
        print("\n" + "=" * 60)
        print("🎉 ALL DONE!")
        print(f"  XSMB: {xsmb_count} records")
        print(f"  XSMN: {xsmn_count} records")
        print("=" * 60)
    else:
        print("❌ Invalid choice!")


if __name__ == "__main__":
    main()
