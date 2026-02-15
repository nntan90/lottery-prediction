
import asyncio
import os
import re
from datetime import datetime, timedelta, date
from dotenv import load_dotenv
from supabase import create_client

class ModelCleanup:
    def __init__(self):
        load_dotenv()
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY") # Needs service key to delete
        
        if not url or not key:
            raise ValueError("Missing Supabase credentials")
            
        self.supabase = create_client(url, key)
        self.bucket = "lottery-models"
        self.retention_days = 30
        
    async def get_active_models(self):
        """Get models used in predictions in the last N days"""
        cutoff = (date.today() - timedelta(days=self.retention_days)).isoformat()
        
        # We want models that HAVE been used recently
        try:
            res = self.supabase.table("predictions")\
                .select("model_version")\
                .gte("prediction_date", cutoff)\
                .execute()
            
            # Use raw filename or base version string?
            # Assuming model_version stores the filename like 'lstm_xsmb_all_20240215.h5'
            active_models = set(r['model_version'] for r in res.data if r['model_version'])
            print(f"📊 Found {len(active_models)} active models used since {cutoff}")
            return active_models
        except Exception as e:
            print(f"⚠️ Error fetching active models: {e}")
            return set()

    def parse_date_from_filename(self, filename):
        """Extract date from filename: lstm_region_province_YYYYMMDD.h5"""
        # Regex to find 8 digits at end of string before .h5
        match = re.search(r'_(\d{8})\.h5$', filename)
        if match:
            try:
                date_str = match.group(1)
                return datetime.strptime(date_str, "%Y%m%d").date()
            except ValueError:
                return None
        return None

    async def cleanup(self, dry_run=False):
        print(f"🧹 STARTING MODEL CLEANUP (Retention: {self.retention_days} days)")
        if dry_run:
            print("👀 DRY RUN MODE - No files will be deleted")
        
        active_models = await self.get_active_models()
        cutoff_date = date.today() - timedelta(days=self.retention_days)
        
        # List all files in bucket
        # Note: listing at root. If you have folders, need to walk them.
        # Based on train_manager, we store in folders: 'xsmb/filename.h5', 'xsmn/filename.h5'
        
        regions = ['xsmb', 'xsmn'] # Known folders
        total_deleted = 0
        total_freed = 0
        
        for folder in regions:
            try:
                files = self.supabase.storage.from_(self.bucket).list(folder)
            except Exception as e:
                print(f"⚠️ Could not list folder {folder}: {e}")
                continue
                
            print(f"\n📂 Checking folder: {folder} ({len(files)} files)")
            
            for file in files:
                file_name = file['name']
                if not file_name.endswith('.h5'):
                    continue
                
                file_path = f"{folder}/{file_name}"
                
                # Check 1: Is it in active list?
                # The prediction table might store just the filename 'lstm_....h5' 
                # or full path? train_manager stores filename in 'model_version'
                if file_name in active_models:
                    # print(f"  ✅ KEEP (Active): {file_name}")
                    continue
                
                # Check 2: Is it recently created (based on filename)?
                # Even if not used yet (e.g. trained yesterday), we shouldn't delete immediately
                file_date = self.parse_date_from_filename(file_name)
                
                if file_date and file_date >= cutoff_date:
                    # print(f"  ✅ KEEP (New): {file_name} ({file_date})")
                    continue
                
                # If we are here, it's OLD and INACTIVE
                size_mb = file['metadata']['size'] / (1024*1024)
                print(f"  🗑️ DELETE CANDIDATE: {file_name} (Size: {size_mb:.2f} MB, Date: {file_date})")
                
                if not dry_run:
                    try:
                        self.supabase.storage.from_(self.bucket).remove([file_path])
                        print(f"     🔥 Deleted {file_path}")
                        total_deleted += 1
                        total_freed += file['metadata']['size']
                    except Exception as e:
                        print(f"     ❌ Failed to delete: {e}")
        
        freed_mb = total_freed / (1024*1024)
        print(f"\n✨ Cleanup Complete. Deleted {total_deleted} files. Freed {freed_mb:.2f} MB.")

if __name__ == "__main__":
    # Default to False (Live Run) if run directly? 
    # Or strict safety? Let's use env var or argument, assume live for script.
    import sys
    dry_run = '--dry-run' in sys.argv
    
    cleaner = ModelCleanup()
    asyncio.run(cleaner.cleanup(dry_run=dry_run))
