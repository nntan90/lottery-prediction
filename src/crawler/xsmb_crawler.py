"""
XSMB Crawler - FIXED VERSION
Crawl lottery results from xskt.com.vn with CORRECT selectors
"""

import requests
from bs4 import BeautifulSoup
from datetime import date
from typing import Optional, Dict
import time

class XSMBCrawler:
    """Crawler for XSMB (Northern Vietnam Lottery) results"""
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/120.0.0.0 Safari/537.36'
        }
    
    def fetch_results(self, target_date: date) -> Optional[Dict]:
        """
        Fetch XSMB results for a specific date
        
        Args:
            target_date: Date to fetch results for
            
        Returns:
            Dictionary with lottery results or None if failed
        """
        try:
            results = self._crawl_from_xskt(target_date)
            if results:
                print(f"✅ Successfully crawled XSMB for {target_date}")
                return results
            else:
                print(f"❌ No data found for {target_date}")
                return None
        except Exception as e:
            print(f"❌ Error crawling XSMB for {target_date}: {e}")
            return None
    
    def _crawl_from_xskt(self, target_date: date) -> Optional[Dict]:
        """Crawl from xskt.com.vn"""
        
        # Format: dd-mm-yyyy
        date_str = target_date.strftime("%d-%m-%Y")
        url = f"https://xskt.com.vn/xsmb/{date_str}.html"
        
        print(f"🔍 Crawling: {url}")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find the XSMB result table
            table = soup.find('table', class_='result', id='MB0')
            
            if not table:
                print(f"  ⚠️ Table not found")
                return None
            
            # Extract special prize (Giải ĐB) - in <em> tag
            special_prize_em = table.find('em')
            if not special_prize_em:
                print(f"  ⚠️ Special prize not found")
                return None
            
            special_prize = special_prize_em.text.strip()
            
            # Extract all <p> tags for other prizes
            prize_ps = table.find_all('p')
            
            if len(prize_ps) < 7:
                print(f"  ⚠️ Expected 7 prize rows, found {len(prize_ps)}")
                return None
            
            # Parse prizes - use regex to split numbers properly
            import re
            
            def parse_numbers(text):
                """Extract all numbers from text, handling <br/> and spaces"""
                # .split() handles whitespace and newlines from <br/> tags perfectly
                return [n for n in text.split() if n.isdigit()]
            
            # G1: 1 number (5 digits)
            first_prize = prize_ps[0].text.strip()
            
            # G2: 2 numbers (5 digits each)
            second_prize = parse_numbers(prize_ps[1].text)
            
            # G3: 6 numbers (5 digits each)
            third_prize = parse_numbers(prize_ps[2].text)
            
            # G4: 4 numbers (5 digits each)
            fourth_prize = parse_numbers(prize_ps[3].text)
            
            # G5: 6 numbers (4 digits each)
            fifth_prize = parse_numbers(prize_ps[4].text)
            
            # G6: 3 numbers (3 digits each)
            sixth_prize = parse_numbers(prize_ps[5].text)
            
            # G7: 4 numbers (2 digits each)
            seventh_prize = parse_numbers(prize_ps[6].text)
            
            # Validate counts
            if len(second_prize) != 2:
                print(f"  ⚠️ G2: expected 2, got {len(second_prize)}")
            if len(third_prize) != 6:
                print(f"  ⚠️ G3: expected 6, got {len(third_prize)}")
            if len(fourth_prize) != 4:
                print(f"  ⚠️ G4: expected 4, got {len(fourth_prize)}")
            if len(fifth_prize) != 6:
                print(f"  ⚠️ G5: expected 6, got {len(fifth_prize)}")
            if len(sixth_prize) != 3:
                print(f"  ⚠️ G6: expected 3, got {len(sixth_prize)}")
            if len(seventh_prize) != 4:
                print(f"  ⚠️ G7: expected 4, got {len(seventh_prize)}")
            
            result = {
                'draw_date': target_date,
                'region': 'XSMB',
                'special_prize': special_prize,
                'first_prize': first_prize,
                'second_prize': second_prize,  # PostgreSQL array
                'third_prize': third_prize,
                'fourth_prize': fourth_prize,
                'fifth_prize': fifth_prize,
                'sixth_prize': sixth_prize,
                'seventh_prize': seventh_prize
            }
            
            print(f"  ✅ Special Prize: {special_prize}")
            print(f"  ✅ First Prize: {first_prize}")
            
            return result
            
        except requests.RequestException as e:
            print(f"  ❌ Request error: {e}")
            return None
        except Exception as e:
            print(f"  ❌ Parse error: {e}")
            return None


# Test function
if __name__ == "__main__":
    from datetime import datetime, timedelta
    
    print("=" * 60)
    print("🧪 TESTING XSMB CRAWLER (FIXED VERSION)")
    print("=" * 60)
    
    crawler = XSMBCrawler()
    
    # Test with yesterday
    yesterday = datetime.now() - timedelta(days=1)
    results = crawler.fetch_results(yesterday.date())
    
    if results:
        print("\n✅ SUCCESS! Results:")
        for key, value in results.items():
            print(f"  {key}: {value}")
    else:
        print("\n❌ FAILED")
