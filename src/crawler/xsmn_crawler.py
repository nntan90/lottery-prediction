"""
XSMN Crawler - Crawl kết quả xổ số miền Nam
Nguồn: xskt.com.vn (có thể thay đổi)
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import Optional, Dict, List
import time


class XSMNCrawler:
    """Crawler cho xổ số miền Nam"""
    
    BASE_URL = "https://xskt.com.vn/xsmn"
    BACKUP_URL = "https://www.minhngoc.net.vn/xo-so-mien-nam"
    
    def __init__(self):
        """Initialize crawler với headers"""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    
    def fetch_results(self, target_date: date, province: str = "tp-hcm") -> Optional[Dict]:
        """
        Crawl kết quả XSMN cho ngày cụ thể
        
        Args:
            target_date: Ngày cần crawl
            province: Tỉnh/thành (mặc định TP.HCM vì quay hàng ngày)
        
        Returns:
            Dictionary chứa kết quả hoặc None
        """
        print(f"🔍 Crawling XSMN ({province}) for {target_date}...")
        
        try:
            results = self._crawl_from_xskt(target_date, province)
            
            if results:
                print(f"✅ Successfully crawled XSMN")
                return results
            
            print(f"❌ Failed to crawl XSMN")
            return None
            
        except Exception as e:
            print(f"❌ Error crawling XSMN: {e}")
            return None
    
    def _crawl_from_xskt(self, target_date: date, province: str) -> Optional[Dict]:
        """
        Crawl từ xskt.com.vn
        
        LƯU Ý: Cấu trúc tương tự XSMB nhưng có thể khác selectors
        """
        try:
            date_str = target_date.strftime("%d-%m-%Y")
            url = f"{self.BASE_URL}/{province}/{date_str}.html"
            
            print(f"  → Fetching: {url}")
            
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Parse kết quả (tương tự XSMB)
            results = {
                'draw_date': target_date,
                'region': 'XSMN',
                'special_prize': self._extract_prize(soup, 'special'),
                'first_prize': self._extract_prize(soup, 'first'),
                'second_prize': self._extract_prize_array(soup, 'second', 1),
                'third_prize': self._extract_prize_array(soup, 'third', 2),
                'fourth_prize': self._extract_prize_array(soup, 'fourth', 7),
                'fifth_prize': self._extract_prize_array(soup, 'fifth', 1),
                'sixth_prize': self._extract_prize_array(soup, 'sixth', 3),
                'seventh_prize': self._extract_prize_array(soup, 'seventh', 1),
            }
            
            if results['special_prize']:
                return results
            else:
                print(f"  ⚠️ No special prize found")
                return None
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return None
    
    def _extract_prize(self, soup: BeautifulSoup, prize_type: str) -> Optional[str]:
        """Extract một giải thưởng đơn"""
        try:
            if prize_type == 'special':
                elem = soup.select_one('.special-prize .number')
            elif prize_type == 'first':
                elem = soup.select_one('.first-prize .number')
            else:
                return None
            
            if elem:
                return elem.text.strip()
            return None
            
        except Exception as e:
            return None
    
    def _extract_prize_array(
        self, 
        soup: BeautifulSoup, 
        prize_type: str, 
        expected_count: int
    ) -> List[str]:
        """Extract các giải có nhiều số"""
        try:
            selector_map = {
                'second': '.second-prize .number',
                'third': '.third-prize .number',
                'fourth': '.fourth-prize .number',
                'fifth': '.fifth-prize .number',
                'sixth': '.sixth-prize .number',
                'seventh': '.seventh-prize .number',
            }
            
            selector = selector_map.get(prize_type)
            if not selector:
                return []
            
            elements = soup.select(selector)
            numbers = [elem.text.strip() for elem in elements]
            
            return numbers
            
        except Exception as e:
            return []


if __name__ == "__main__":
    from datetime import timedelta
    
    crawler = XSMNCrawler()
    yesterday = date.today() - timedelta(days=1)
    
    print(f"\nTesting XSMN Crawler\n{'='*60}\n")
    
    results = crawler.fetch_results(yesterday)
    
    if results:
        print(f"\n✅ Success! Special Prize: {results['special_prize']}")
    else:
        print(f"\n❌ Failed - need to update selectors")
