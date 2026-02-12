"""
XSMB Crawler - Crawl kết quả xổ số miền Bắc
Nguồn: xskt.com.vn (có thể thay đổi)
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, date
from typing import Optional, Dict, List
import time


class XSMBCrawler:
    """Crawler cho xổ số miền Bắc"""
    
    # Nguồn chính - có thể thay đổi nếu website thay đổi cấu trúc
    BASE_URL = "https://xskt.com.vn/xsmb"
    
    # Nguồn dự phòng
    BACKUP_URL = "https://www.minhngoc.net.vn/xo-so-mien-bac"
    
    def __init__(self):
        """Initialize crawler với headers"""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
        }
    
    def fetch_results(self, target_date: date) -> Optional[Dict]:
        """
        Crawl kết quả XSMB cho ngày cụ thể
        
        Args:
            target_date: Ngày cần crawl (date object)
        
        Returns:
            Dictionary chứa kết quả hoặc None nếu failed
            {
                'draw_date': date,
                'region': 'XSMB',
                'special_prize': '12345',
                'first_prize': '67890',
                'second_prize': ['11111', '22222'],
                ...
            }
        """
        print(f"🔍 Crawling XSMB for {target_date}...")
        
        try:
            # Thử nguồn chính trước
            results = self._crawl_from_xskt(target_date)
            
            if results:
                print(f"✅ Successfully crawled from xskt.com.vn")
                return results
            
            # Nếu fail, thử nguồn dự phòng
            print(f"⚠️ Primary source failed, trying backup...")
            results = self._crawl_from_minhngoc(target_date)
            
            if results:
                print(f"✅ Successfully crawled from minhngoc.net.vn")
                return results
            
            print(f"❌ All sources failed for {target_date}")
            return None
            
        except Exception as e:
            print(f"❌ Error crawling XSMB: {e}")
            return None
    
    def _crawl_from_xskt(self, target_date: date) -> Optional[Dict]:
        """
        Crawl từ xskt.com.vn
        
        LƯU Ý: Cấu trúc HTML có thể thay đổi!
        Nếu crawler không hoạt động, cần update selectors
        """
        try:
            # Format URL: https://xskt.com.vn/xsmb/dd-mm-yyyy.html
            date_str = target_date.strftime("%d-%m-%Y")
            url = f"{self.BASE_URL}/{date_str}.html"
            
            print(f"  → Fetching: {url}")
            
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Parse kết quả
            # LƯU Ý: Selectors này là ví dụ, cần kiểm tra cấu trúc thực tế
            results = {
                'draw_date': target_date,
                'region': 'XSMB',
                'special_prize': self._extract_prize(soup, 'special'),
                'first_prize': self._extract_prize(soup, 'first'),
                'second_prize': self._extract_prize_array(soup, 'second', 2),
                'third_prize': self._extract_prize_array(soup, 'third', 6),
                'fourth_prize': self._extract_prize_array(soup, 'fourth', 4),
                'fifth_prize': self._extract_prize_array(soup, 'fifth', 6),
                'sixth_prize': self._extract_prize_array(soup, 'sixth', 3),
                'seventh_prize': self._extract_prize_array(soup, 'seventh', 4),
            }
            
            # Validate: ít nhất phải có giải đặc biệt
            if results['special_prize']:
                return results
            else:
                print(f"  ⚠️ No special prize found - might be wrong selectors")
                return None
                
        except requests.RequestException as e:
            print(f"  ❌ Request error: {e}")
            return None
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            return None
    
    def _crawl_from_minhngoc(self, target_date: date) -> Optional[Dict]:
        """
        Crawl từ minhngoc.net.vn (backup source)
        
        TODO: Implement parser cho minhngoc.net.vn
        Hiện tại return None, cần update khi có thời gian
        """
        # Placeholder - cần implement
        print(f"  ⚠️ Backup source not implemented yet")
        return None
    
    def _extract_prize(self, soup: BeautifulSoup, prize_type: str) -> Optional[str]:
        """
        Extract một giải thưởng đơn (ĐB, Nhất)
        
        LƯU Ý: Cần update selectors theo cấu trúc thực tế của website
        """
        try:
            # Ví dụ selector - CẦN KIỂM TRA LẠI
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
            print(f"  ⚠️ Error extracting {prize_type}: {e}")
            return None
    
    def _extract_prize_array(
        self, 
        soup: BeautifulSoup, 
        prize_type: str, 
        expected_count: int
    ) -> List[str]:
        """
        Extract các giải có nhiều số (Nhì, Ba, Tư, ...)
        
        Args:
            soup: BeautifulSoup object
            prize_type: Loại giải ('second', 'third', ...)
            expected_count: Số lượng số dự kiến
        
        Returns:
            List of strings
        """
        try:
            # Ví dụ selector - CẦN KIỂM TRA LẠI
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
            
            # Validate count
            if len(numbers) != expected_count:
                print(f"  ⚠️ Expected {expected_count} numbers for {prize_type}, got {len(numbers)}")
            
            return numbers
            
        except Exception as e:
            print(f"  ⚠️ Error extracting {prize_type} array: {e}")
            return []


def test_crawler():
    """Test crawler với ngày hôm qua"""
    from datetime import timedelta
    
    crawler = XSMBCrawler()
    yesterday = date.today() - timedelta(days=1)
    
    print(f"\n{'='*60}")
    print(f"Testing XSMB Crawler")
    print(f"{'='*60}\n")
    
    results = crawler.fetch_results(yesterday)
    
    if results:
        print(f"\n✅ Crawl successful!")
        print(f"\nResults:")
        print(f"  Date: {results['draw_date']}")
        print(f"  Region: {results['region']}")
        print(f"  Special Prize: {results['special_prize']}")
        print(f"  First Prize: {results['first_prize']}")
        print(f"  Second Prize: {results['second_prize']}")
    else:
        print(f"\n❌ Crawl failed!")
        print(f"\n⚠️ LƯU Ý:")
        print(f"  1. Kiểm tra website nguồn có hoạt động không")
        print(f"  2. Cần update CSS selectors nếu website thay đổi cấu trúc")
        print(f"  3. Thử chạy với ngày khác (có thể chưa có kết quả)")


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    test_crawler()
