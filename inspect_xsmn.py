#!/usr/bin/env python3
"""
Inspect XSMN HTML Structure
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# Test với ngày hôm qua
yesterday = datetime.now() - timedelta(days=1)
date_str = yesterday.strftime("%d-%m-%Y")
url = f"https://xskt.com.vn/xsmn/tp-hcm/{date_str}.html"

print(f"Fetching: {url}")
print("=" * 60)

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                 'Chrome/120.0.0.0 Safari/537.36'
}

try:
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Save HTML to file
    with open('debug_xsmn.html', 'w', encoding='utf-8') as f:
        f.write(soup.prettify())
    
    print(f"✅ HTML saved to: debug_xsmn.html")
    print(f"   File size: {len(response.content)} bytes")
    print()
    
    # Look for result tables
    tables = soup.find_all('table', class_='result')
    print(f"Found {len(tables)} table(s) with class='result'")
    
    if tables:
        for i, table in enumerate(tables):
            print(f"\nTable {i+1}:")
            # Check for special prize
            em = table.find('em')
            if em:
                print(f"  Special prize: {em.text.strip()}")
            
            # Check for prize rows
            ps = table.find_all('p')
            print(f"  Found {len(ps)} <p> tags")
    
    print()
    print("=" * 60)
    print("Check debug_xsmn.html for full structure")
    
except Exception as e:
    print(f"❌ Error: {e}")
