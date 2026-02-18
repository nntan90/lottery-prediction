from collections import Counter
from typing import List, Dict, Any

class FrequencyAnalyzer:
    """
    Classic Frequency Analysis for Lottery.
    Counts occurrences of numbers in the provided historical data.
    """
    
    def __init__(self, data: List[Dict[str, Any]]):
        """
        Initialize with historical data.
        
        Args:
            data: List of draw dictionaries from database (Supabase).
                  Each dict should have 'special_prize' or other prize fields.
        """
        self.data = data
        
    def find_hot_numbers(self, top_n: int = 5) -> List[str]:
        """
        Find the most frequent numbers in the special prize.
        
        Args:
            top_n: Number of hot numbers to return.
            
        Returns:
            List of hot numbers (strings).
        """
        if not self.data:
            return []
            
        # Collect all special prize numbers
        numbers = []
        for draw in self.data:
            sp = draw.get('special_prize')
            if sp:
                # We typically analyze the last 2 digits for "lo de" style, 
                # or the full number. 
                # Based on `predict_v2.py`, it seems we might be just returning the full number string 
                # or maybe the script expects something specific?
                # Let's assume we count the FULL winning number for now, 
                # OR meaningful endings. 
                # Standard "hot numbers" usually refer to 2-digit pairs in Vietnam.
                # However, `predict_v2` logic: 
                #    hot_numbers = freq_analyzer.find_hot_numbers(top_n=5)
                #    ... 
                #    'hot_numbers': hot_numbers
                # If we return full 5-6 digit strings, they rarely repeat. 
                # So it is HIGHLY LIKELY this analyzer was counting the last 2 digits (tail).
                
                # Extract last 2 digits
                if len(str(sp)) >= 2:
                    tail = str(sp)[-2:]
                    numbers.append(tail)
                else:
                    numbers.append(str(sp))
                    
        # Count frequency
        counter = Counter(numbers)
        
        # Get top N
        most_common = counter.most_common(top_n)
        
        # Return just the numbers
        return [num for num, count in most_common]

    def analyze_positional_frequency(self):
        """
        Analyze frequency of digits at each position (0-9).
        (Placeholder for future use if needed)
        """
        pass
