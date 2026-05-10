"""
XSMB Crawler - MINH NGOC VERSION
Crawl lottery results from minhngoc.net.vn (mien=2)
With retry/backoff for transient network failures.
"""

import requests
from bs4 import BeautifulSoup
from datetime import date
from typing import Optional, Dict
import time

from src.utils.retry import retry_with_backoff

class XSMBCrawler:
    """Crawler for XSMB (Northern Vietnam Lottery) results from Minh Ngoc"""
    
    # XSMB Schedule (Weekday -> Province Slug)
    XSMB_SCHEDULE = {
        0: 'ha-noi',      # Monday
        1: 'quang-ninh',  # Tuesday
        2: 'bac-ninh',    # Wednesday
        3: 'ha-noi',      # Thursday
        4: 'hai-phong',   # Friday
        5: 'nam-dinh',    # Saturday
        6: 'thai-binh'    # Sunday
    }

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36'
        }

    @retry_with_backoff(max_retries=3, base_delay=2.0, exceptions=(requests.RequestException,))
    def _fetch_page(self, url: str) -> requests.Response:
        """Fetch page with retry on network errors."""
        return requests.get(url, headers=self.headers, timeout=15)
    
    def fetch_results(self, target_date: date) -> Optional[Dict]:
        """
        Fetch XSMB results for a specific date
        
        Args:
            target_date: Date to fetch results for
            
        Returns:
            Dictionary with lottery results or None if failed
        """
        try:
            results = self._crawl_from_minhngoc(target_date)
            if results:
                print(f"✅ Successfully crawled XSMB for {target_date}")
                return results
            else:
                print(f"❌ No data found for XSMB on {target_date}")
                return None
        except Exception as e:
            print(f"❌ Error crawling XSMB for {target_date}: {e}")
            return None
    
    def _clean_prize_list(self, text_value):
        """
        Cleans text extracted with separator='|'.
        Returns a list of strings.
        """
        if not text_value:
            return []
        # Replace common separators with pipe if scraping combined text
        text_value = text_value.replace('-', '|')
        # Split by |
        parts = text_value.split('|')
        # Clean each part
        cleaned = []
        for p in parts:
            p = p.strip()
            # Filter for pure digits (Minh Ngoc might have symbols)
            if p and p.isdigit():
                cleaned.append(p)
        return cleaned

    def _crawl_from_minhngoc(self, target_date: date) -> Optional[Dict]:
        """Crawl from minhngoc.net.vn (Search Interface mien=2)"""
        
        # URL: mien=2 for XSMB
        url = f"https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=2&ngay={target_date.day}&thang={target_date.month}&nam={target_date.year}"
        
        print(f"🔍 Crawling XSMB: {url}")
        
        try:
            response = self._fetch_page(url)
            # Check if successful
            if response.status_code != 200:
                print(f"  ❌ Failed to fetch: {response.status_code}")
                return None
                
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Determine table (try refined selector first)
            table = soup.find('table', class_='bkqmienbac')
            if not table:
                # Fallback to general class
                table = soup.find('table', class_='bkqmiennam')
                
            if not table:
                # Check for Holiday
                page_text = soup.get_text().lower()
                if any(kw in page_text for kw in ["nghỉ tết", "nghỉ lễ", "lịch tết", "miền bắc nghỉ"]):
                    print(f"  ⚠️ Holiday detected: XSMB is not drawn today.")
                    return None

                print(f"  ⚠️ XSMB table (bkqmienbac) not found")
                return None
            
            # Determine Province based on Schedule
            weekday = target_date.weekday()
            province_slug = self.XSMB_SCHEDULE.get(weekday, 'ha-noi') # Fallback
            
            prizes = {}
            
            # Helper to extract
            def extract(class_name, db_field, is_array=True):
                 # Search for ANY td with this class in the table
                td = table.find('td', class_=class_name)
                if td:
                    text = td.get_text(separator='|')
                    values = self._clean_prize_list(text)
                    if not values:
                        return
                    
                    if is_array:
                        prizes[db_field] = values
                    else:
                        prizes[db_field] = values[0]
            
            extract('giaidb', 'special_prize', is_array=False)
            extract('giai1', 'first_prize', is_array=False) 
            extract('giai2', 'second_prize', is_array=True)
            extract('giai3', 'third_prize', is_array=True)
            extract('giai4', 'fourth_prize', is_array=True)
            extract('giai5', 'fifth_prize', is_array=True)
            extract('giai6', 'sixth_prize', is_array=True)
            extract('giai7', 'seventh_prize', is_array=True)
            
            if 'special_prize' not in prizes:
                print(f"  ⚠️ No special prize found for XSMB")
                return None
            
            result = {
                'draw_date': target_date,
                'region': 'XSMB',
                'province': province_slug,
                **prizes
            }
            
            print(f"  ✅ Special Prize: {prizes.get('special_prize')}")
            print(f"  ✅ Province: {province_slug}")
            return result
            
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            import traceback
            traceback.print_exc()
            return None

# Test function
if __name__ == "__main__":
    from datetime import datetime, timedelta
    
    print("=" * 60)
    print("🧪 TESTING XSMB CRAWLER (MINH NGOC VERSION)")
    print("=" * 60)
    
    crawler = XSMBCrawler()
    
    # Test with yesterday (or a specific date)
    # 2024-01-01 was Monday (Hanoi)
    test_date = date(2024, 1, 1) 
    results = crawler.fetch_results(test_date)
    
    if results:
        print("\n✅ SUCCESS! Results for 2024-01-01:")
        for key, value in results.items():
            print(f"  {key}: {value}")
    else:
        print("\n❌ FAILED")
