import numpy as np
import pandas as pd
from datetime import date
from typing import Dict, List, Optional
from src.xsmb_ensemble.data_utils import _load_tails_by_draws, compute_pair_appeared_matrix

class XSMBLotoAnalyzer:
    """
    Phân tích thống kê lô tô XSMB hằng ngày (tiếp cận thuần xác suất).
    Phục vụ cho việc tạo báo cáo Telegram chi tiết.
    """

    def __init__(self, db, target_date: date, lookback: int = 100):
        self.db = db
        self.target_date = target_date
        self.lookback = lookback
        
        # Load history data lazily
        self._history = None
        self._appeared_matrix = None
        self._n_draws = 0

    def _ensure_data_loaded(self):
        """Lazy load data from database."""
        if self._history is None:
            # Load 100 draws by default to compute most stats
            self._history = _load_tails_by_draws(
                self.db, region="XSMB", province=None, 
                n_draws=self.lookback, before_date=self.target_date
            )
            self._n_draws = len(self._history)
            if self._n_draws > 0:
                self._appeared_matrix = compute_pair_appeared_matrix(self._history)
            else:
                self._appeared_matrix = np.zeros((0, 100))

    def analyze_hot_numbers(self, window: int = 30, top_n: int = 10) -> Dict:
        """Top N số xuất hiện nhiều nhất trong `window` kỳ."""
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {"pairs": [], "window_size": window}

        window = min(window, self._n_draws)
        # appeared_matrix shape: (n_draws, 100). Take the last `window` rows
        recent_data = self._appeared_matrix[-window:]
        counts = recent_data.sum(axis=0)  # (100,)
        
        # Sort indices by count descending
        top_indices = np.argsort(counts)[-top_n:][::-1]
        
        results = []
        for idx in top_indices:
            count = int(counts[idx])
            freq_pct = round(count / window * 100, 1)
            results.append((int(idx), count, freq_pct))
            
        return {"pairs": results, "window_size": window}

    def analyze_overdue_numbers(self, top_n: int = 10) -> Dict:
        """Top N số chưa về lâu nhất (Lô Gan)."""
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {"pairs": []}

        gaps = np.zeros(100, dtype=int)
        for pair in range(100):
            col = self._appeared_matrix[:, pair]
            positions = np.where(col > 0)[0]
            if len(positions) > 0:
                gaps[pair] = self._n_draws - 1 - positions[-1]
            else:
                gaps[pair] = self._n_draws

        top_indices = np.argsort(gaps)[-top_n:][::-1]
        results = []
        for idx in top_indices:
            # We could calculate avg gap and z-score here if needed, but for the basic report, 
            # current gap is sufficient. For full implementation, let's add avg gap.
            col = self._appeared_matrix[:, idx]
            positions = np.where(col > 0)[0]
            if len(positions) >= 2:
                hist_gaps = np.diff(positions)
                avg_gap = round(float(np.mean(hist_gaps)), 1)
            elif len(positions) == 1:
                avg_gap = round(float(self._n_draws - 1 - positions[0]), 1)
            else:
                avg_gap = float(self._n_draws)
                
            results.append((int(idx), int(gaps[idx]), avg_gap))
            
        return {"pairs": results}

    def analyze_falling_numbers(self) -> Dict:
        """
        Các số về hôm qua (Lô rơi). 
        Tính xác suất rơi tiếp 1 ngày, 2 ngày.
        """
        self._ensure_data_loaded()
        if self._n_draws < 2:
            return {"yesterday_pairs": [], "fall_1day_probs": [], "fall_2day_probs": []}

        # Pairs from yesterday (the very last draw in history before target_date)
        yesterday_row = self._appeared_matrix[-1]
        yesterday_pairs = [int(i) for i in np.where(yesterday_row > 0)[0]]

        # Calculate historical falling probability
        fall_1_probs = []
        fall_2_probs = []
        
        for pair in yesterday_pairs:
            col = self._appeared_matrix[:, pair]
            # Count appearances
            appearances = np.where(col > 0)[0]
            if len(appearances) == 0:
                continue
                
            # Fall 1 day (next day hit)
            fall_1_count = 0
            # Fall 2 days (next 2 days hit)
            fall_2_count = 0
            
            valid_trials_1 = 0
            valid_trials_2 = 0
            
            for pos in appearances:
                # Exclude the very last row because we don't know the future yet
                if pos < self._n_draws - 1:
                    valid_trials_1 += 1
                    if col[pos + 1] > 0:
                        fall_1_count += 1
                        
                if pos < self._n_draws - 2:
                    valid_trials_2 += 1
                    if col[pos + 1] > 0 and col[pos + 2] > 0:
                        fall_2_count += 1

            prob_1 = round(fall_1_count / valid_trials_1 * 100, 1) if valid_trials_1 > 0 else 0.0
            prob_2 = round(fall_2_count / valid_trials_2 * 100, 1) if valid_trials_2 > 0 else 0.0
            
            fall_1_probs.append((pair, prob_1))
            fall_2_probs.append((pair, prob_2))

        # Sort by prob descending
        fall_1_probs.sort(key=lambda x: x[1], reverse=True)
        fall_2_probs.sort(key=lambda x: x[1], reverse=True)

        return {
            "yesterday_pairs": yesterday_pairs,
            "fall_1day_probs": fall_1_probs[:5], # Top 5 highest falling prob
            "fall_2day_probs": fall_2_probs[:5],
        }

    def analyze_doubles(self) -> Dict:
        """Phân tích 10 số kép (00,11,22...99)."""
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {"doubles_status": []}

        doubles = [0, 11, 22, 33, 44, 55, 66, 77, 88, 99]
        status = []
        
        for pair in doubles:
            col = self._appeared_matrix[:, pair]
            positions = np.where(col > 0)[0]
            if len(positions) > 0:
                gap = self._n_draws - 1 - positions[-1]
            else:
                gap = self._n_draws
                
            if len(positions) >= 2:
                avg_gap = round(float(np.mean(np.diff(positions))), 1)
            else:
                avg_gap = 10.0 # roughly 10 for doubles since there are 10 of them
                
            # mark as overdue if current gap >= 1.5 * avg_gap
            is_overdue = gap >= (1.5 * avg_gap) and gap > 5
            
            status.append((pair, gap, avg_gap, is_overdue))
            
        # Sort by gap descending
        status.sort(key=lambda x: x[1], reverse=True)
        
        return {"doubles_status": status}

    def analyze_head_tail(self, window: int = 30) -> Dict:
        """Phân bổ đầu 0-9 và đuôi 0-9 trong `window` kỳ."""
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {}

        window = min(window, self._n_draws)
        recent_data = self._appeared_matrix[-window:]
        pair_counts = recent_data.sum(axis=0)  # (100,)
        
        head_dist = {i: 0 for i in range(10)}
        tail_dist = {i: 0 for i in range(10)}
        
        for pair in range(100):
            count = pair_counts[pair]
            head = pair // 10
            tail = pair % 10
            head_dist[head] += int(count)
            tail_dist[tail] += int(count)
            
        # Sort heads/tails
        sorted_heads = sorted(head_dist.items(), key=lambda x: x[1], reverse=True)
        sorted_tails = sorted(tail_dist.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "head_distribution": head_dist,
            "tail_distribution": tail_dist,
            "strong_heads": [h for h, c in sorted_heads[:3]],
            "strong_tails": [t for t, c in sorted_tails[:3]],
            "weak_heads": [h for h, c in sorted_heads[-3:]],
            "weak_tails": [t for t, c in sorted_tails[-3:]],
            "window_size": window
        }

    def analyze_reverse_pairs(self, window: int = 30) -> Dict:
        """
        Phân tích các cặp đảo (27↔72). 
        Nếu 1 trong 2 vừa về hôm qua hoặc đang rất nóng → có tín hiệu cho cặp kia.
        """
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {"active_reverse_pairs": []}
            
        window = min(window, self._n_draws)
        recent_data = self._appeared_matrix[-window:]
        pair_counts = recent_data.sum(axis=0)
        
        # Calculate gaps
        gaps = np.zeros(100, dtype=int)
        for pair in range(100):
            col = self._appeared_matrix[:, pair]
            positions = np.where(col > 0)[0]
            if len(positions) > 0:
                gaps[pair] = self._n_draws - 1 - positions[-1]
            else:
                gaps[pair] = self._n_draws

        # Identify signals
        signals = []
        processed = set()
        for pair_a in range(100):
            pair_b = (pair_a % 10) * 10 + (pair_a // 10)
            if pair_a == pair_b or pair_a in processed:
                continue
            
            processed.add(pair_a)
            processed.add(pair_b)
            
            gap_a = gaps[pair_a]
            gap_b = gaps[pair_b]
            
            # Signal: One dropped yesterday (gap=0), the other has a moderate gap
            if gap_a == 0 and gap_b > 0 and gap_b <= 15:
                signals.append((pair_a, pair_b, gap_a, gap_b, f"Rơi {pair_a:02d} chờ {pair_b:02d}"))
            elif gap_b == 0 and gap_a > 0 and gap_a <= 15:
                signals.append((pair_a, pair_b, gap_a, gap_b, f"Rơi {pair_b:02d} chờ {pair_a:02d}"))
                
        return {"active_reverse_pairs": signals[:5]} # Limit to top 5 signals

    def analyze_sum_touch(self, window: int = 30) -> Dict:
        """Tổng 0-18 và Chạm 0-9."""
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {}

        window = min(window, self._n_draws)
        recent_data = self._appeared_matrix[-window:]
        pair_counts = recent_data.sum(axis=0)
        
        sum_dist = {i: 0 for i in range(19)}
        touch_dist = {i: 0 for i in range(10)}
        
        for pair in range(100):
            count = int(pair_counts[pair])
            # Sum
            s = (pair // 10) + (pair % 10)
            sum_dist[s] += count
            # Touch
            touch_dist[pair // 10] += count
            if (pair // 10) != (pair % 10):
                touch_dist[pair % 10] += count
                
        sorted_sums = sorted(sum_dist.items(), key=lambda x: x[1], reverse=True)
        sorted_touches = sorted(touch_dist.items(), key=lambda x: x[1], reverse=True)
        
        return {
            "sum_distribution": sum_dist,
            "strong_sums": [s for s, c in sorted_sums[:3]],
            "touch_distribution": touch_dist,
            "strong_touches": [t for t, c in sorted_touches[:3]],
            "window_size": window
        }

    def suggest_xien(self) -> Dict:
        """Ghép cặp xiên 2 từ đầu mạnh, đuôi mạnh, hoặc cặp lộn."""
        head_tail = self.analyze_head_tail(window=14) # Shorter window for momentum
        strong_heads = head_tail.get("strong_heads", [])
        strong_tails = head_tail.get("strong_tails", [])
        
        xien_head = []
        if len(strong_heads) >= 1:
            h = strong_heads[0]
            # Simple pairing of some strong candidates within the strong head
            xien_head.append((h*10 + 2, h*10 + 7, f"Cùng đầu mạnh {h}"))
            xien_head.append((h*10 + 3, h*10 + 8, f"Cùng đầu mạnh {h}"))
            
        xien_tail = []
        if len(strong_tails) >= 1:
            t = strong_tails[0]
            xien_tail.append((20 + t, 70 + t, f"Cùng đuôi mạnh {t}"))
            
        return {
            "xien_same_head": xien_head[:2],
            "xien_same_tail": xien_tail[:2],
            "xien_correlated": [], # Placeholder for correlation analysis
        }

    def suggest_top_3_dan_so(self, top_n: int = 3) -> Dict:
        """
        Dàn số gợi ý: Pick top-N số có tỉ lệ ra cao nhất dựa trên chấm điểm đa tiêu chí:
        1. Nằm trong Chạm mạnh, Đầu/Đuôi mạnh
        2. Tần suất 30 ngày cao (Lô Nóng)
        3. Khoảng cách (Gap) rơi vào điểm rơi lý tưởng (1-7 ngày)
        4. Xác suất rơi lại (nếu vừa ra hôm qua)
        """
        self._ensure_data_loaded()
        if self._n_draws == 0:
            return {"top_3": [], "top_scored": [], "criteria": []}

        # Lấy các chỉ số thống kê
        st_data = self.analyze_sum_touch(window=14)
        ht_data = self.analyze_head_tail(window=14)
        hot_data = self.analyze_hot_numbers(window=30, top_n=30)
        fall_data = self.analyze_falling_numbers()

        strong_touches = set(st_data.get("strong_touches", []))
        strong_heads = set(ht_data.get("strong_heads", []))
        strong_tails = set(ht_data.get("strong_tails", []))
        
        # Tạo map Lô Nóng
        hot_freq = {p: f for p, c, f in hot_data.get("pairs", [])}
        
        # Tạo map xác suất rơi 1 ngày
        fall_probs = {p: prob for p, prob in fall_data.get("fall_1day_probs", [])}

        scores = {}
        for pair in range(100):
            score = 0.0
            
            # Tính Gap
            col = self._appeared_matrix[:, pair]
            positions = np.where(col > 0)[0]
            if len(positions) > 0:
                gap = self._n_draws - 1 - positions[-1]
            else:
                gap = self._n_draws

            # Tiêu chí 1: Gap lý tưởng (1 đến 7 ngày) -> +3 điểm
            if 1 <= gap <= 7:
                score += 3.0
            # Nếu gan > 15 ngày -> Phạt nặng
            elif gap > 15:
                score -= 5.0

            # Tiêu chí 2: Đầu/Đuôi/Chạm mạnh
            head = pair // 10
            tail = pair % 10
            if head in strong_heads:
                score += 2.0
            if tail in strong_tails:
                score += 2.0
            if head in strong_touches or tail in strong_touches:
                score += 1.5

            # Tiêu chí 3: Lô Nóng (cộng điểm theo tần suất)
            if pair in hot_freq:
                # freq từ 0-100%, chia 10 để ra điểm cộng
                score += hot_freq[pair] / 10.0

            # Tiêu chí 4: Lô Rơi (nếu vừa ra hôm qua, cộng điểm theo xác suất rơi lại)
            if gap == 0 and pair in fall_probs:
                score += fall_probs[pair] / 10.0

            scores[pair] = score

        # Lấy top-N điểm cao nhất. top_3 giữ nguyên để report cũ không đổi,
        # top_scored phục vụ wrapper model đưa loto vào ensemble.
        sorted_pairs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_3 = [p for p, s in sorted_pairs[:3]]
        top_scored = [(int(p), round(float(s), 4)) for p, s in sorted_pairs[:top_n]]
        
        return {
            "top_3": top_3,
            "top_scored": top_scored,
            "criteria": [
                "Chạm/Đầu/Đuôi mạnh (14 ngày)",
                "Chu kỳ nổ lý tưởng (1-7 ngày)",
                "Tần suất nổ cao (30 ngày)",
                "Xác suất lô rơi (nếu ra hôm qua)"
            ]
        }

    def generate_full_report(self) -> Dict:
        """Gom tất cả phân tích thành 1 báo cáo tổng hợp."""
        return {
            "hot_numbers": self.analyze_hot_numbers(window=30, top_n=10),
            "overdue_numbers": self.analyze_overdue_numbers(top_n=10),
            "falling_numbers": self.analyze_falling_numbers(),
            "doubles": self.analyze_doubles(),
            "head_tail": self.analyze_head_tail(window=30),
            "reverse_pairs": self.analyze_reverse_pairs(window=30),
            "sum_touch": self.analyze_sum_touch(window=30),
            "xien": self.suggest_xien(),
            "dan_de": self.suggest_top_3_dan_so(),
        }
