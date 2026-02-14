"""
XSMN Crawler - FIXED VERSION
Crawl lottery results from xskt.com.vn with CORRECT selectors
"""

import requests
from bs4 import BeautifulSoup
from datetime import date
from typing import Optional, Dict
import time

class XSMNCrawler:
    """Crawler for XSMN (Southern Vietnam Lottery) results"""
    
    # Province mapping
    PROVINCE_MAP = {
        'tp-hcm': 'TP.HCM',
        'dong-thap': 'Đồng Tháp',
        'ca-mau': 'Cà Mau',
        'ben-tre': 'Bến Tre',
        'vung-tau': 'Vũng Tàu',
        'bac-lieu': 'Bạc Liêu',
        'dong-nai': 'Đồng Nai',
        'can-tho': 'Cần Thơ',
        'soc-trang': 'Sóc Trăng',
        'tay-ninh': 'Tây Ninh',
        'an-giang': 'An Giang',
        'binh-thuan': 'Bình Thuận',
        'vinh-long': 'Vĩnh Long',
        'binh-duong': 'Bình Dương',
        'tra-vinh': 'Trà Vinh',
        'long-an': 'Long An',
        'binh-phuoc': 'Bình Phước',
        'hau-giang': 'Hậu Giang',
        'tien-giang': 'Tiền Giang',
        'kien-giang': 'Kiên Giang',
        'da-lat': 'Đà Lạt'
    }
    
    # Province Schedule (0=Monday, 6=Sunday)
    PROVINCE_SCHEDULE = {
        0: ['tp-hcm', 'dong-thap', 'ca-mau'],
        1: ['ben-tre', 'vung-tau', 'bac-lieu'],
        2: ['dong-nai', 'can-tho', 'soc-trang'],
        3: ['tay-ninh', 'an-giang', 'binh-thuan'],
        4: ['vinh-long', 'binh-duong', 'tra-vinh'],
        5: ['tp-hcm', 'long-an', 'binh-phuoc', 'hau-giang'],
        6: ['tien-giang', 'kien-giang', 'da-lat']
    }

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36'
        }
    
    def get_provinces_for_date(self, target_date: date) -> list:
        """Get list of provinces for a specific date based on schedule"""
        weekday = target_date.weekday()
        return self.PROVINCE_SCHEDULE.get(weekday, [])
    
    def fetch_results(self, target_date: date, province: str = 'tp-hcm') -> Optional[Dict]:
        """
        Fetch XSMN results for a specific date and province
        
        Args:
            target_date: Date to fetch results for
            province: Province slug (e.g., 'tp-hcm', 'dong-thap')
            
        Returns:
            Dictionary with lottery results or None if failed
        """
        try:
            results = self._crawl_from_xskt(target_date, province)
            if results:
                print(f"✅ Successfully crawled XSMN ({province}) for {target_date}")
                return results
            else:
                print(f"❌ No data found for XSMN ({province}) on {target_date}")
                return None
        except Exception as e:
            print(f"❌ Error crawling XSMN ({province}) for {target_date}: {e}")
            return None
    
    def _crawl_from_xskt(self, target_date: date, province: str) -> Optional[Dict]:
        """Crawl from xskt.com.vn"""
        
        # Format: ngay-d-m-yyyy (e.g., ngay-13-2-2026)
        day = target_date.day
        month = target_date.month
        year = target_date.year
        # Use XSMN general page which has all provinces
        url = f"https://xskt.com.vn/xsmn/ngay-{day}-{month}-{year}"
        
        print(f"🔍 Crawling XSMN ({province}): {url}")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find the XSMN table (id="MN0" or similar)
            table = soup.find('table', class_='tbl-xsmn')
            
            if not table:
                print(f"  ⚠️ XSMN table not found")
                return None
            
            # Find the column for the specific province
            province_name = self.PROVINCE_MAP.get(province, province.title())
            
            # Find all header cells
            headers = table.find_all('th')
            province_col_index = None
            
            for idx, th in enumerate(headers):
                if province_name.lower() in th.text.lower():
                    province_col_index = idx
                    print(f"  ✅ Found {province_name} in column {idx}")
                    break
            
            if province_col_index is None:
                print(f"  ⚠️ Province {province_name} not found in table")
                return None
            
            # Extract data from the province column
            rows = table.find_all('tr')
            prizes = {}
            
            for row in rows[1:]:  # Skip header row
                cells = row.find_all('td')
                if len(cells) <= province_col_index:
                    continue
                
                prize_name_cell = cells[0]
                prize_value_cell = cells[province_col_index]
                
                prize_name = prize_name_cell.text.strip()
                prize_value = prize_value_cell.text.strip().replace('\n', ',').replace(' ', '')
                
                # Map prize names
                if 'ĐB' in prize_name or 'Đặc biệt' in prize_name:
                    prizes['special_prize'] = prize_value
                elif 'G.1' in prize_name or 'Giải nhất' in prize_name:
                    prizes['first_prize'] = prize_value
                elif 'G.2' in prize_name or 'Giải nhì' in prize_name:
                    prizes['second_prize'] = prize_value
                elif 'G.3' in prize_name or 'Giải ba' in prize_name:
                    prizes['third_prize'] = prize_value
                elif 'G.4' in prize_name or 'Giải tư' in prize_name:
                    prizes['fourth_prize'] = prize_value
                elif 'G.5' in prize_name or 'Giải năm' in prize_name:
                    prizes['fifth_prize'] = prize_value
                elif 'G.6' in prize_name or 'Giải sáu' in prize_name:
                    prizes['sixth_prize'] = prize_value
                elif 'G.7' in prize_name or 'Giải bảy' in prize_name:
                    prizes['seventh_prize'] = prize_value
                elif 'G.8' in prize_name or 'Giải tám' in prize_name:
                    prizes['eighth_prize'] = prize_value
            
            if 'special_prize' not in prizes:
                print(f"  ⚠️ No special prize found")
                return None
            
            result = {
                'draw_date': target_date,
                'region': 'XSMN',
                'province': province,
                **prizes
            }
            
            print(f"  ✅ Special Prize: {prizes.get('special_prize')}")
            print(f"  ✅ First Prize: {prizes.get('first_prize')}")
            
            return result
            
        except requests.RequestException as e:
            print(f"  ❌ Request error: {e}")
            return None
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            import traceback
            traceback.print_exc()
            return None


# Test function
if __name__ == "__main__":
    from datetime import datetime, timedelta
    
    print("=" * 60)
    print("🧪 TESTING XSMN CRAWLER (FIXED VERSION)")
    print("=" * 60)
    
    crawler = XSMNCrawler()
    
    # Test with yesterday
    yesterday = datetime.now() - timedelta(days=1)
    results = crawler.fetch_results(yesterday.date(), province='tp-hcm')
    
    if results:
        print("\n✅ SUCCESS! Results:")
        for key, value in results.items():
            print(f"  {key}: {value}")
    else:
        print("\n❌ FAILED")
