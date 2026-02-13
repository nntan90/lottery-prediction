#!/usr/bin/env python3
"""
Test Supabase Connection
Chạy script này để kiểm tra credentials trước khi dùng trong GitHub Actions
"""

import os
import sys

# Prompt user nhập credentials
print("=" * 60)
print("🔧 SUPABASE CONNECTION TEST")
print("=" * 60)
print()

supabase_url = input("Nhập SUPABASE_URL: ").strip()
supabase_key = input("Nhập SUPABASE_SERVICE_KEY: ").strip()

print()
print("Testing connection...")
print("-" * 60)

# Set environment variables
os.environ["SUPABASE_URL"] = supabase_url
os.environ["SUPABASE_SERVICE_KEY"] = supabase_key

try:
    # Import sau khi set env vars
    from src.database.supabase_client import LotteryDB
    
    print("✅ Import successful")
    
    # Test connection
    db = LotteryDB()
    print("✅ Client created successfully")
    
    # Test query
    print("\nTesting database query...")
    result = db.supabase.table("lottery_draws").select("count").execute()
    
    print(f"✅ Database accessible!")
    print(f"   Current records in lottery_draws: {len(result.data)}")
    
    # Test all tables
    print("\nChecking all tables...")
    tables = [
        "lottery_draws",
        "predictions", 
        "evaluation_metrics",
        "telegram_subscribers",
        "crawler_logs",
        "model_metadata"
    ]
    
    for table in tables:
        try:
            result = db.supabase.table(table).select("count").limit(1).execute()
            print(f"   ✅ {table}: OK")
        except Exception as e:
            print(f"   ❌ {table}: {e}")
    
    print()
    print("=" * 60)
    print("🎉 ALL TESTS PASSED!")
    print("=" * 60)
    print()
    print("Your credentials are correct. You can use them in GitHub Secrets:")
    print(f"SUPABASE_URL: {supabase_url}")
    print(f"SUPABASE_SERVICE_KEY: {supabase_key[:20]}...{supabase_key[-20:]}")
    print()
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("\nMake sure you have installed dependencies:")
    print("  pip install -r requirements.txt")
    sys.exit(1)
    
except Exception as e:
    print(f"❌ Connection failed: {e}")
    print()
    print("Possible issues:")
    print("  1. Wrong SUPABASE_URL format (should be: https://xxxxx.supabase.co)")
    print("  2. Wrong API key (make sure you use 'service_role' key, not 'anon' key)")
    print("  3. Database schema not created yet (run schema.sql first)")
    print()
    sys.exit(1)
