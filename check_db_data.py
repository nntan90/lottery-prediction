from src.database.supabase_client import LotteryDB

db = LotteryDB()
data = db.get_draw_by_date(region='XSMN', draw_date='2024-01-01', province='tp-hcm') # Need to pass date object or string? Client handles date obj.
# fetch_results uses date object. upsert uses string.
# get_draw_by_date expects date object usually? definition says date object.
from datetime import date
d = date(2024, 1, 1)
data = db.get_draw_by_date(d, 'XSMN', 'tp-hcm')

print(f"Data for 2024-01-01 TP.HCM: {data}")
if data:
    print(f"Eighth Prize: {data.get('eighth_prize')}")
