#!/usr/bin/env python3
"""
Inspect minhngoc.net.vn HTML structure for backup crawler
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

yesterday = datetime.now() - timedelta(days=1)
date_str = yesterday.strftime("%d-%m-%Y")

# Test XSMB
url_xsmb = f"https://www.minhngoc.net.vn/xo-so-mien-bac/xsmb-{date_str}.html"
print(f"Testing XSMB: {url_xsmb}")
print("=" * 60)

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                 'Chrome/120.0.0.0 Safari/537.36'
}

try:
    response = requests.get(url_xsmb, headers=headers, timeout=15)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    with open('debug_minhngoc_xsmb.html', 'w', encoding='utf-8') as f:
        f.write(soup.prettify())
    
    print(f"✅ XSMB HTML saved: debug_minhngoc_xsmb.html")
    print(f"   Size: {len(response.content)} bytes")
    
    # Look for result patterns
    tables = soup.find_all('table')
    print(f"   Found {len(tables)} tables")
    
    # Look for common result classes
    result_divs = soup.find_all(class_=lambda x: x and ('result' in x.lower() or 'kqxs' in x.lower()))
    print(f"   Found {len(result_divs)} result-related divs")
    
except Exception as e:
    print(f"❌ XSMB Error: {e}")

print()

# Test XSMN
url_xsmn = f"https://www.minhngoc.net.vn/xo-so-mien-nam/xsmn-{date_str}.html"
print(f"Testing XSMN: {url_xsmn}")
print("=" * 60)

try:
    response = requests.get(url_xsmn, headers=headers, timeout=15)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.content, 'html.parser')
    
    with open('debug_minhngoc_xsmn.html', 'w', encoding='utf-8') as f:
        f.write(soup.prettify())
    
    print(f"✅ XSMN HTML saved: debug_minhngoc_xsmn.html")
    print(f"   Size: {len(response.content)} bytes")
    
    tables = soup.find_all('table')
    print(f"   Found {len(tables)} tables")
    
    result_divs = soup.find_all(class_=lambda x: x and ('result' in x.lower() or 'kqxs' in x.lower()))
    print(f"   Found {len(result_divs)} result-related divs")
    
except Exception as e:
    print(f"❌ XSMN Error: {e}")

print()
print("=" * 60)
print("Check debug_minhngoc_*.html files to analyze structure")
