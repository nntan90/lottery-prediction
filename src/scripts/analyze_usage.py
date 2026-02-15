
import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}"

async def analyze():
    print("📊 ANALYZING SYSTEM USAGE...\n")
    
    # Load env
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        print("❌ Missing Supabase credentials in .env")
        return

    supabase = create_client(url, key)
    
    # 1. Check Row Counts
    print("--- 1. Database Records ---")
    tables = ['lottery_draws', 'predictions', 'model_training_logs', 'crawler_logs']
    
    for table in tables:
        try:
            res = supabase.table(table).select('*', count='exact', head=True).execute()
            print(f"📄 {table}: {res.count} records")
        except Exception as e:
            print(f"⚠️ {table}: Error fetching count ({e})")
            
    # 2. Check Storage Bucket
    print("\n--- 2. Storage (Bucket: lottery-models) ---")
    try:
        bucket_id = 'lottery-models'
        # List files (recursive assumption or flat structure for now)
        # Supabase list() returns top level. Our models are in region/ folders or root.
        
        # List root
        total_size = 0
        file_count = 0
        
        res = supabase.storage.from_(bucket_id).list()
        
        # Simple recursive walker (depth=2 limited)
        folders = []
        
        for item in res:
            if item.get('id'): # It's a file
                size = item.get('metadata', {}).get('size', 0)
                total_size += size
                file_count += 1
                # print(f"  - {item['name']} ({format_bytes(size)})")
            else:
                # It's a folder (no id, usually)
                folders.append(item['name'])
        
        # Check subfolders (regions)
        for folder in folders:
            sub_res = supabase.storage.from_(bucket_id).list(folder)
            for item in sub_res:
                 if item.get('id'):
                    size = item.get('metadata', {}).get('size', 0)
                    total_size += size
                    file_count += 1
                    # print(f"  - {folder}/{item['name']} ({format_bytes(size)})")

        print(f"📦 Total Files: {file_count}")
        print(f"💾 Total Size: {format_bytes(total_size)}")
        
    except Exception as e:
        print(f"❌ Error checking storage: {e}")

if __name__ == "__main__":
    asyncio.run(analyze())
