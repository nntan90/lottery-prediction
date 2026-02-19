"""
feature_builder.py
Tính toán feature vector cho 100 cặp (00–99) tại một ngày cụ thể, cho 1 đài.

Features:
  - freq_30, freq_60, freq_100: tần suất xuất hiện trong N kỳ gần nhất
  - gap_since_last: số kỳ từ lần xuất hiện gần nhất
  - avg_gap_100, std_gap_100: thống kê chu kỳ
  - gap_zscore: (gap_since_last - avg_gap) / std_gap
  - is_even, is_high, sum_digits: đặc trưng của cặp số
  - day_of_week: thứ trong tuần
  - hit: label (1 = pair xuất hiện trong TAIL_SET ngày đó)
"""

import numpy as np
import pandas as pd
from datetime import date
from typing import List, Dict, Optional


def _extract_history(tails_data: List[Dict], max_rows: int = 100) -> pd.DataFrame:
    """
    Chuyển list bản ghi tails_2d thành DataFrame theo kỳ.
    Mỗi kỳ là 1 set các cặp xuất hiện.

    Returns:
        DataFrame indexed by draw_date (sorted ascending), column = 'tail_set' (frozenset)
    """
    if not tails_data:
        return pd.DataFrame(columns=["draw_date", "tail_set"])

    df = pd.DataFrame(tails_data)
    # Group by draw_date → set of tail_2d values
    grouped = df.groupby("draw_date")["tail_2d"].apply(frozenset).reset_index()
    grouped.columns = ["draw_date", "tail_set"]
    grouped["draw_date"] = pd.to_datetime(grouped["draw_date"])
    grouped = grouped.sort_values("draw_date").tail(max_rows)
    return grouped


def build_features_for_day(
    target_date: date,
    history: pd.DataFrame,   # from _extract_history, does NOT include target_date
    target_tail_set: Optional[frozenset] = None,  # TAIL_SET của target_date (nếu biết)
) -> List[Dict]:
    """
    Tính feature vector cho 100 cặp (00–99) tại target_date.

    Args:
        target_date: ngày cần tính feature
        history: DataFrame lịch sử (không bao gồm target_date)
        target_tail_set: TAIL_SET của target_date (để tính label hit). None nếu đang predict tương lai.

    Returns:
        List of 100 dicts (1 dict per pair 0–99)
    """
    dow = target_date.weekday()  # 0=Mon..6=Sun
    n = len(history)
    rows = []

    for pair in range(100):
        # Tìm các kỳ trong đó pair xuất hiện
        appeared = history["tail_set"].apply(lambda s: pair in s)
        appeared_indices = history.index[appeared].tolist()

        # Frequency
        freq_30  = appeared.tail(30).sum() / min(n, 30) if n > 0 else 0.0
        freq_60  = appeared.tail(60).sum() / min(n, 60) if n > 0 else 0.0
        freq_100 = appeared.tail(100).sum() / min(n, 100) if n > 0 else 0.0

        # Gap since last
        if appeared_indices:
            last_idx = appeared_indices[-1]
            pos_last = history.index.tolist().index(last_idx)
            gap_since_last = n - 1 - pos_last  # số kỳ từ lần cuối (0 = kỳ ngay trước)
        else:
            gap_since_last = n  # chưa bao giờ xuất hiện

        # Gap analysis (khoảng cách giữa các lần xuất hiện)
        if len(appeared_indices) >= 2:
            positions = [history.index.tolist().index(i) for i in appeared_indices]
            gaps = np.diff(positions).tolist()
            avg_gap = float(np.mean(gaps))
            std_gap = float(np.std(gaps)) if len(gaps) > 1 else 0.0
        elif len(appeared_indices) == 1:
            avg_gap = float(gap_since_last)
            std_gap = 0.0
        else:
            avg_gap = float(n) if n > 0 else 100.0
            std_gap = 0.0

        gap_zscore = (gap_since_last - avg_gap) / (std_gap + 1e-6)

        # Pair characteristics
        is_even = (pair % 2 == 0)
        is_high = (pair >= 50)
        sum_digits = (pair // 10) + (pair % 10)

        # Label
        hit = None
        if target_tail_set is not None:
            hit = pair in target_tail_set

        rows.append({
            "feature_date":  target_date.isoformat(),
            "pair":          pair,
            "freq_30":       round(freq_30, 4),
            "freq_60":       round(freq_60, 4),
            "freq_100":      round(freq_100, 4),
            "gap_since_last":gap_since_last,
            "avg_gap_100":   round(avg_gap, 2),
            "std_gap_100":   round(std_gap, 2),
            "gap_zscore":    round(gap_zscore, 4),
            "is_even":       is_even,
            "is_high":       is_high,
            "sum_digits":    sum_digits,
            "day_of_week":   dow,
            "hit":           hit,
        })

    return rows


def build_feature_matrix(feature_rows: List[Dict]) -> pd.DataFrame:
    """
    Chuyển list feature dicts thành DataFrame sẵn sàng cho XGBoost.

    Returns:
        X: DataFrame features (100 rows × 10 cols)
        y: Series labels (100 rows, bool) hoặc None nếu không có hit
    """
    df = pd.DataFrame(feature_rows)

    FEATURE_COLS = [
        "freq_30", "freq_60", "freq_100",
        "gap_since_last", "avg_gap_100", "std_gap_100", "gap_zscore",
        "is_even", "is_high", "sum_digits",
        "day_of_week",
    ]

    X = df[FEATURE_COLS].astype(float)

    if "hit" in df.columns and df["hit"].notna().all():
        y = df["hit"].astype(int)
    else:
        y = None

    return X, y
