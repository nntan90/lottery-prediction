from src.database.supabase_client import LotteryDB
import time

def delete_xsmn_data():
    print("⚠️  WARNING: This will delete ALL XSMN data from the database.")
    print("Initializing LotteryDB...")
    db = LotteryDB()
    
    try:
        print("Executing delete operation for region='XSMN'...")
        # Delete all records where region is XSMN
        # Note: supabase-py delete() returns the deleted data
        response = db.supabase.table("lottery_draws").delete().eq("region", "XSMN").execute()
        
        deleted_count = len(response.data) if response.data else 0
        print(f"✅ Successfully deleted {deleted_count} XSMN records.")
        
    except Exception as e:
        print(f"❌ Error deleting data: {e}")

if __name__ == "__main__":
    delete_xsmn_data()
