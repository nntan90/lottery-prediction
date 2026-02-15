
import asyncio
from src.models.frequency_analyzer import FrequencyAnalyzer
from src.database.supabase_client import LotteryDB
from datetime import datetime, timedelta

async def main():
    print('🎯 Testing XSMB prediction save...')
    
    db = LotteryDB()
    
    try:
        # Lấy dữ liệu lịch sử (dummy request effectively, just need DB connection)
        historical = db.get_historical_data('XSMB', days=90)
        
        if not historical or len(historical) < 10:
            print(f'⚠️ Warning: Low historical data ({len(historical) if historical else 0} records), but proceeding to test save logic.')
        
        print(f'📊 Loaded {len(historical) if historical else 0} historical records')
        
        # MOCK prediction to avoid needing enough data for the analyzer if strictly enforced
        # But let's try to use the analyzer if possible, or fallback to mock
        try:
            if historical and len(historical) >= 30:
                analyzer = FrequencyAnalyzer(historical)
                prediction = analyzer.predict_next(n_digits=5)
            else:
                print("⚠️ Not enough data for real analysis, using MOCK prediction data.")
                prediction = {
                    'predicted_number': '12345',
                    'confidence': 0.5,
                    'hot_numbers': ['12', '34', '56']
                }
        except Exception as e:
            print(f"⚠️ Model error: {e}, using MOCK data.")
            prediction = {
                    'predicted_number': '12345',
                    'confidence': 0.5,
                    'hot_numbers': ['12', '34', '56']
                }

        # Lưu dự đoán cho ngày mai
        tomorrow = datetime.now() + timedelta(days=1)
        
        print(f"Attempting to save prediction for {tomorrow.date()}...")
        
        # NOTE: This uses the logic we set in supabase_client.py 
        # where province=None should be converted to ''
        db.save_prediction({
            'prediction_date': tomorrow.date(),
            'region': 'XSMB',
            'province': None,  # XSMB không có province
            'model_version': 'frequency_v1',
            'predicted_numbers': prediction,
            'confidence_score': prediction['confidence']
        })
        
        print(f'✅ Prediction saved successfully for {tomorrow.date()}!')
        print(f'   Predicted number: {prediction["predicted_number"]}')
        
        # Verify by fetching it back
        saved = db.get_prediction_by_date(tomorrow.date(), 'XSMB')
        if saved:
             print(f"✅ Verified: Retrieved saved record from DB. ID: {saved.get('id')}")
             if saved.get('province') == '':
                 print(f"✅ Verified: Province was stored as empty string ''")
             else:
                 print(f"⚠️ Warning: Province is '{saved.get('province')}' (expected '')")
        else:
             print("❌ Error: Could not retrieve saved record.")

    except Exception as e:
        print(f'❌ Error: {e}')
        exit(1)

if __name__ == '__main__':
    asyncio.run(main())
