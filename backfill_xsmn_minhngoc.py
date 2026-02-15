import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta, datetime
import logging
from src.database.supabase_client import LotteryDB

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("backfill_minhngoc.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize DB
try:
    db = LotteryDB()
except Exception as e:
    logger.error(f"Failed to initialize LotteryDB: {e}")
    raise

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                 'Chrome/120.0.0.0 Safari/537.36'
}

# Province mapping (Vietnamese Name -> Code)
PROVINCE_MAPPING = {
    'TP. HCM': 'tp-hcm',
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
    'Đà Lạt': 'da-lat'
}

def clean_prize_list(text_value):
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

def parse_minhngoc_date(target_date):
    """
    Fetches and parses XSMN data for a specific date from Minh Ngoc.
    Returns a list of dictionaries ready for DB insertion.
    """
    # URL: mien=1 for XSMN (based on inspection)
    url = f"https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=1&ngay={target_date.day}&thang={target_date.month}&nam={target_date.year}"
    logger.info(f"Fetching {url}...")
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        # Check if successful (Minh Ngoc returns 200 even for empty, but we check content)
        if response.status_code != 200:
            logger.error(f"Failed to fetch: {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.content, 'html.parser')
        table = soup.find('table', class_='bkqmiennam')
        
        if not table:
            logger.warning(f"No results table found for {target_date}")
            return []
            
        results = []
        current_province = None
        current_data = {}
        
        # Iterate all rows in the table
        rows = table.find_all('tr')
        
        for row in rows:
            # Check for province header
            td_tinh = row.find('td', class_='tinh')
            if td_tinh:
                # If we were processing a province, save it
                if current_province and current_data:
                    # Validate we have some data
                    if 'special_prize' in current_data:
                        results.append(current_data)
                
                # Start new province
                prov_name = td_tinh.text.strip()
                prov_code = PROVINCE_MAPPING.get(prov_name)
                
                if not prov_code:
                    logger.warning(f"Unknown province name: {prov_name}")
                    current_province = None
                    current_data = {}
                else:
                    current_province = prov_code
                    current_data = {
                        'draw_date': target_date.strftime('%Y-%m-%d'),
                        'region': 'XSMN',
                        'province': prov_code
                    }
                continue
            
            # If no current province, skip parsing prizes
            if not current_province:
                continue
                
            # Parse prizes
            # Mapping class name -> DB column
            # Note: Minh Ngoc classes: giai8, giai7... giaidb
            # DB columns: eighth_prize, seventh_prize... special_prize
            
            # Helper to extract and set
            def extract_prize(class_name, db_field, is_array=True):
                td = row.find('td', class_=class_name)
                if td:
                    text = td.get_text(separator='|')
                    values = clean_prize_list(text)
                    if not values:
                        return
                    
                    if is_array:
                        current_data[db_field] = values
                    else:
                        # For single value fields, take the first one (should be only one)
                        current_data[db_field] = values[0]
            
            extract_prize('giai8', 'eighth_prize', is_array=False) # Single value
            extract_prize('giai7', 'seventh_prize', is_array=False) # XSMB is array(4), XSMN G7 is single value (1) usually?
            # Wait, XSMN G7 is SINGLE value (3 digit). 
            # XSMB G7 is ARRAY (4 values).
            # Minh Ngoc output for XSMN G7: "['giai7']:|578" -> Single
            # So for XSMN, G7 is single. BUT schema uses array?
            # Let's check schema/existing generic crawler.
            # existing crawler treats G7 as array?
            # User schema: seventh_prize TEXT[]
            # So we should store as LIST even if single.
            
            # Re-checking logic:
            # G8: Single (2 digits)
            # G7: Single (3 digits)
            # G6: 3 values (4 digits)
            # G5: Single (4 digits)
            # G4: 7 values (5 digits)
            # G3: 2 values (5 digits)
            # G2: Single (5 digits)
            # G1: Single (5 digits)
            # DB: Single (6 digits)
            
            # DB Schema:
            # special, first, eighth -> VARCHAR (Single)
            # second...seventh -> TEXT[] (Array)
            
            # So:
            # G8 -> eighth_prize (Single)
            # G7 -> seventh_prize (Array - even if 1 value)
            # G6 -> sixth_prize (Array)
            # G5 -> fifth_prize (Array - even if 1 value)
            # ...
            # G2 -> second_prize (Array - even if 1 value)
            # G1 -> first_prize (Single)
            # DB -> special_prize (Single)
            
            # Refined extraction:
            extract_prize('giai8', 'eighth_prize', is_array=False)
            extract_prize('giai7', 'seventh_prize', is_array=True)
            extract_prize('giai6', 'sixth_prize', is_array=True)
            extract_prize('giai5', 'fifth_prize', is_array=True)
            extract_prize('giai4', 'fourth_prize', is_array=True)
            extract_prize('giai3', 'third_prize', is_array=True)
            extract_prize('giai2', 'second_prize', is_array=True)
            extract_prize('giai1', 'first_prize', is_array=False)
            extract_prize('giaidb', 'special_prize', is_array=False)
            
        # Append last province
        if current_province and current_data and 'special_prize' in current_data:
             results.append(current_data)
             
        return results
        
    except Exception as e:
        logger.error(f"Error parsing date {target_date}: {e}")
        return []

def backfill_xsmn():
    start_date = date(2024, 1, 1)
    end_date = date.today()
    
    current_date = start_date
    delta = timedelta(days=1)
    
    total_days = (end_date - start_date).days + 1
    processed_count = 0
    
    print(f"Starting backfill from {start_date} to {end_date} ({total_days} days)")
    
    while current_date <= end_date:
        processed_count += 1
        print(f"[{processed_count}/{total_days}] Processing {current_date}...")
        
        results = parse_minhngoc_date(current_date)
        
        if results:
            print(f"  Found {len(results)} provinces")
            for res in results:
                try:
                    # Upsert
                    db.upsert_draw(res)
                    print(f"    ✅ {res['province']}: Upserted")
                except Exception as e:
                    print(f"    ❌ Error upserting {res['province']}: {e}")
        else:
            print("  ⚠️ No data found")
            
        current_date += delta
        time.sleep(1) # Rate limiting

if __name__ == "__main__":
    backfill_xsmn()
