"""
Frequency Analyzer - Phân tích tần suất xuất hiện số
Model đơn giản nhất cho lottery prediction
"""

import pandas as pd
from collections import Counter
from typing import Dict, List, Tuple
from datetime import date


class FrequencyAnalyzer:
    """
    Phân tích patterns dựa trên tần suất xuất hiện
    
    LƯU Ý: Lottery là random, model này chỉ mang tính giải trí!
    Không nên kỳ vọng độ chính xác cao.
    """
    
    def __init__(self, historical_data: List[Dict]):
        """
        Initialize analyzer với dữ liệu lịch sử
        
        Args:
            historical_data: List of dictionaries từ database
                [
                    {
                        'draw_date': '2024-01-01',
                        'special_prize': '12345',
                        'first_prize': '67890',
                        ...
                    },
                    ...
                ]
        """
        self.df = pd.DataFrame(historical_data)
        print(f"📊 Loaded {len(self.df)} historical records")
    
    def analyze_digit_frequency(self, prize_type: str = 'special_prize') -> Counter:
        """
        Phân tích tần suất từng chữ số (0-9)
        
        Args:
            prize_type: Loại giải cần phân tích
        
        Returns:
            Counter object với tần suất mỗi chữ số
        """
        all_digits = []
        
        for num in self.df[prize_type].dropna():
            # Tách số thành các chữ số riêng lẻ
            all_digits.extend(list(str(num)))
        
        freq = Counter(all_digits)
        
        print(f"\n📈 Digit Frequency for {prize_type}:")
        for digit, count in freq.most_common():
            print(f"  {digit}: {count} times ({count/len(all_digits)*100:.1f}%)")
        
        return freq
    
    def analyze_number_frequency(self, prize_type: str = 'special_prize') -> Counter:
        """
        Phân tích tần suất số nguyên (00-99)
        
        Lấy 2 chữ số cuối của mỗi giải
        """
        last_two_digits = []
        
        for num in self.df[prize_type].dropna():
            num_str = str(num)
            if len(num_str) >= 2:
                last_two_digits.append(num_str[-2:])
        
        freq = Counter(last_two_digits)
        
        print(f"\n📈 Last 2 Digits Frequency:")
        for num, count in freq.most_common(10):
            print(f"  {num}: {count} times")
        
        return freq
    
    def find_hot_numbers(self, top_n: int = 5) -> List[str]:
        """
        Tìm các số 'nóng' (xuất hiện nhiều nhất)
        
        Args:
            top_n: Số lượng số nóng cần lấy
        
        Returns:
            List of hot numbers
        """
        freq = self.analyze_number_frequency()
        hot_numbers = [num for num, _ in freq.most_common(top_n)]
        
        print(f"\n🔥 Top {top_n} Hot Numbers: {hot_numbers}")
        return hot_numbers
    
    def find_cold_numbers(self, top_n: int = 5) -> List[str]:
        """
        Tìm các số 'lạnh' (xuất hiện ít nhất)
        
        Args:
            top_n: Số lượng số lạnh cần lấy
        
        Returns:
            List of cold numbers
        """
        freq = self.analyze_number_frequency()
        
        # Lấy từ cuối lên (ít nhất)
        all_numbers = freq.most_common()
        cold_numbers = [num for num, _ in all_numbers[-top_n:]]
        
        print(f"\n❄️ Top {top_n} Cold Numbers: {cold_numbers}")
        return cold_numbers
    
    def predict_next(self, n_digits: int = 5) -> Dict:
        """
        Dự đoán số tiếp theo dựa trên tần suất
        
        Args:
            n_digits: Số chữ số cần dự đoán (mặc định 5)
        
        Returns:
            Dictionary chứa prediction
            {
                'predicted_number': '12345',
                'confidence': 0.25,
                'method': 'frequency_analysis',
                'hot_numbers': ['12', '34', ...],
                'reasoning': '...'
            }
        """
        # Phân tích tần suất chữ số
        digit_freq = self.analyze_digit_frequency()
        
        # Lấy n chữ số phổ biến nhất
        top_digits = [digit for digit, _ in digit_freq.most_common(n_digits)]
        
        # Ghép thành số dự đoán
        predicted_number = ''.join(top_digits)
        
        # Lấy hot numbers (2 chữ số cuối)
        hot_numbers = self.find_hot_numbers(top_n=3)
        
        # Confidence score (luôn thấp vì lottery là random)
        # Tính dựa trên độ phân tán của tần suất
        total_digits = sum(digit_freq.values())
        max_freq = digit_freq.most_common(1)[0][1]
        confidence = min(0.35, max_freq / total_digits)  # Cap ở 35%
        
        prediction = {
            'predicted_number': predicted_number,
            'confidence': round(confidence, 2),
            'method': 'frequency_analysis',
            'hot_numbers': hot_numbers,
            'reasoning': f'Based on {len(self.df)} historical draws. '
                        f'Top digits: {", ".join(top_digits)}. '
                        f'Hot 2-digit numbers: {", ".join(hot_numbers)}.'
        }
        
        print(f"\n🎯 Prediction:")
        print(f"  Number: {predicted_number}")
        print(f"  Confidence: {confidence*100:.0f}%")
        print(f"  Hot Numbers: {hot_numbers}")
        print(f"\n⚠️ DISCLAIMER: This is for entertainment only!")
        print(f"   Lottery is random and cannot be reliably predicted.")
        
        return prediction
    
    def get_statistics(self) -> Dict:
        """
        Lấy thống kê tổng quan
        
        Returns:
            Dictionary chứa stats
        """
        stats = {
            'total_records': len(self.df),
            'date_range': {
                'from': str(self.df['draw_date'].min()),
                'to': str(self.df['draw_date'].max())
            },
            'most_common_digit': self.analyze_digit_frequency().most_common(1)[0],
            'most_common_number': self.analyze_number_frequency().most_common(1)[0],
        }
        
        return stats


def test_analyzer():
    """Test analyzer với sample data"""
    # Sample data
    sample_data = [
        {'draw_date': '2024-01-01', 'special_prize': '12345', 'region': 'XSMB'},
        {'draw_date': '2024-01-02', 'special_prize': '67890', 'region': 'XSMB'},
        {'draw_date': '2024-01-03', 'special_prize': '11223', 'region': 'XSMB'},
        {'draw_date': '2024-01-04', 'special_prize': '44556', 'region': 'XSMB'},
        {'draw_date': '2024-01-05', 'special_prize': '77889', 'region': 'XSMB'},
    ]
    
    print(f"\n{'='*60}")
    print(f"Testing Frequency Analyzer")
    print(f"{'='*60}\n")
    
    analyzer = FrequencyAnalyzer(sample_data)
    
    # Test prediction
    prediction = analyzer.predict_next()
    
    # Test statistics
    stats = analyzer.get_statistics()
    print(f"\n📊 Statistics:")
    print(f"  Total records: {stats['total_records']}")
    print(f"  Date range: {stats['date_range']['from']} to {stats['date_range']['to']}")


if __name__ == "__main__":
    test_analyzer()
