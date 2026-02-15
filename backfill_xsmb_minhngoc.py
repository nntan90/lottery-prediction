import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta, datetime
import time
import os
from src.database.supabase_client import LotteryDB

# Initialize DB
try:
    db = LotteryDB()
except Exception as e:
    print(f"Error initializing DB: {e}")
    exit(1)

# XSMB Schedule (Weekday -> Province Slug)
# 0: Mon -> Ha Noi
# 1: Tue -> Quang Ninh
# 2: Wed -> Bac Ninh
# 3: Thu -> Ha Noi
# 4: Fri -> Hai Phong
# 5: Sat -> Nam Dinh
# 6: Sun -> Thai Binh
XSMB_SCHEDULE = {
    0: 'ha-noi',
    1: 'quang-ninh',
    2: 'bac-ninh',
    3: 'ha-noi',
    4: 'hai-phong',
    5: 'nam-dinh',
    6: 'thai-binh'
}

def clean_prize_list(text_value):
    """Cleans text explicitly associated with numbers"""
    if not text_value:
        return []
    # Replace common separators with pipe
    text_value = text_value.replace('-', '|')
    parts = text_value.split('|')
    # Filter for digits only (Minh Ngoc might have symbols?)
    # existing data in DB implies just numbers string.
    cleaned = []
    for p in parts:
        p = p.strip()
        if p and p.isdigit():
            cleaned.append(p)
    return cleaned

def parse_minhngoc_xsmb(target_date):
    url = f"https://www.minhngoc.net/tra-cuu-ket-qua-xo-so.html?mien=2&ngay={target_date.day}&thang={target_date.month}&nam={target_date.year}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            print(f"  ❌ Failed to fetch: {response.status_code}")
            return None
            
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Determine table (try refined selector first)
        table = soup.find('table', class_='bkqmienbac')
        if not table:
            # Fallback
            table = soup.find('table', class_='bkqmiennam')
            
        if not table:
            print("  ⚠️ Table not found")
            return None
            
        # Determine Province based on Schedule
        weekday = target_date.weekday()
        province_slug = XSMB_SCHEDULE.get(weekday, 'ha-noi') # Fallback
        
        # Parsing Logic
        # Minh Ngoc XSMB table layout:
        # Rows have logic like:
        # <td class="giai_db">...</td>
        # <td class="giai_nhat">...</td>
        # OR just <td> inside <tr>
        
        # Let's try to map by class names if possible, or by row index?
        # Minh Ngoc classes for XSMB:
        # giaidb, giai1, giai2, giai3, giai4, giai5, giai6, giai7
        # Note: distinct from XSMN which has giai8.
        
        prizes = {}
        
        def extract_prize(class_name, db_field, is_array=True):
            # Search for ANY td with this class in the table
            td = table.find('td', class_=class_name)
            if td:
                text = td.get_text(separator='|')
                values = clean_prize_list(text)
                if not values:
                    return
                
                if is_array:
                    prizes[db_field] = values
                else:
                    prizes[db_field] = values[0]
        
        extract_prize('giaidb', 'special_prize', is_array=False)
        extract_prize('giai1', 'first_prize', is_array=False) # G1 is single in XSMB? usually 1 value (5 digits)
        extract_prize('giai2', 'second_prize', is_array=True)
        extract_prize('giai3', 'third_prize', is_array=True)
        extract_prize('giai4', 'fourth_prize', is_array=True)
        extract_prize('giai5', 'fifth_prize', is_array=True)
        extract_prize('giai6', 'sixth_prize', is_array=True)
        extract_prize('giai7', 'seventh_prize', is_array=True)
        
        # Verify
        if not prizes.get('special_prize'):
            print("  ⚠️ Special prize not found")
            return None

        # Correction for First Prize: DB expects String, but if it comes as list...
        # clean_prize_list returns list.
        # If I used is_array=False, it assigns values[0].
        # G1 in XSMB is 1 value. Correct.
        
        result = {
            'draw_date': target_date,
            'region': 'XSMB',
            'province': province_slug,
            **prizes
        }
        
        return result

    except Exception as e:
        print(f"  ❌ Error parsing: {e}")
        return None

def backfill_xsmb():
    start_date = date(2024, 1, 1)
    end_date = date(2026, 2, 14) # As requested
    
    current_date = start_date
    delta = timedelta(days=1)
    
    total_days = (end_date - start_date).days + 1
    processed_count = 0
    
    print(f"Starting XSMB backfill from {start_date} to {end_date} ({total_days} days)")
    
    while current_date <= end_date:
        processed_count += 1
        print(f"[{processed_count}/{total_days}] Processing {current_date}...")
        
        res = parse_minhngoc_xsmb(current_date)
        
        if res:
            try:
                db.upsert_draw(res)
                print(f"    ✅ {res['province']}: Upserted")
            except Exception as e:
                print(f"    ❌ Error upserting: {e}")
        else:
            print("  ⚠️ No data found")
            
        current_date += delta
        time.sleep(1) # Rate limit

if __name__ == "__main__":
    backfill_xsmb()
