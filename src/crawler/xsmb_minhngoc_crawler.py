"""
XSMB Crawler for MinhNgoc.net.vn
Crawls XSMB lottery results with province information
"""

import requests
from bs4 import BeautifulSoup
from datetime import date
from typing import Dict, Optional, List
import re


class XSMBMinhNgocCrawler:
    """Crawler for XSMB results from minhngoc.net.vn"""
    
    def __init__(self):
        self.base_url = "https://www.minhngoc.net/ket-qua-xo-so/mien-bac/{date}.html"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Province mapping
        self.province_map = {
            'Hà Nội': 'ha-noi',
            'Hải Phòng': 'hai-phong',
            'Bắc Ninh': 'bac-ninh',
            'Nam Định': 'nam-dinh',
            'Thái Bình': 'thai-binh',
            'Quảng Ninh': 'quang-ninh'
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
            result = self._crawl_from_minhngoc(target_date)
            return result
        except Exception as e:
            print(f"❌ Error fetching XSMB for {target_date}: {e}")
            return None
    
    def _crawl_from_minhngoc(self, target_date: date) -> Optional[Dict]:
        """Crawl from minhngoc.net.vn"""
        
        # Format date as dd-mm-yyyy
        date_str = target_date.strftime('%d-%m-%Y')
        url = self.base_url.format(date=date_str)
        
        print(f"🔍 Crawling: {url}")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find result table - look for table containing target date
            # Format: "18/01/2024" or "Ngày: 18/01/2024"
            # Note: Page may have multiple tables, we want the LAST one (most specific)
            target_date_str = target_date.strftime('%d/%m/%Y')
            matching_tables = []
            
            for table in soup.find_all('table'):
                text = table.get_text()
                # Check if table contains both the date and prize info
                if target_date_str in text and 'Giải' in text and ('ĐB' in text or 'Đặc biệt' in text):
                    matching_tables.append(table)
            
            if not matching_tables:
                print(f"  ❌ Result table not found for {target_date_str}")
                return None
            
            # Use the LAST matching table (most specific to target date)
            result_table = matching_tables[-1]
            
            # Extract province from day of week
            province = self._extract_province(target_date)
            print(f"  📍 Province: {province}")
            
            # Extract prizes
            prizes = self._extract_prizes(result_table)
            
            if not prizes:
                print(f"  ❌ Failed to extract prizes")
                return None
            
            # Build result
            result = {
                'draw_date': target_date,
                'region': 'XSMB',
                'province': province,
                **prizes
            }
            
            print(f"  ✅ Special Prize: {result['special_prize']}")
            print(f"  ✅ First Prize: {result['first_prize']}")
            print(f"✅ Successfully crawled XSMB for {target_date}")
            
            return result
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            return None
    
    def _extract_province(self, target_date: date) -> str:
        """
        Extract province based on day of week
        XSMB has fixed schedule:
        - Monday: Hà Nội
        - Tuesday: Quảng Ninh
        - Wednesday: Bắc Ninh
        - Thursday: Hà Nội
        - Friday: Hải Phòng
        - Saturday: Nam Định
        - Sunday: Thái Bình
        """
        day_of_week = target_date.weekday()  # 0=Monday, 6=Sunday
        
        province_schedule = {
            0: 'ha-noi',      # Monday
            1: 'quang-ninh',  # Tuesday
            2: 'bac-ninh',    # Wednesday
            3: 'ha-noi',      # Thursday
            4: 'hai-phong',   # Friday
            5: 'nam-dinh',    # Saturday
            6: 'thai-binh'    # Sunday
        }
        
        return province_schedule.get(day_of_week, 'ha-noi')
    
    def _extract_prizes(self, table) -> Optional[Dict]:
        """
        Extract all prizes from table
        XSMB has specific number formats:
        - ĐB, G1: 5 digits
        - G2-G5: 5 digits each
        - G6: 3 digits each (3 numbers)
        - G7: 2 digits each (4 numbers)
        """
        
        try:
            prizes = {}
            
            # Find all rows
            rows = table.find_all('tr')
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2:
                    continue
                
                # Get prize label
                label_cell = cells[0]
                label = label_cell.get_text().strip()
                
                # Get prize numbers from cells
                number_cells = cells[1:]
                cell_text = ''
                for cell in number_cells:
                    cell_text += cell.get_text().strip()
                
                # Remove all non-digit characters
                digits_only = re.sub(r'\D', '', cell_text)
                
                # Map to prize keys with appropriate chunking
                if 'ĐB' in label or 'Đặc biệt' in label:
                    if len(digits_only) >= 5:
                        prizes['special_prize'] = digits_only[:5]
                
                elif 'Giải nhất' in label or 'G1' in label or 'Giải 1' in label:
                    if len(digits_only) >= 5:
                        prizes['first_prize'] = digits_only[:5]
                
                elif 'Giải nhì' in label or 'G2' in label or 'Giải 2' in label:
                    # 2 numbers x 5 digits = 10 digits
                    if len(digits_only) >= 10:
                        prizes['second_prize'] = [
                            digits_only[0:5],
                            digits_only[5:10]
                        ]
                
                elif 'Giải ba' in label or 'G3' in label or 'Giải 3' in label:
                    # 6 numbers x 5 digits = 30 digits
                    if len(digits_only) >= 30:
                        prizes['third_prize'] = [
                            digits_only[i:i+5] for i in range(0, 30, 5)
                        ]
                
                elif 'Giải tư' in label or 'G4' in label or 'Giải 4' in label:
                    # 4 numbers x 4 digits = 16 digits
                    if len(digits_only) >= 16:
                        prizes['fourth_prize'] = [
                            digits_only[i:i+4] for i in range(0, 16, 4)
                        ]
                
                elif 'Giải năm' in label or 'G5' in label or 'Giải 5' in label:
                    # 6 numbers x 4 digits = 24 digits
                    if len(digits_only) >= 24:
                        prizes['fifth_prize'] = [
                            digits_only[i:i+4] for i in range(0, 24, 4)
                        ]
                
                elif 'Giải sáu' in label or 'G6' in label or 'Giải 6' in label:
                    # 3 numbers x 3 digits = 9 digits
                    if len(digits_only) >= 9:
                        prizes['sixth_prize'] = [
                            digits_only[i:i+3] for i in range(0, 9, 3)
                        ]
                
                elif 'Giải bảy' in label or 'G7' in label or 'Giải 7' in label:
                    # 4 numbers x 2 digits = 8 digits
                    if len(digits_only) >= 8:
                        prizes['seventh_prize'] = [
                            digits_only[i:i+2] for i in range(0, 8, 2)
                        ]
            
            # Validate required prizes
            if 'special_prize' not in prizes or 'first_prize' not in prizes:
                return None
            
            # Set defaults for missing prizes
            prizes.setdefault('second_prize', [])
            prizes.setdefault('third_prize', [])
            prizes.setdefault('fourth_prize', [])
            prizes.setdefault('fifth_prize', [])
            prizes.setdefault('sixth_prize', [])
            prizes.setdefault('seventh_prize', [])
            
            return prizes
            
        except Exception as e:
            print(f"  ❌ Error extracting prizes: {e}")
            return None
