"""
Training Manager
Orchestrates the conditional training workflow.
"""

import sys
import os
import json
from datetime import datetime, timedelta, date

from src.database.supabase_client import LotteryDB
from src.models.lstm_model import LotteryLSTM
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
import numpy as np
import traceback
import asyncio

class TrainingManager:
    def __init__(self):
        self.db = LotteryDB()
        self.storage = LotteryStorage()
        self.notifier = LotteryNotifier()
        
    async def check_performance(self, region: str, days: int = 14) -> bool:
        """
        Check if we need to retrain.
        Condition: No wins in the last `days`.
        Returns: True if retraining is needed.
        """
        # Fetch verified predictions
        # Warning: This assumes 'is_correct' is updated by verification script
        
        # Simple check: Query predictions with is_correct=True in last 14 days
        # Since we don't have a direct method, we'll fetch recent metrics or raw predictions
        # Let's add a method to DB client later or just use direct query here if possible, 
        # but for now let's use a simple query approach via the client if we extended it.
        
        # Extended approach: Fetch predictions from DB
        try:
            # We need to query the 'predictions' table directly
            # This requires exposing supabase client or adding a method
            response = self.db.supabase.table("predictions")\
                .select("is_correct")\
                .eq("region", region)\
                .gte("created_at", (datetime.now() - timedelta(days=days)).isoformat())\
                .eq("is_correct", True)\
                .execute()
                
            win_count = len(response.data)
            print(f"[{region}] Wins in last {days} days: {win_count}")
            
            if win_count == 0:
                print(f"⚠️ Performance drop! Triggering retraining for {region}")
                return True
            return False
            
        except Exception as e:
            print(f"❌ Error checking performance: {e}")
            # If error, maybe default to NOT training to avoid loops, or default to YES?
            # Let's default to False to be safe, but log error
            return False

    async def train_model_for_province(self, region: str, province: str = None):
        """Train model for a specific province/region"""
        try:
            print(f"\n🚀 Starting training for {region} - {province or 'All'}")
            
            # 1. Fetch Data (2 years)
            data = self.db.get_historical_data(region, days=730, province=province)
            if len(data) < 60:
                print(f"⚠️ Not enough data for {province}. Skipping.")
                return

            print(f"📊 Loaded {len(data)} records")

            # 2. Prepare Data
            lstm = LotteryLSTM(sequence_length=60)
            X, y = lstm.prepare_data(data)
            
            if len(X) == 0:
                print("⚠️ No data chunks created. Skipping.")
                return

            # 3. Train
            # Reshape X for first build
            X = np.reshape(X, (X.shape[0], X.shape[1], 1))
            lstm.build_model((X.shape[1], 1))
            
            history = lstm.train(X, y, epochs=50, batch_size=32)
            
            # 4. Save Model
            timestamp = datetime.now().strftime("%Y%m%d")
            prov_slug = province if province else "all"
            filename = f"lstm_{region.lower()}_{prov_slug}_{timestamp}.h5"
            local_path = f"models/{filename}"
            
            os.makedirs("models", exist_ok=True)
            lstm.model.save(local_path)
            
            # 5. Upload to Storage
            storage_path = f"{region.lower()}/{filename}"
            self.storage.upload_model(local_path, storage_path)
            
            # 6. Log to DB
            metrics = {
                "loss": history.history['loss'][-1],
                "val_loss": history.history['val_loss'][-1] if 'val_loss' in history.history else 0
            }
            
            log_data = {
                "region": region,
                "province": province,
                "model_version": filename,
                "data_range_start": data[-1]['draw_date'], # Oldest
                "data_range_end": data[0]['draw_date'],    # Newest
                "training_params": {"epochs": 50, "batch_size": 32},
                "metrics": metrics,
                "model_path": storage_path,
                "trigger_reason": "performance_drop"
            }
            
            self.db.supabase.table("model_training_logs").insert(log_data).execute()
            
            # Clean up
            if os.path.exists(local_path):
                os.remove(local_path)
                
            return filename, metrics

        except Exception as e:
            print(f"❌ Training failed for {province}: {e}")
            traceback.print_exc()
            return None, None

    async def run(self):
        """Main execution flow"""
        regions = ['XSMB', 'XSMN']
        report = []
        
        for region in regions:
            should_train = await self.check_performance(region)
            
            # FORCE TRAINING FOR FIRST RUN? 
            # User might want to run this manually first.
            # Let's assume if it's run manually via workflow_dispatch, we force it?
            # For now, stick to logic: if no wins -> train.
            
            if should_train:
                if region == 'XSMB':
                    model_file, metrics = await self.train_model_for_province('XSMB')
                    if model_file:
                        report.append(f"✅ XSMB: Trained {model_file} (Loss: {metrics['loss']:.4f})")
                
                elif region == 'XSMN':
                    # Train for top provinces or all? 
                    # Training for ALL 21 provinces is heavy.
                    # Let's train for a few key ones or just loop all.
                    # For V2, let's limit to active ones or just loop provided list.
                    from src.crawler.xsmn_crawler import XSMNCrawler
                    crawler = XSMNCrawler()
                    # Flatten schedule to get unique provinces
                    all_provinces = set()
                    for provs in crawler.PROVINCE_SCHEDULE.values():
                        all_provinces.update(provs)
                    
                    for prov in all_provinces:
                        model_file, metrics = await self.train_model_for_province('XSMN', prov)
                        if model_file:
                            report.append(f"✅ XSMN-{prov}: Trained (Loss: {metrics['loss']:.4f})")
                            
        # Notify
        if report:
            msg = "🧠 <b>Weekly Training Report</b>\n\n" + "\n".join(report)
            await self.notifier.send_message(msg)
        else:
             print("✨ No training needed (Performance is good or no data)")

if __name__ == "__main__":
    manager = TrainingManager()
    asyncio.run(manager.run())
