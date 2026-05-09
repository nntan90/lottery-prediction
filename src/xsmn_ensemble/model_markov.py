"""
model_markov.py — Model B: Markov Chain Transition Probability
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Input:  tails_2d data (100 kỳ gần nhất per province)
Output: Top 5 cặp số + probability

Logic:
  1. Xây dựng ma trận chuyển 100×100:
     P(pair_j | pair_i xuất hiện ở kỳ trước)
  2. Lấy context = tail_set kỳ gần nhất
  3. Tính P(pair_j | context) = avg(P(pair_j | pair_i) for pair_i in context)
  4. Trả về Top 5 theo probability
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional


def _load_tails_sequential(
    db,
    region: str,
    province: Optional[str] = None,
    n_draws: int = 100,
    before_date: Optional[date] = None,
) -> list:
    """
    Lấy N kỳ quay gần nhất, trả về list of frozensets (mỗi set = tails 1 kỳ).
    Sorted by draw_date ASC (cũ → mới).
    """
    query = db.supabase.table("tails_2d") \
        .select("draw_date,tail_2d") \
        .eq("region", region) \
        .order("draw_date", desc=True)

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    if before_date:
        query = query.lt("draw_date", before_date.isoformat())

    query = query.limit(n_draws * 30)
    rows = query.execute().data

    if not rows:
        return []

    # Group by draw_date
    from collections import defaultdict
    date_groups = defaultdict(set)
    for r in rows:
        date_groups[r["draw_date"]].add(r["tail_2d"])

    # Sort by date ASC, take last n_draws
    sorted_dates = sorted(date_groups.keys())[-n_draws:]
    return [frozenset(date_groups[d]) for d in sorted_dates]


def predict_markov(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 100,
    top_n: int = 5,
    region: str = "XSMN",
) -> Dict:
    """
    Model B: Markov Chain transition probability cho XSMN/XSMB.

    Args:
        db: LotteryDB instance
        province: slug tỉnh, None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ lookback
        top_n: số cặp top-N output
        region: 'XSMN' hoặc 'XSMB'

    Returns:
        {
            'model_name': 'markov',
            'province': province,
            'top_pairs': [(pair, probability), ...],
            'n_draws_used': int,
            'status': 'success' | 'error',
            'error_message': str | None,
            'execution_time_ms': int,
        }
    """
    start_ms = time.time()

    try:
        draws = _load_tails_sequential(db, region, province, n_draws, before_date=target_date)
        n = len(draws)

        if n < 10:
            return {
                "model_name": "markov",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "status": "error",
                "error_message": f"Không đủ lịch sử: {n} kỳ (cần ≥ 10)",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        # Build transition matrix 100×100
        # transition[i][j] = count(kỳ t có pair_i AND kỳ t+1 có pair_j)
        transition = np.zeros((100, 100), dtype=float)

        for t in range(n - 1):
            current_set = draws[t]
            next_set = draws[t + 1]
            for i in current_set:
                if 0 <= i <= 99:
                    for j in next_set:
                        if 0 <= j <= 99:
                            transition[i][j] += 1

        # Normalize rows → probability
        row_sums = transition.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1  # avoid div by 0
        prob_matrix = transition / row_sums

        # Context = tail_set kỳ gần nhất (kỳ cuối)
        context = draws[-1] if draws else set()

        if not context:
            return {
                "model_name": "markov",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "status": "error",
                "error_message": "Context rỗng (kỳ cuối không có data)",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        # P(pair_j | context) = mean(P(pair_j | pair_i) for pair_i in context)
        predictions = np.zeros(100, dtype=float)
        valid_context = [i for i in context if 0 <= i <= 99]

        if valid_context:
            for i in valid_context:
                predictions += prob_matrix[i]
            predictions /= len(valid_context)

        # Top N
        top_indices = np.argsort(predictions)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(predictions[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "markov",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return {
            "model_name": "markov",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
