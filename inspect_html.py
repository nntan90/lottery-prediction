#!/usr/bin/env python3
"""
Inspect HTML Structure
Lấy HTML từ website để xem cấu trúc thực tế
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# Test với ngày hôm qua
yesterday = datetime.now() - timedelta(days=1)
date_str = yesterday.strftime("%d-%m-%Y")
url = f"https://xskt.com.vn/xsmb/{date_str}.html"

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
    
    # Save HTML to file for inspection
    with open('debug_html.html', 'w', encoding='utf-8') as f:
        f.write(soup.prettify())
    
    print(f"✅ HTML saved to: debug_html.html")
    print(f"   File size: {len(response.content)} bytes")
    print()
    
    # Try to find any table or result structure
    print("Looking for common patterns...")
    print("-" * 60)
    
    # Check for tables
    tables = soup.find_all('table')
    print(f"Found {len(tables)} table(s)")
    
    # Check for divs with common class names
    common_classes = ['result', 'prize', 'number', 'kqxs', 'ketqua']
    for cls in common_classes:
        elements = soup.find_all(class_=lambda x: x and cls in x.lower())
        if elements:
            print(f"Found {len(elements)} elements with class containing '{cls}'")
    
    # Check for specific IDs
    common_ids = ['result', 'kqxs', 'ketqua']
    for id_name in common_ids:
        element = soup.find(id=id_name)
        if element:
            print(f"Found element with id='{id_name}'")
    
    print()
    print("=" * 60)
    print("Open 'debug_html.html' in browser to inspect structure")
    print("Look for:")
    print("  - Table with lottery results")
    print("  - CSS classes for prizes")
    print("  - Structure of number elements")
    print("=" * 60)
    
except Exception as e:
    print(f"❌ Error: {e}")
