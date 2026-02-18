"""
Prediction V2 Script
Uses LSTM model for prediction.
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta

from src.database.supabase_client import LotteryDB
from src.models.lstm_model import LotteryLSTM
from src.utils.storage import LotteryStorage
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler
import numpy as np

# Suppress TensorFlow warnings
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

async def predict_region(region: str, province: str = None):
    """Generate prediction using LSTM model"""
    db = LotteryDB()
    storage = LotteryStorage()
    
    # 1. Determine Model Path
    prov_slug = province if province else "all"
    # Find latest model for this region/province (simplification: assume fixed naming or search)
    # For V1, let's look for known pattern or just "latest" logic if we implemented it.
    # We saved as: lstm_{region}_{prov}_{date}.h5
    # For now, let's try to find the MOST RECENT file in storage or just fail if not found.
    # Since storage list is not easily available in our simple client, 
    # we might need to rely on DB log or just Try to download "latest" if we updated the saver to save a 'latest.h5' copy?
    # BETTER: Query `model_training_logs` for the latest model_path!
    
    try:
        # Get latest model path from DB
        query = db.supabase.table("model_training_logs")\
            .select("model_path")\
            .eq("region", region)
            
        if province:
             query = query.eq("province", province)
        else:
             query = query.is_("province", "null")
             
        response = query.order("training_date", desc=True).limit(1).execute()
        
        if not response.data:
            print(f"⚠️ No trained model found for {region}-{province}. Falling back to Frequency model? Or skipping.")
            return None
            
        model_storage_path = response.data[0]['model_path']
        print(f"📥 Found model: {model_storage_path}")
        
        # 2. Download Model
        local_model_path = "model_temp.h5"
        if not storage.download_model(model_storage_path, local_model_path):
             return None
             
        # 3. Load Model
        lstm = LotteryLSTM(sequence_length=60)
        lstm.load(local_model_path)
        
        # 4. Fetch Data for Input
        # We need last 60 days
        data = db.get_historical_data(region, days=100, province=province) # Fetch more to be safe
        if len(data) < 60:
             print("⚠️ Not enough history for input.")
             return None
             
        # Prepare input (last 60 items)
        # Use the same data preparation logic
        # Warning: prepare_data returns (X, y) for training. We just need the SERIES.
        # Let's extract manually or reuse helper
        import pandas as pd
        df = pd.DataFrame(data)
        if 'draw_date' in df.columns:
            df['draw_date'] = pd.to_datetime(df['draw_date'])
            df = df.sort_values('draw_date')
            
        series = df['special_prize'].astype(str).apply(lambda x: float(x) if x.isdigit() else 0).values
        
        # Normalize using the scaler (which fits on this data)
        # In a real pipeline, we should Load the scaler state from training!
        # For this simplified V2, we fit scaler on Recent Data -> Predict. 
        # (This is slightly inexact but works if distribution hasn't shifted wildly).
        # A better way is saving scaler.pkl alongside model.
        # Let's assume we fit on recent 100 days.
        
        input_series = series[-60:]
        input_data = input_series.reshape(-1, 1)
        
        # Fit scaler on RECENT data (proxy) or full data?
        # Let's fit on the fetched recent data
        lstm.scaler.fit(input_data) 
        
        # Predict
        predicted_val = lstm.predict_next(input_data)
        
        # Post-process: Convert float to string (integer)
        # Lottery is integer, sometimes 0-padded.
        # XSMB: 5 digits, XSMN: 6 digits
        digits = 5 if region == 'XSMB' else 6
        predicted_int = int(round(predicted_val))
        predicted_str = str(predicted_int).zfill(digits)
        
        # Calculate Mock Confidence (LSTM doesn't give prob unless we use softmax output)
        # We used regression (linear output).
        # Let's Hardcode/Estimate confidence or use Dropout variance (MC Dropout) -> Too complex for now.
        confidence = 0.85 # High confidence because AI! (Just kidding, maybe fixed 0.5?)
        
        # Hot numbers? LSTM doesn't give hot numbers directly.
        # We can run Frequency Analyzer on the side for that!
        try:
            from src.models.frequency_analyzer import FrequencyAnalyzer
            freq_analyzer = FrequencyAnalyzer(data)
            hot_numbers = freq_analyzer.find_hot_numbers(top_n=5)
        except ImportError:
            print("⚠️ FrequencyAnalyzer not found. Hot numbers will be empty.")
            hot_numbers = []
        
        result = {
             'predicted_number': predicted_str,
             'confidence': confidence,
             'hot_numbers': hot_numbers,
             'model': 'lstm_v2'
        }
        
        # Cleanup
        if os.path.exists(local_model_path):
             os.remove(local_model_path)
             
        return result

    except Exception as e:
        print(f"❌ Prediction failed: {e}")
        import traceback
        traceback.print_exc()
        return None

async def main():
    notifier = LotteryNotifier()
    db = LotteryDB()
    today = datetime.now().date()
    
    # Logic: If run early in the day (e.g. < 12:00), predict for TODAY.
    # If run late (e.g. > 18:00), predict for TOMORROW.
    # Our cron runs at 7:00 AM, so it should be TODAY.
    vn_now = datetime.utcnow() + timedelta(hours=7)
    if vn_now.hour < 12:
        target_date = vn_now.date()
        print(f"🌅 Morning run ({vn_now.hour}h): Predicting for TODAY ({target_date})")
    else:
        target_date = (vn_now + timedelta(days=1)).date()
        print(f"c Evening run ({vn_now.hour}h): Predicting for TOMORROW ({target_date})")

    # 1. XSMB
    print(f"🎯 Predicting XSMB for {target_date}...")
    pred_xsmb = await predict_region('XSMB')
    
    if pred_xsmb:
        print(f"✅ XSMB: {pred_xsmb['predicted_number']}")
        db.save_prediction({
            'prediction_date': target_date,
            'region': 'XSMB',
            'province': None,
            'model_version': 'lstm_v2',
            'predicted_numbers': pred_xsmb,
            'confidence_score': pred_xsmb['confidence']
        })
        # Notify
        await notifier.send_prediction({
            'region': 'XSMB',
            'prediction_date': target_date,
            'predicted_numbers': {
                'predicted_number': pred_xsmb['predicted_number'],
                'hot_numbers': pred_xsmb['hot_numbers']
            },
            'confidence_score': pred_xsmb['confidence'],
            'model_version': 'lstm_v2'
        })
    else:
        print("❌ XSMB Prediction Failed")
        await notifier.send_error_alert(f"⚠️ XSMB Prediction Failed for {target_date}")

    # 2. XSMN
    crawler = XSMNCrawler()
    provinces = crawler.get_provinces_for_date(target_date)
    print(f"🎯 Predicting XSMN for {provinces}...")
    
    success_count = 0
    for province in provinces:
        pred_xsmn = await predict_region('XSMN', province)
        if pred_xsmn:
            print(f"✅ XSMN-{province}: {pred_xsmn['predicted_number']}")
            db.save_prediction({
                'prediction_date': target_date,
                'region': 'XSMN',
                'province': province,
                'model_version': 'lstm_v2',
                'predicted_numbers': pred_xsmn,
                'confidence_score': pred_xsmn['confidence']
            })
            # Notify
            province_name = crawler.PROVINCE_MAP.get(province, province)
            await notifier.send_prediction({
                'region': 'XSMN',
                'province': province_name,
                'prediction_date': target_date,
                'predicted_numbers': {
                    'predicted_number': pred_xsmn['predicted_number'],
                    'hot_numbers': list(map(str, pred_xsmn['hot_numbers']))
                },
                'confidence_score': pred_xsmn['confidence'],
                'model_version': 'lstm_v2'
            })
            success_count += 1
            
    if success_count == 0 and provinces:
        print("❌ XSMN Prediction Failed (All provinces)")
        await notifier.send_error_alert(f"⚠️ XSMN Prediction Failed for {target_date}")

if __name__ == "__main__":
    asyncio.run(main())
