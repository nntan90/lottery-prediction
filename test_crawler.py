#!/usr/bin/env python3
"""
Test Crawler Locally
Kiểm tra xem crawler có lấy được data từ website không
"""

from datetime import datetime, timedelta
from src.crawler.xsmb_crawler import XSMBCrawler

print("=" * 60)
print("🔍 TESTING XSMB CRAWLER")
print("=" * 60)
print()

crawler = XSMBCrawler()

# Test với 3 ngày gần đây
for i in range(1, 4):
    target_date = datetime.now() - timedelta(days=i)
    
    print(f"\nTesting date: {target_date.date()}")
    print("-" * 60)
    
    results = crawler.fetch_results(target_date.date())
    
    if results:
        print(f"✅ SUCCESS!")
        print(f"   Special Prize: {results.get('special_prize')}")
        print(f"   First Prize: {results.get('first_prize')}")
        print(f"   Second Prize: {results.get('second_prize')}")
    else:
        print(f"❌ FAILED - No data returned")
        print(f"   This means CSS selectors are wrong or website blocked us")

print()
print("=" * 60)
print("If all tests failed, we need to update the crawler selectors")
print("=" * 60)
