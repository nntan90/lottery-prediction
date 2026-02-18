"""
XSMN Crawler - MINH NGOC VERSION
Crawl lottery results from minhngoc.net.vn (Search Interface)
"""

import requests
from bs4 import BeautifulSoup
from datetime import date
from typing import Optional, Dict
import time

class XSMNCrawler:
    """Crawler for XSMN (Southern Vietnam Lottery) results from Minh Ngoc"""
    
    # Province mapping (Slug -> Minh Ngoc Display Name)
    PROVINCE_MAP = {
        'tp-hcm': 'TP. HCM',  # Note: Minh Ngoc uses space "TP. HCM"
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
            results = self._crawl_from_minhngoc(target_date, province)
            if results:
                print(f"✅ Successfully crawled XSMN ({province}) for {target_date}")
                return results
            else:
                print(f"❌ No data found for XSMN ({province}) on {target_date}")
                return None
        except Exception as e:
            print(f"❌ Error crawling XSMN ({province}) for {target_date}: {e}")
            return None
    
    def _clean_prize_list(self, text_value):
        """
        Cleans text extracted with separator='|'.
        Returns a list of strings.
        """
        if not text_value:
            return []
        # Split by |
        parts = text_value.split('|')
        # Clean each part
        cleaned = [p.strip() for p in parts if p.strip()]
        return cleaned

    def _crawl_from_minhngoc(self, target_date: date, target_province_slug: str) -> Optional[Dict]:
        """Crawl from minhngoc.net.vn (Search Interface)"""
        
        # URL: mien=1 for XSMN
        url = f"https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=1&ngay={target_date.day}&thang={target_date.month}&nam={target_date.year}"
        
        print(f"🔍 Crawling XSMN ({target_province_slug}): {url}")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            # Check if successful
            if response.status_code != 200:
                print(f"  ❌ Failed to fetch: {response.status_code}")
                return None
                
            soup = BeautifulSoup(response.content, 'html.parser')
            table = soup.find('table', class_='bkqmiennam')
            
            if not table:
                print(f"  ⚠️ XSMN table (bkqmiennam) not found")
                return None
                
            # Target display name
            expected_name = self.PROVINCE_MAP.get(target_province_slug)
            if not expected_name:
                print(f"  ⚠️ Unknown province slug: {target_province_slug}")
                return None
            
            rows = table.find_all('tr')
            
            for idx, row in enumerate(rows):
                # Check if this row is for our target province
                td_tinh = row.find('td', class_='tinh')
                if not td_tinh:
                    continue
                
                prov_name_text = td_tinh.text.strip()
                
                # Check if matches (EXACT match or contains)
                if expected_name.lower() != prov_name_text.lower():
                    continue
                
                # FOUND! Extract prizes
                print(f"  ✅ Found province row {idx}: {prov_name_text}")
                # Debug: print all cell text
                cells = [td.get_text(separator='|').strip() for td in row.find_all('td')]
                print(f"  Row content: {cells[:5]}...") # Print first 5 cells
                
                prizes = {}
                
                # Helper to extract
                def extract(class_name, db_field, is_array=True):
                    td = row.find('td', class_=class_name)
                    if td:
                        text = td.get_text(separator='|')
                        values = self._clean_prize_list(text)
                        if not values:
                            return
                        
                        if is_array:
                            prizes[db_field] = values
                        else:
                            prizes[db_field] = values[0]
                
                extract('giai8', 'eighth_prize', is_array=False)
                
                # Check if this is a header row (value is not a digit/contains "Giải")
                g8 = prizes.get('eighth_prize')
                if g8 and ('Giải' in str(g8) or not str(g8).replace('|', '').isdigit()):
                     print(f"  ⚠️ Skipping header row for {expected_name} (Value: {g8})")
                     continue

                extract('giai7', 'seventh_prize', is_array=True)
                extract('giai6', 'sixth_prize', is_array=True)
                extract('giai5', 'fifth_prize', is_array=True)
                extract('giai4', 'fourth_prize', is_array=True)
                extract('giai3', 'third_prize', is_array=True)
                extract('giai2', 'second_prize', is_array=True)
                extract('giai1', 'first_prize', is_array=False)
                extract('giaidb', 'special_prize', is_array=False)
                
                if 'special_prize' not in prizes:
                    print(f"  ⚠️ No special prize found for {target_province_slug}")
                    return None
                
                result = {
                    'draw_date': target_date,
                    'region': 'XSMN',
                    'province': target_province_slug,
                    **prizes
                }
                
                print(f"  ✅ Special Prize: {prizes.get('special_prize')}")
                return result

            print(f"  ⚠️ Province {expected_name} not found in results table")
            return None
            
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def fetch_batch_results(self, target_date: date) -> list:
        """
        Fetch results for ALL provinces on a specific date in one request.
        Returns a list of result dictionaries.
        """
        try:
            results = self._crawl_batch_from_minhngoc(target_date)
            if results:
                print(f"✅ Successfully crawled batch XSMN for {target_date}: {len(results)} provinces found")
                return results
            else:
                print(f"❌ No data found for XSMN on {target_date}")
                return []
        except Exception as e:
            print(f"❌ Error crawling batch XSMN for {target_date}: {e}")
            return []

    def _crawl_batch_from_minhngoc(self, target_date: date) -> list:
        """Crawl ALL provinces from minhngoc page.
        
        HTML structure: each province spans MULTIPLE rows:
          - Row with class 'tinh': province name
          - Row with class 'matinh': lottery code
          - Rows with class 'giai8', 'giai7', ..., 'giaidb': individual prizes
        
        We group rows into province blocks by detecting 'tinh' rows as separators.
        """
        url = f"https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=1&ngay={target_date.day}&thang={target_date.month}&nam={target_date.year}"
        print(f"🔍 Crawling Batch XSMN: {url}")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            if response.status_code != 200:
                print(f"  ❌ Failed to fetch: {response.status_code}")
                return []
                
            soup = BeautifulSoup(response.content, 'html.parser')
            table = soup.find('table', class_='bkqmiennam')
            
            if not table:
                print(f"  ⚠️ XSMN table (bkqmiennam) not found")
                return []

            # Reverse map: 'display name (lowercase)' -> 'slug'
            name_to_slug = {v.lower(): k for k, v in self.PROVINCE_MAP.items()}

            rows = table.find_all('tr')

            # ── Step 1: Group rows into province blocks ──────────────────────
            # A new block starts whenever we see a row with class 'tinh'
            # (but skip the very first header block which has no real province)
            blocks = []       # list of (province_slug, [rows_in_block])
            current_slug = None
            current_rows = []

            for row in rows:
                all_tinh_tds = row.find_all('td', class_='tinh')

                if len(all_tinh_tds) > 1:
                    # Summary row containing all provinces — skip entirely
                    continue
                elif len(all_tinh_tds) == 1:
                    # Individual province header row — start a new block
                    if current_slug and current_rows:
                        blocks.append((current_slug, current_rows))

                    prov_name = all_tinh_tds[0].text.strip()
                    prov_lower = prov_name.lower()
                    slug = name_to_slug.get(prov_lower)
                    if not slug:
                        if 'đà lạt' in prov_lower:
                            slug = 'da-lat'
                        elif 'hcm' in prov_lower:
                            slug = 'tp-hcm'
                        else:
                            current_slug = None
                            current_rows = []
                            continue

                    print(f"  ✅ Found province: {prov_name} ({slug})")
                    current_slug = slug
                    current_rows = [row]
                else:
                    # Prize row — append to current province block
                    if current_slug is not None:
                        current_rows.append(row)

            # Don't forget the last block
            if current_slug and current_rows:
                blocks.append((current_slug, current_rows))

            # ── Step 2: Extract prizes from each block ───────────────────────
            results = []

            for slug, block_rows in blocks:
                prizes = {}

                def get_td_text(class_name):
                    """Find a td with given class across all rows in the block."""
                    for r in block_rows:
                        td = r.find('td', class_=class_name)
                        if td:
                            return td.get_text(separator='|')
                    return None

                def extract(class_name, db_field, is_array=True):
                    text = get_td_text(class_name)
                    if text:
                        values = self._clean_prize_list(text)
                        if not values:
                            return
                        if is_array:
                            prizes[db_field] = values
                        else:
                            prizes[db_field] = values[0]

                extract('giai8', 'eighth_prize', is_array=False)

                # Skip header rows (value contains "Giải" or is not numeric)
                g8 = prizes.get('eighth_prize')
                if g8 and ('Giải' in str(g8) or not str(g8).replace('|', '').isdigit()):
                    continue

                extract('giai7', 'seventh_prize', is_array=True)
                extract('giai6', 'sixth_prize', is_array=True)
                extract('giai5', 'fifth_prize', is_array=True)
                extract('giai4', 'fourth_prize', is_array=True)
                extract('giai3', 'third_prize', is_array=True)
                extract('giai2', 'second_prize', is_array=True)
                extract('giai1', 'first_prize', is_array=False)
                extract('giaidb', 'special_prize', is_array=False)

                if 'special_prize' in prizes:
                    results.append({
                        'draw_date': target_date,
                        'region': 'XSMN',
                        'province': slug,
                        **prizes
                    })
                else:
                    print(f"  ⚠️ No special prize found for {slug}, skipping")

            return results
            
        except Exception as e:
            print(f"  ❌ Batch Parse error: {e}")
            import traceback
            traceback.print_exc()
            return []

# Test function
if __name__ == "__main__":
    from datetime import datetime, timedelta
    
    print("=" * 60)
    print("🧪 TESTING XSMN CRAWLER (MINH NGOC VERSION)")
    print("=" * 60)
    
    crawler = XSMNCrawler()
    
    # Test with yesterday (or slightly further back to ensure data)
    # Minh Ngoc usually updates quickly.
    # Let's test 2024-01-01 to be sure (known data)
    test_date = date(2024, 1, 1) # TP.HCM
    results = crawler.fetch_results(test_date, province='tp-hcm')
    
    if results:
        print("\n✅ SUCCESS! Results for 2024-01-01:")
        for key, value in results.items():
            print(f"  {key}: {value}")
    else:
        print("\n❌ FAILED")
