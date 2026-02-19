"""
Supabase Storage Utils
Handle upload/download of model files.
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

class LotteryStorage:
    def __init__(self):
        load_dotenv()
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        self.bucket = "models"   # Supabase Storage bucket for V3 .pkl files
        
        if not url or not key:
            raise ValueError("Missing Supabase credentials")
            
        self.supabase: Client = create_client(url, key)

    def upload_model(self, local_path: str, storage_path: str):
        """Upload .h5 file to storage"""
        try:
            with open(local_path, 'rb') as f:
                self.supabase.storage.from_(self.bucket).upload(
                    file=f,
                    path=storage_path,
                    file_options={"content-type": "application/octet-stream", "upsert": "true"}
                )
            print(f"✅ Uploaded {local_path} to {self.bucket}/{storage_path}")
            return True
        except Exception as e:
            print(f"❌ Upload failed: {e}")
            return False

    def download_model(self, storage_path: str, local_path: str):
        """Download .h5 file from storage"""
        try:
            with open(local_path, 'wb+') as f:
                res = self.supabase.storage.from_(self.bucket).download(storage_path)
                f.write(res)
            print(f"✅ Downloaded {storage_path} to {local_path}")
            return True
        except Exception as e:
            print(f"❌ Download failed: {e}")
            return False
