"""
XSMN Historical Crawler - Minhngoc.net.vn (FIXED VERSION)
Crawl XSMN data từ 01/01/2024 đến hiện tại
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, date
from src.database.supabase_client import LotteryDB
import time

class MinhngocXSMNCrawler:
    """Crawler for XSMN historical data from minhngoc.net.vn"""
    
    def __init__(self):
        self.base_url = "https://www.minhngoc.net.vn/ket-qua-xo-so/mien-nam"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        
        # Province name mapping
        self.province_map = {
            'TP. HCM': 'tp-hcm',
            'TP.HCM': 'tp-hcm',
            'Đồng Tháp': 'dong-thap',
            'Cà Mau': 'ca-mau',
            'Bến Tre': 'ben-tre',
            'Vũng Tàu': 'vung-tau',
            'Bạc Liêu': 'bac-lieu',
            'Đồng Nai': 'dong-nai',
            'Cần Thơ': 'can-tho',
            'Sóc Trăng': 'soc-trang',
            'Tây Ninh': 'tay-ninh',
            'An Giang': 'an-giang',
            'Bình Thuận': 'binh-thuan',
            'Vĩnh Long': 'vinh-long',
            'Bình Dương': 'binh-duong',
            'Trà Vinh': 'tra-vinh',
            'Long An': 'long-an',
            'Bình Phước': 'binh-phuoc',
            'Hậu Giang': 'hau-giang',
            'Tiền Giang': 'tien-giang',
            'Kiên Giang': 'kien-giang',
            'Đà Lạt': 'da-lat',
            'Lâm Đồng': 'da-lat'  # Alternative name
        }
    
    def fetch_results(self, target_date: date):
        """Fetch XSMN results for a specific date"""
        
        # Format URL: /mien-nam/dd-mm-yyyy.html
        date_str = target_date.strftime('%d-%m-%Y')
        url = f"{self.base_url}/{date_str}.html"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find the main result table (table 8)
            all_tables = soup.find_all('table')
            
            if len(all_tables) < 9:
                return []
            
            result_table = all_tables[8]
            
            # Extract province results
            provinces_data = self._parse_provinces(result_table, target_date)
            
            return provinces_data
            
        except Exception as e:
            print(f"  ❌ Error crawling {date_str}: {e}")
            return []
    
    def _parse_provinces(self, table, target_date):
        """Parse all provinces from the result table"""
        
        provinces_data = []
        
        # Get first row (contains all data)
        rows = table.find_all('tr')
        if not rows:
            return []
        
        # Get text with pipe separator
        row_text = rows[0].get_text(separator='|', strip=True)
        
        # Extract each province
        for province_name, province_code in self.province_map.items():
            if province_name in row_text:
                try:
                    province_result = self._extract_province_data(
                        row_text, province_name, province_code, target_date
                    )
                    if province_result:
                        provinces_data.append(province_result)
                except Exception as e:
                    pass  # Skip errors silently
        
        return provinces_data
    
    def _extract_province_data(self, full_text, province_name, province_code, target_date):
        """Extract data for a specific province"""
        
        # Find province section
        start_idx = full_text.find(province_name)
        if start_idx == -1:
            return None
        
        # Find next province or end
        next_idx = len(full_text)
        for other_prov in self.province_map.keys():
            if other_prov != province_name:
                idx = full_text.find(other_prov, start_idx + len(province_name))
                if idx != -1 and idx < next_idx:
                    next_idx = idx
        
        # Extract section
        section = full_text[start_idx:next_idx]
        
        # Split by pipe and filter numbers
        parts = section.split('|')
        numbers = []
        for part in parts:
            part = part.strip()
            # Only pure numeric with 2+ digits
            if part.isdigit() and len(part) >= 2:
                numbers.append(part)
        
        # XSMN has 18 numbers total
        # G8(2 digits) + G7(3 digits) + G6(3x4 digits) + G5(4 digits) + 
        # G4(7x5 digits) + G3(2x5 digits) + G2(5 digits) + ĐB(6 digits)
        if len(numbers) < 18:
            return None
        
        try:
            # Map to our schema
            draw_data = {
                'draw_date': target_date,
                'region': 'XSMN',
                'province': province_code,
                'seventh_prize': [numbers[0]],      # G8 (2 digits)
                'sixth_prize': [numbers[1]],        # G7 (3 digits)
                'fifth_prize': numbers[2:5],        # G6 (3x4 digits)
                'fourth_prize': [numbers[5]],       # G5 (4 digits)
                'third_prize': numbers[6:13],       # G4 (7x5 digits)
                'second_prize': numbers[13:15],     # G3 (2x5 digits)
                'first_prize': [numbers[15]],       # G2 (5 digits)
                'special_prize': numbers[17]        # ĐB (6 digits) - last one
            }
            
            return draw_data
            
        except IndexError:
            return None


def import_xsmn_from_minhngoc(start_date: date, end_date: date):
    """Import XSMN data from minhngoc.net.vn"""
    
    print("=" * 60)
    print(f"📥 Importing XSMN from {start_date} to {end_date}")
    print("=" * 60)
    
    crawler = MinhngocXSMNCrawler()
    db = LotteryDB()
    
    current_date = start_date
    total_success = 0
    total_skipped = 0
    total_failed = 0
    total_days = (end_date - start_date).days + 1
    
    while current_date <= end_date:
        day_index = (current_date - start_date).days + 1
        print(f"\n[{day_index}/{total_days}] {current_date.strftime('%d/%m/%Y')}...")
        
        try:
            provinces_data = crawler.fetch_results(current_date)
            
            if provinces_data:
                day_success = 0
                for province_data in provinces_data:
                    try:
                        db.upsert_draw(province_data)
                        total_success += 1
                        day_success += 1
                    except Exception as e:
                        error_str = str(e).lower()
                        if 'duplicate' in error_str or '23505' in error_str:
                            total_skipped += 1
                        else:
                            total_failed += 1
                            if total_failed <= 5:
                                print(f"  ❌ DB Error: {e}")
                
                print(f"  ✅ Imported {day_success} provinces")
            else:
                total_failed += 1
        
        except Exception as e:
            total_failed += 1
            if total_failed <= 5:
                print(f"  ❌ Error: {e}")
        
        current_date += timedelta(days=1)
        
        # Rate limiting
        if day_index % 10 == 0:
            time.sleep(3)
            print(f"\n📊 Progress: {total_success} success, {total_skipped} skipped, {total_failed} failed")
        else:
            time.sleep(1.5)
    
    print("\n" + "=" * 60)
    print(f"📊 Final Summary:")
    print(f"  ✅ Success: {total_success}")
    print(f"  ⚠️ Skipped: {total_skipped}")
    print(f"  ❌ Failed: {total_failed}")
    total = total_success + total_skipped + total_failed
    if total > 0:
        print(f"  📈 Success rate: {(total_success+total_skipped)/total*100:.1f}%")
    print("=" * 60)
    
    return total_success


def main():
    """Main function"""
    print("\n🚀 XSMN Import Tool - Minhngoc.net.vn")
    print("=" * 60)
    
    # Date range: 2024-01-01 to today
    start_date = date(2024, 1, 1)
    end_date = datetime.now().date()
    
    total_days = (end_date - start_date).days + 1
    
    print(f"\n📅 Date range:")
    print(f"   From: {start_date}")
    print(f"   To: {end_date}")
    print(f"   Total days: {total_days}")
    print()
    print("⚠️  Note: This will UPSERT data (update if exists, insert if new)")
    print()
    
    input("Press Enter to continue or Ctrl+C to cancel...")
    
    success = import_xsmn_from_minhngoc(start_date, end_date)
    
    if success > 0:
        print(f"\n✅ Import completed! {success} records imported.")
    else:
        print("\n❌ Import failed. Please check the error messages above.")


if __name__ == "__main__":
    main()
