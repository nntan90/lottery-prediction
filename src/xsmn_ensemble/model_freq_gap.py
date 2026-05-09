"""
model_freq_gap.py — Model A: Frequency/Gap Scoring (Rule-based)
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Input:  tails_2d data (100 kỳ gần nhất per province)
Output: Top 5 cặp số + score (chuẩn hóa min-max)

Score = weighted sum of:
  - freq_short  (30 kỳ)  × 0.25
  - freq_mid    (60 kỳ)  × 0.20
  - freq_long   (100 kỳ) × 0.15
  - gap_zscore             × 0.25  (ưu tiên cặp đang "overdue")
  - is_hot_short           × 0.15  (xuất hiện trong 3 kỳ gần nhất)
"""

import numpy as np
import pandas as pd
import time
from datetime import date
from typing import List, Dict, Tuple, Optional


def _load_tails_by_draws(
    db,
    region: str,
    province: Optional[str] = None,
    n_draws: int = 100,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Lấy N kỳ quay gần nhất của 1 province từ tails_2d.
    Lookback theo kỳ quay (LIMIT), KHÔNG theo ngày.

    Returns:
        DataFrame: columns ['draw_date', 'tail_set']
        Mỗi row = 1 kỳ quay, tail_set = frozenset of ints
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

    # Lấy gấp 30 lần vì mỗi kỳ có ~18 tails → 100 kỳ cần ~1800 rows max
    query = query.limit(n_draws * 30)

    rows = query.execute().data
    if not rows:
        return pd.DataFrame(columns=["draw_date", "tail_set"])

    df = pd.DataFrame(rows)
    grouped = df.groupby("draw_date")["tail_2d"].apply(frozenset).reset_index()
    grouped.columns = ["draw_date", "tail_set"]
    grouped["draw_date"] = pd.to_datetime(grouped["draw_date"])
    grouped = grouped.sort_values("draw_date").tail(n_draws)

    return grouped.reset_index(drop=True)


def predict_freq_gap(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 100,
    top_n: int = 5,
    region: str = "XSMN",
) -> Dict:
    """
    Model A: Frequency/Gap scoring cho XSMN/XSMB.

    Args:
        db: LotteryDB instance
        province: slug tỉnh (e.g. 'tp-hcm'), None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ quay lookback
        top_n: số cặp top-N output
        region: 'XSMN' hoặc 'XSMB'

    Returns:
        {
            'model_name': 'freq_gap',
            'province': province,
            'top_pairs': [(pair, score), ...],  # sorted desc
            'n_draws_used': int,
            'status': 'success' | 'error',
            'error_message': str | None,
            'execution_time_ms': int,
        }
    """
    start_ms = time.time()

    try:
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < 10:
            return {
                "model_name": "freq_gap",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "status": "error",
                "error_message": f"Không đủ lịch sử: {n} kỳ (cần ≥ 10)",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        scores = np.zeros(100, dtype=float)

        for pair in range(100):
            appeared = history["tail_set"].apply(lambda s: pair in s).to_numpy()

            # Frequency features (theo kỳ quay)
            freq_30 = appeared[-min(n, 30):].sum() / max(min(n, 30), 1)
            freq_60 = appeared[-min(n, 60):].sum() / max(min(n, 60), 1)
            freq_100 = appeared.sum() / max(n, 1)

            # Gap since last (kỳ)
            appeared_positions = np.where(appeared)[0]
            if len(appeared_positions) > 0:
                gap_since_last = n - 1 - appeared_positions[-1]
            else:
                gap_since_last = n

            # Gap statistics
            if len(appeared_positions) >= 2:
                gaps = np.diff(appeared_positions)
                avg_gap = float(gaps.mean())
                std_gap = float(gaps.std()) if len(gaps) > 1 else 1e-6
            elif len(appeared_positions) == 1:
                avg_gap = float(gap_since_last)
                std_gap = 1e-6
            else:
                avg_gap = float(n) if n > 0 else 100.0
                std_gap = 1e-6

            gap_zscore = (gap_since_last - avg_gap) / (std_gap + 1e-6)

            # Is hot: appeared in last 3 draws
            is_hot = float(appeared[-3:].all()) if n >= 3 else 0.0

            # Composite score
            scores[pair] = (
                freq_30 * 0.25 +
                freq_60 * 0.20 +
                freq_100 * 0.15 +
                max(gap_zscore, 0) * 0.005 * 0.25 +  # chỉ tính khi overdue (>0), scale nhỏ
                is_hot * 0.15
            )

        # Min-max normalize
        s_min, s_max = scores.min(), scores.max()
        if s_max - s_min > 1e-8:
            scores = (scores - s_min) / (s_max - s_min)

        # Top N
        top_indices = np.argsort(scores)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(scores[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "freq_gap",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return {
            "model_name": "freq_gap",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
