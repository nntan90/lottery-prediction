"""
data_utils.py — XSMB-Specific Data Loading Utilities (v4.0)

Tối ưu cho XSMB daily data:
  - _load_tails_by_draws(): Load N kỳ gần nhất (giống XSMN version)
  - _load_tails_by_weekday(): Load N kỳ CÙNG THỨ (weekday-filtered)
  - _load_tails_multi_window(): Load multiple lookback windows cùng lúc
  - _load_full_draw_data(): Load full lottery_draws (tất cả giải, không chỉ tail)
"""

import pandas as pd
import numpy as np
from datetime import date
from typing import Optional, Dict, List
from collections import defaultdict


def _load_tails_by_draws(
    db,
    region: str = "XSMB",
    province: Optional[str] = None,
    n_draws: int = 180,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Lấy N kỳ quay gần nhất từ tails_2d.
    Lookback theo kỳ quay (LIMIT), KHÔNG theo ngày.
    Tương thích interface với XSMN version.

    Returns:
        DataFrame: columns ['draw_date', 'tail_set', 'tail_count']
        Mỗi row = 1 kỳ quay, tail_set = frozenset of ints
        Sorted ascending (cũ → mới)
    """
    limit = 1000
    offset = 0
    all_rows = []

    while True:
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

        query = query.range(offset, offset + limit - 1)
        chunk = query.execute().data
        if not chunk:
            break

        all_rows.extend(chunk)

        # Fetch one extra date when possible so the oldest retained date is not
        # truncated by a PostgREST page boundary.
        unique_dates = {r["draw_date"] for r in all_rows}
        if len(unique_dates) > n_draws:
            break

        if len(chunk) < limit:
            break

        offset += limit

    if not all_rows:
        return pd.DataFrame(columns=["draw_date", "tail_set", "tail_count"])

    df = pd.DataFrame(all_rows)
    grouped = df.groupby("draw_date").agg(
        tail_set=("tail_2d", frozenset),
        tail_count=("tail_2d", "size"),
    ).reset_index()

    grouped["draw_date"] = pd.to_datetime(grouped["draw_date"])
    grouped = grouped.sort_values("draw_date").tail(n_draws)

    return grouped.reset_index(drop=True)


def _load_tails_by_weekday(
    db,
    weekday: int,
    region: str = "XSMB",
    province: Optional[str] = None,
    n_draws: int = 60,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Lấy N kỳ quay gần nhất CHỈ CÙNG THỨ (weekday-filtered).

    XSMB xổ hàng ngày, mỗi thứ có pattern riêng.
    VD: weekday=0 (Thứ 2) → chỉ lấy các kỳ quay vào thứ 2.

    Args:
        weekday: 0=Mon, 1=Tue, ..., 6=Sun
        n_draws: số kỳ quay CÙNG THỨ cần lấy (ví dụ 60 thứ 2)

    Returns:
        DataFrame: columns ['draw_date', 'tail_set'], sorted ascending
    """
    # Cần lấy n_draws * 7 kỳ rồi filter vì mỗi 7 kỳ mới có 1 kỳ cùng thứ
    fetch_limit = n_draws * 8  # buffer thêm chút
    all_data = _load_tails_by_draws(db, region, province, fetch_limit, before_date)

    if all_data.empty:
        return all_data

    # Filter cùng weekday
    all_data["weekday"] = all_data["draw_date"].dt.weekday
    filtered = all_data[all_data["weekday"] == weekday].tail(n_draws).copy()
    filtered = filtered.drop(columns=["weekday"]).reset_index(drop=True)

    return filtered


def _load_tails_multi_window(
    db,
    region: str = "XSMB",
    province: Optional[str] = None,
    windows: Optional[List[int]] = None,
    before_date: Optional[date] = None,
) -> Dict[int, pd.DataFrame]:
    """
    Load multiple lookback windows cùng lúc (1 query duy nhất).

    Args:
        windows: list of lookback sizes. Default = [7, 14, 30, 60, 100, 180]

    Returns:
        Dict[window_size, DataFrame] — mỗi DataFrame là 1 window
    """
    if windows is None:
        windows = [7, 14, 30, 60, 100, 180]

    max_window = max(windows)
    full_data = _load_tails_by_draws(db, region, province, max_window, before_date)

    result = {}
    for w in windows:
        result[w] = full_data.tail(w).reset_index(drop=True)

    return result


def _load_full_draw_data(
    db,
    region: str = "XSMB",
    n_draws: int = 100,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Load full lottery_draws data (tất cả giải, không chỉ tail).
    Dùng cho pattern analysis: giải nào xuất hiện pair, cluster analysis.

    Returns:
        DataFrame: columns ['draw_date', 'prize_name', 'value', ...]
    """
    query = db.supabase.table("lottery_draws") \
        .select("draw_date,prize_name,value") \
        .eq("region", region) \
        .order("draw_date", desc=True) \
        .limit(n_draws * 30)

    if before_date:
        query = query.lt("draw_date", before_date.isoformat())

    rows = query.execute().data
    if not rows:
        return pd.DataFrame(columns=["draw_date", "prize_name", "value"])

    df = pd.DataFrame(rows)
    df["draw_date"] = pd.to_datetime(df["draw_date"])
    df = df.sort_values("draw_date")
    return df.reset_index(drop=True)


def compute_pair_appeared_matrix(
    history: pd.DataFrame,
) -> np.ndarray:
    """
    Chuyển DataFrame history thành binary matrix (n_draws, 100).
    appeared[i][pair] = 1 nếu pair xuất hiện trong kỳ i.

    Args:
        history: DataFrame with 'tail_set' column (frozenset per draw)

    Returns:
        np.ndarray shape (n_draws, 100), dtype float32
    """
    n = len(history)
    matrix = np.zeros((n, 100), dtype=np.float32)

    for i, tail_set in enumerate(history["tail_set"]):
        for pair in tail_set:
            if 0 <= pair <= 99:
                matrix[i, pair] = 1.0

    return matrix
