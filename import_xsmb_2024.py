"""
Import XSMB data từ 01/01/2024 đến hiện tại
Sử dụng GitHub CSV source
"""

import requests
import pandas as pd
from datetime import datetime, date
from src.database.supabase_client import LotteryDB
from io import StringIO
import time

def import_xsmb_date_range(start_date: date, end_date: date):
    """Import XSMB data for specific date range"""
    
    print("=" * 60)
    print(f"📥 Importing XSMB from {start_date} to {end_date}")
    print("=" * 60)
    
    # Download CSV from GitHub
    csv_url = "https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/main/data/xsmb.csv"
    
    try:
        print(f"\n🔍 Downloading CSV from GitHub...")
        response = requests.get(csv_url, timeout=30)
        response.raise_for_status()
        
        # Parse CSV
        df = pd.read_csv(StringIO(response.text))
        df['date'] = pd.to_datetime(df['date']).dt.date
        
        print(f"✅ Downloaded {len(df)} total records")
        
        # Filter by date range
        df_filtered = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
        
        print(f"\n📊 Found {len(df_filtered)} records in date range")
        print(f"   From: {df_filtered['date'].min()}")
        print(f"   To: {df_filtered['date'].max()}")
        
        if len(df_filtered) == 0:
            print("\n❌ No data found in this date range!")
            return 0
        
        # Import to database
        db = LotteryDB()
        success_count = 0
        skipped_count = 0
        failed_count = 0
        
        print(f"\n📤 Importing to Supabase...")
        
        for idx, row in df_filtered.iterrows():
            try:
                # Convert to lottery draw format
                draw_data = {
                    'draw_date': row['date'],
                    'region': 'XSMB',
                    'province': None,  # XSMB doesn't have province
                    'special_prize': str(int(row['special'])),
                    'first_prize': str(int(row['prize1'])),
                    'second_prize': [str(int(row['prize2_1'])), str(int(row['prize2_2']))],
                    'third_prize': [
                        str(int(row['prize3_1'])), str(int(row['prize3_2'])), 
                        str(int(row['prize3_3'])), str(int(row['prize3_4'])), 
                        str(int(row['prize3_5'])), str(int(row['prize3_6']))
                    ],
                    'fourth_prize': [
                        str(int(row['prize4_1'])), str(int(row['prize4_2'])),
                        str(int(row['prize4_3'])), str(int(row['prize4_4']))
                    ],
                    'fifth_prize': [
                        str(int(row['prize5_1'])), str(int(row['prize5_2'])), 
                        str(int(row['prize5_3'])), str(int(row['prize5_4'])), 
                        str(int(row['prize5_5'])), str(int(row['prize5_6']))
                    ],
                    'sixth_prize': [
                        str(int(row['prize6_1'])), str(int(row['prize6_2'])), 
                        str(int(row['prize6_3']))
                    ],
                    'seventh_prize': [
                        str(int(row['prize7_1'])), str(int(row['prize7_2'])),
                        str(int(row['prize7_3'])), str(int(row['prize7_4']))
                    ]
                }
                
                # Upsert (insert or update)
                db.upsert_draw(draw_data)
                success_count += 1
                
                if success_count % 50 == 0:
                    print(f"  ✅ Imported {success_count}/{len(df_filtered)}...")
                    
            except Exception as e:
                error_str = str(e).lower()
                if 'duplicate' in error_str or '23505' in error_str:
                    skipped_count += 1
                else:
                    failed_count += 1
                    if failed_count <= 3:
                        print(f"  ❌ Error at {row['date']}: {e}")
            
            # Rate limiting
            if success_count % 100 == 0:
                time.sleep(0.5)
        
        print("\n" + "=" * 60)
        print(f"📊 Import Summary:")
        print(f"  ✅ Success: {success_count}/{len(df_filtered)}")
        print(f"  ⚠️ Skipped (duplicates): {skipped_count}/{len(df_filtered)}")
        print(f"  ❌ Failed: {failed_count}/{len(df_filtered)}")
        print(f"  📈 Success rate: {(success_count+skipped_count)/len(df_filtered)*100:.1f}%")
        print("=" * 60)
        
        return success_count
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 0


def main():
    """Main function"""
    print("\n🚀 XSMB Import Tool - Date Range")
    print("=" * 60)
    
    # Date range: 2024-01-01 to today
    start_date = date(2024, 1, 1)
    end_date = datetime.now().date()
    
    total_days = (end_date - start_date).days + 1
    
    print(f"\n📅 Date range:")
    print(f"   From: {start_date}")
    print(f"   To: {end_date}")
    print(f"   Total days: {total_days}")
    print()
    print("⚠️  Note: This will UPSERT data (update if exists, insert if new)")
    print()
    
    input("Press Enter to continue or Ctrl+C to cancel...")
    
    success = import_xsmb_date_range(start_date, end_date)
    
    if success > 0:
        print(f"\n✅ Import completed! {success} records imported.")
    else:
        print("\n❌ Import failed. Please check the error messages above.")


if __name__ == "__main__":
    main()
