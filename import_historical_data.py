"""
Import Historical Lottery Data from GitHub CSV
Lấy data 2 năm từ GitHub để train ML model
"""

import requests
import pandas as pd
from datetime import datetime
from src.database.supabase_client import LotteryDB
import time

class HistoricalDataImporter:
    """Import historical lottery data from GitHub CSV"""
    
    def __init__(self):
        self.db = LotteryDB()
        # GitHub repo: khiemdoan/vietnam-lottery-xsmb-analysis
        self.csv_url = "https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/main/data/xsmb.csv"
    
    def import_xsmb_from_csv(self, days: int = 730):
        """
        Import XSMB data from GitHub CSV
        
        Args:
            days: Number of days to import (default 730 = 2 years)
        """
        print("=" * 60)
        print(f"📥 Importing {days} days of XSMB historical data...")
        print("=" * 60)
        
        try:
            print(f"\n🔍 Downloading from GitHub...")
            response = requests.get(self.csv_url, timeout=30)
            response.raise_for_status()
            
            # Parse CSV
            from io import StringIO
            df = pd.read_csv(StringIO(response.text))
            
            print(f"✅ Downloaded {len(df)} total records")
            print(f"   Date range: {df['date'].min()} to {df['date'].max()}")
            
            # Get last N days
            df_recent = df.tail(days)
            
            print(f"\n📤 Importing {len(df_recent)} records to Supabase...")
            print(f"   From: {df_recent['date'].min()}")
            print(f"   To: {df_recent['date'].max()}")
            
            # Import to database
            success_count = 0
            failed_count = 0
            skipped_count = 0
            
            for idx, row in df_recent.iterrows():
                try:
                    # Convert row to lottery draw format
                    draw_data = self._convert_csv_row_to_draw(row)
                    
                    if draw_data:
                        self.db.insert_draw(draw_data)
                        success_count += 1
                        
                        if success_count % 100 == 0:
                            print(f"  ✅ Imported {success_count}/{len(df_recent)}...")
                    else:
                        failed_count += 1
                        
                except Exception as e:
                    error_str = str(e).lower()
                    if 'duplicate' in error_str or '23505' in error_str:
                        skipped_count += 1  # Already exists
                        if skipped_count % 100 == 0:
                            print(f"  ⚠️ Skipped {skipped_count} duplicates...")
                    else:
                        failed_count += 1
                        if failed_count <= 5:  # Only show first 5 errors
                            print(f"  ❌ Error at {row['date']}: {e}")
                
                # Rate limiting
                if (success_count + skipped_count) % 200 == 0:
                    time.sleep(1)
            
            print("\n" + "=" * 60)
            print(f"📊 Import Summary:")
            print(f"  ✅ Success: {success_count}/{len(df_recent)}")
            print(f"  ⚠️ Skipped (duplicates): {skipped_count}/{len(df_recent)}")
            print(f"  ❌ Failed: {failed_count}/{len(df_recent)}")
            print(f"  📈 Success rate: {(success_count+skipped_count)/len(df_recent)*100:.1f}%")
            print("=" * 60)
            
            return success_count
            
        except Exception as e:
            print(f"❌ Error downloading CSV: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def _convert_csv_row_to_draw(self, row):
        """
        Convert CSV row to lottery draw format
        
        CSV columns: date, special, prize1, prize2_1, prize2_2, prize3_1-6, 
                     prize4_1-4, prize5_1-6, prize6_1-3, prize7_1-4
        """
        try:
            # Parse date
            draw_date = pd.to_datetime(row['date']).date()
            
            # Parse prizes - convert to strings and create lists
            draw_data = {
                'draw_date': draw_date,
                'region': 'XSMB',
                'special_prize': str(int(row['special'])),
                'first_prize': str(int(row['prize1'])),
                'second_prize': [
                    str(int(row['prize2_1'])), 
                    str(int(row['prize2_2']))
                ],
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
            
            return draw_data
            
        except Exception as e:
            print(f"  ⚠️ Error converting row for {row.get('date', 'unknown')}: {e}")
            return None


def main():
    """Main function to run import"""
    print("\n🚀 Historical Data Import Tool")
    print("=" * 60)
    print("Source: GitHub - khiemdoan/vietnam-lottery-xsmb-analysis")
    print("=" * 60)
    
    importer = HistoricalDataImporter()
    
    # Import 2 years of XSMB data
    days = 730  # 2 years
    
    print(f"\n📌 Will import {days} days (~2 years) of historical data")
    print()
    
    input("Press Enter to continue or Ctrl+C to cancel...")
    
    success = importer.import_xsmb_from_csv(days=days)
    
    if success > 0:
        print(f"\n✅ Import completed! {success} records imported.")
        print("   You can now use this data to train your ML model.")
    else:
        print("\n❌ Import failed. Please check the error messages above.")


if __name__ == "__main__":
    main()
