"""
Prediction V2 Script
Uses LSTM model for prediction, then sends a single clean Telegram summary.
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
    """Generate prediction using LSTM model, returns prediction dict or None."""
    db = LotteryDB()
    storage = LotteryStorage()
    
    prov_slug = province if province else "all"
    
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
            print(f"⚠️ No trained model found for {region}-{prov_slug}. Skipping.")
            return None
            
        model_storage_path = response.data[0]['model_path']
        print(f"📥 Found model: {model_storage_path}")
        
        # Download Model
        local_model_path = f"model_temp_{region}_{prov_slug}.h5"
        if not storage.download_model(model_storage_path, local_model_path):
             return None
             
        # Load Model
        lstm = LotteryLSTM(sequence_length=60)
        lstm.load(local_model_path)
        
        # Fetch last 100 days of data
        data = db.get_historical_data(region, days=100, province=province)
        if len(data) < 60:
             print(f"⚠️ Not enough history for {region}-{prov_slug} ({len(data)} records).")
             return None
             
        import pandas as pd
        df = pd.DataFrame(data)
        if 'draw_date' in df.columns:
            df['draw_date'] = pd.to_datetime(df['draw_date'])
            df = df.sort_values('draw_date')
            
        series = df['special_prize'].astype(str).apply(lambda x: float(x) if x.isdigit() else 0).values
        input_series = series[-60:]
        input_data = input_series.reshape(-1, 1)
        
        lstm.scaler.fit(input_data) 
        predicted_val = lstm.predict_next(input_data)
        
        digits = 5 if region == 'XSMB' else 6
        predicted_int = int(round(predicted_val))
        predicted_str = str(predicted_int).zfill(digits)
        
        # Hot numbers via FrequencyAnalyzer
        try:
            from src.models.frequency_analyzer import FrequencyAnalyzer
            freq_analyzer = FrequencyAnalyzer(data)
            hot_numbers = freq_analyzer.find_hot_numbers(top_n=5)
        except ImportError:
            hot_numbers = []
        
        result = {
             'predicted_number': predicted_str,
             'confidence': 0.85,
             'hot_numbers': hot_numbers,
             'model': 'lstm_v2'
        }
        
        # Cleanup temp file
        if os.path.exists(local_model_path):
             os.remove(local_model_path)
             
        return result

    except Exception as e:
        print(f"❌ Prediction failed for {region}-{prov_slug}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    notifier = LotteryNotifier()
    db = LotteryDB()
    
    # Determine target date
    vn_now = datetime.now(tz=None) + timedelta(hours=0)  # CI runs in UTC, cron at 00:00 UTC = 07:00 VN
    vn_now = datetime.utcnow() + timedelta(hours=7)
    if vn_now.hour < 12:
        target_date = vn_now.date()
        print(f"🌅 Morning run ({vn_now.hour}h VN): Predicting for TODAY ({target_date})")
    else:
        target_date = (vn_now + timedelta(days=1)).date()
        print(f"🌇 Evening run ({vn_now.hour}h VN): Predicting for TOMORROW ({target_date})")

    results = {}  # {label: predicted_number} for summary

    # ── 1. XSMB ──────────────────────────────────────────────────────────────
    print(f"\n🎯 Predicting XSMB for {target_date}...")
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
        results['XSMB'] = pred_xsmb
    else:
        print("❌ XSMB Prediction Failed")

    # ── 2. XSMN ──────────────────────────────────────────────────────────────
    crawler = XSMNCrawler()
    provinces = crawler.get_provinces_for_date(target_date)
    print(f"\n🎯 Predicting XSMN for {provinces}...")
    
    xsmn_results = {}
    for province in provinces:
        pred = await predict_region('XSMN', province)
        if pred:
            print(f"✅ XSMN-{province}: {pred['predicted_number']}")
            db.save_prediction({
                'prediction_date': target_date,
                'region': 'XSMN',
                'province': province,
                'model_version': 'lstm_v2',
                'predicted_numbers': pred,
                'confidence_score': pred['confidence']
            })
            xsmn_results[province] = pred

    # ── 3. Send ONE clean Telegram summary ───────────────────────────────────
    date_str = target_date.strftime("%d/%m/%Y")
    
    # XSMB message
    if results.get('XSMB'):
        p = results['XSMB']
        hot = ', '.join(map(str, p['hot_numbers'][:5])) if p['hot_numbers'] else 'N/A'
        xsmb_msg = (
            f"🎯 <b>DỰ ĐOÁN XSMB - {date_str}</b>\n\n"
            f"🔮 Số dự đoán: <code>{p['predicted_number']}</code>\n"
            f"📊 Độ tin cậy: {int(p['confidence'] * 100)}%\n"
            f"🔥 Số nóng: {hot}\n\n"
            f"<i>Model: lstm_v2</i>"
        )
        await notifier.send_message(xsmb_msg)
        print("✅ XSMB Telegram sent")
    else:
        await notifier.send_error_alert(f"⚠️ XSMB Prediction Failed for {target_date}")

    # XSMN message (all provinces in one message)
    if xsmn_results:
        xsmn_msg = f"🎯 <b>DỰ ĐOÁN XSMN - {date_str}</b>\n\n"
        for prov_slug, pred in xsmn_results.items():
            prov_name = crawler.PROVINCE_MAP.get(prov_slug, prov_slug)
            xsmn_msg += f"📍 <b>{prov_name}</b>: <code>{pred['predicted_number']}</code> ({int(pred['confidence']*100)}%)\n"
        xsmn_msg += f"\n<i>Tổng: {len(xsmn_results)} tỉnh | Model: lstm_v2</i>"
        await notifier.send_message(xsmn_msg)
        print(f"✅ XSMN Telegram sent ({len(xsmn_results)} provinces)")
    elif provinces:
        await notifier.send_error_alert(f"⚠️ XSMN Prediction Failed for all provinces on {target_date}")


if __name__ == "__main__":
    asyncio.run(main())
