
from datetime import datetime, timedelta

def test_utc7_logic():
    print("🌍 Testing UTC+7 Logic...")
    
    # Simulate current UTC time
    utc_now = datetime.utcnow()
    print(f"   UTC Now:        {utc_now}")
    
    # Calculate VN Time
    vn_now = utc_now + timedelta(hours=7)
    print(f"   VN Now (UTC+7): {vn_now}")
    
    # Calculate Prediction Date (Tomorrow relative to VN)
    prediction_date = (vn_now + timedelta(days=1)).date()
    print(f"   Prediction For: {prediction_date} (Tomorrow in VN)")
    
    # Calculate Evaluation Date (Yesterday relative to VN)
    evaluation_date = (vn_now - timedelta(days=1)).date()
    print(f"   Evaluation For: {evaluation_date} (Yesterday in VN)")

    # Verification:
    # If it is 2026-02-15 01:00 UTC -> 2026-02-15 08:00 VN
    # Prediction should be for 2026-02-16
    # Evaluation should be for 2026-02-14
    
    if prediction_date == vn_now.date() + timedelta(days=1):
        print("✅ Prediction logic correct")
    else:
        print("❌ Prediction logic FAILED")
        
    if evaluation_date == vn_now.date() - timedelta(days=1):
        print("✅ Evaluation logic correct")
    else:
        print("❌ Evaluation logic FAILED")

if __name__ == '__main__':
    test_utc7_logic()
