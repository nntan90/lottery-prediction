"""
Verification Utils
Logic to verify predictions against actual lottery results.
"""

from typing import Dict, List, Optional, Tuple

def get_last_n_digits(number_str: str, n: int = 2) -> str:
    """Get last n digits of a number string"""
    if not number_str:
        return ""
    return str(number_str)[-n:]

def verify_prediction(prediction: Dict, result: Dict) -> Dict:
    """
    Verify a single prediction against the actual result.
    
    Args:
        prediction: Prediction record from DB
            {
                'predicted_numbers': {'predicted_number': '12345'},
                ...
            }
        result: Draw result record from DB
            {
                'special_prize': '12345',
                'first_prize': '67890',
                'second_prize': ['123', '456'],
                ...
            }
            
    Returns:
        Dictionary with verification results:
        {
            'is_correct': True/False,
            'win_prize': {
                'count': 2,
                'matches': ['Giai 8', 'Giai Dac Biet'],
                'details': ['Matched 45 in Giai 8', ...]
            }
        }
    """
    if not prediction or not result:
        return {'is_correct': False, 'win_prize': None}
    
    # Get predicted number
    pred_data = prediction.get('predicted_numbers', {})
    if isinstance(pred_data, str):
        # Handle case where it might be stored as string (legacy)
        import json
        try:
            pred_data = json.loads(pred_data)
        except:
            pred_data = {'predicted_number': pred_data}
            
    predicted_full = str(pred_data.get('predicted_number', ''))
    
    # User Requirement: "Win Check" logic: trùng 2 số cuối
    predicted_last_2 = get_last_n_digits(predicted_full, 2)
    
    if not predicted_last_2 or len(predicted_last_2) < 2:
        return {'is_correct': False, 'win_prize': None}

    # --- Validate Metadata (Region, Province, Date) ---
    pred_region = prediction.get('region')
    res_region = result.get('region')
    if pred_region != res_region:
        print(f"⚠️ Mismatch Region: Prediction({pred_region}) vs Result({res_region})")
        return {'is_correct': False, 'win_prize': None, 'error': 'Region Mismatch'}

    pred_prov = prediction.get('province') or '' # Handle None as ''
    res_prov = result.get('province') or ''
    # Normalize
    pred_prov = pred_prov.lower().strip()
    res_prov = res_prov.lower().strip()
    
    if pred_prov != res_prov:
        print(f"⚠️ Mismatch Province: Prediction({pred_prov}) vs Result({res_prov})")
        return {'is_correct': False, 'win_prize': None, 'error': 'Province Mismatch'}

    # Date check (optional but good)
    pred_date = str(prediction.get('prediction_date'))
    res_date = str(result.get('draw_date'))
    if pred_date != res_date:
        print(f"⚠️ Mismatch Date: Prediction({pred_date}) vs Result({res_date})")
        return {'is_correct': False, 'win_prize': None, 'error': 'Date Mismatch'}
    # --------------------------------------------------

    matches = []
    win_count = 0
    
    # Define fields to check
    # Single value fields
    single_fields = {
        'special_prize': 'Giải Đặc Biệt',
        'first_prize': 'Giải Nhất',
        'eighth_prize': 'Giải Tám'
    }
    
    # List value fields
    list_fields = {
        'second_prize': 'Giải Nhì',
        'third_prize': 'Giải Ba',
        'fourth_prize': 'Giải Tư',
        'fifth_prize': 'Giải Năm',
        'sixth_prize': 'Giải Sáu',
        'seventh_prize': 'Giải Bảy'
    }
    
    # Check single fields
    for field, name in single_fields.items():
        val = result.get(field)
        if val:
            val_str = str(val)
            if val_str.endswith(predicted_last_2):
                matches.append(f"{name} ({val_str})")
                win_count += 1
                
    # Check list fields
    for field, name in list_fields.items():
        vals = result.get(field) # Should be a list
        if vals and isinstance(vals, list):
            for val in vals:
                val_str = str(val)
                if val_str.endswith(predicted_last_2):
                    matches.append(f"{name} ({val_str})")
                    win_count += 1
    
    is_correct = win_count > 0
    
    win_info = None
    if is_correct:
        win_info = {
            'count': win_count,
            'matches': matches,
            'match_type': 'last_2',
            'predicted_last_2': predicted_last_2
        }
        
    return {
        'is_correct': is_correct,
        'win_prize': win_info
    }
