"""
feature_builder.py
Tính toán feature vector cho 100 cặp (00–99) tại một ngày cụ thể, cho 1 đài.
Áp dụng cho cả XSMB (all) và XSMN (province).

Features v1 (cũ):
  - freq_30, freq_60, freq_100: tần suất xuất hiện trong N kỳ gần nhất
  - gap_since_last: số kỳ từ lần xuất hiện gần nhất
  - avg_gap_100, std_gap_100: thống kê chu kỳ
  - gap_zscore: (gap_since_last - avg_gap) / std_gap
  - is_even, is_high, sum_digits: đặc trưng của cặp số
  - day_of_week: thứ trong tuần

Features v2 (mới - tăng tín hiệu):
  - freq_7: tần suất 7 kỳ gần nhất (nóng/lạnh ngắn hạn)
  - consecutive_miss: số kỳ liên tiếp chưa xuất hiện
  - is_hot_3: xuất hiện trong cả 3 kỳ liên tiếp gần nhất
  - decade_freq_30: tần suất nhóm thập phân (00-09, 10-19...) 30 kỳ
  - mirror_freq_30: tần suất cặp đảo (23↔32) 30 kỳ
  - month_freq: tần suất trong cùng tháng lịch sử

Label:
  - hit: 1 nếu pair xuất hiện trong TAIL_SET ngày đó
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
    Áp dụng cho cả XSMB và XSMN.

    Args:
        target_date: ngày cần tính feature
        history: DataFrame lịch sử (không bao gồm target_date)
        target_tail_set: TAIL_SET của target_date (để tính label hit). None nếu đang predict tương lai.

    Returns:
        List of 100 dicts (1 dict per pair 0–99)
    """
    dow = target_date.weekday()   # 0=Mon..6=Sun
    target_month = target_date.month
    n = len(history)

    # ── Pre-compute: decade_freq_30 ──────────────────────────────────────────
    # Nhóm thập phân: pair 0–9 → decade 0, 10–19 → decade 1, ...
    # Tính tần suất mỗi chữ số đầu trong 30 kỳ gần nhất
    recent_30 = history.tail(30)
    n_30 = len(recent_30)
    decade_count = {d: 0.0 for d in range(10)}
    if n_30 > 0:
        for tail_set in recent_30["tail_set"]:
            for p in tail_set:
                decade_count[p // 10] += 1
        # Chuẩn hóa: tỉ lệ trong 30 kỳ × 10 cặp/decade (kỳ vọng 2.7/10 mỗi kỳ)
        for d in decade_count:
            decade_count[d] = decade_count[d] / (n_30 * 10)

    # ── Pre-compute: mirror_freq_30 ──────────────────────────────────────────
    # Tần suất cặp đảo: 23 → 32, 45 → 54, v.v.
    # Cặp palindrome (00, 11, ..., 99) mirror là chính nó
    mirror_count = {}
    if n_30 > 0:
        for tail_set in recent_30["tail_set"]:
            for p in tail_set:
                mirror_count[p] = mirror_count.get(p, 0) + 1

    # ── Pre-compute: month_freq ──────────────────────────────────────────────
    # Lọc các kỳ trong cùng tháng với target_date (toàn bộ lịch sử)
    if n > 0 and "draw_date" in history.columns:
        same_month_mask = history["draw_date"].dt.month == target_month
        same_month_history = history[same_month_mask]
        n_month = len(same_month_history)
    else:
        same_month_history = pd.DataFrame()
        n_month = 0

    # ── Main loop: tính features cho từng cặp 00–99 ─────────────────────────
    rows = []
    history_index_list = history.index.tolist()

    for pair in range(100):

        # Tìm các kỳ trong đó pair xuất hiện (boolean mask)
        appeared = history["tail_set"].apply(lambda s: pair in s)
        appeared_arr = appeared.to_numpy()

        # ── Frequency features ──────────────────────────────────────────
        freq_30  = appeared.tail(30).sum()  / max(min(n, 30), 1)
        freq_60  = appeared.tail(60).sum()  / max(min(n, 60), 1)
        freq_100 = appeared.tail(100).sum() / max(min(n, 100), 1)
        freq_7   = appeared.tail(7).sum()   / max(min(n, 7), 1)   # NEW

        # ── Gap since last ──────────────────────────────────────────────
        appeared_indices = [idx for idx, ap in zip(history_index_list, appeared_arr) if ap]

        if appeared_indices:
            last_idx = appeared_indices[-1]
            pos_last = history_index_list.index(last_idx)
            gap_since_last = n - 1 - pos_last   # 0 = kỳ ngay trước
        else:
            gap_since_last = n   # chưa bao giờ xuất hiện

        # ── Gap analysis ────────────────────────────────────────────────
        if len(appeared_indices) >= 2:
            positions = [history_index_list.index(i) for i in appeared_indices]
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

        # ── Pair characteristics ────────────────────────────────────────
        is_even    = (pair % 2 == 0)
        is_high    = (pair >= 50)
        sum_digits = (pair // 10) + (pair % 10)

        # ── NEW: consecutive_miss ───────────────────────────────────────
        # Số kỳ LIÊN TIẾP gần nhất mà pair KHÔNG xuất hiện
        # (đếm từ kỳ mới nhất về trước, dừng khi gặp kỳ có pair)
        consecutive_miss = 0
        for ap in reversed(appeared_arr):
            if not ap:
                consecutive_miss += 1
            else:
                break

        # ── NEW: is_hot_3 ───────────────────────────────────────────────
        # TRUE nếu pair xuất hiện trong tất cả 3 kỳ gần nhất
        if n >= 3:
            last3 = appeared_arr[-3:]
            is_hot_3 = bool(last3.all())
        else:
            is_hot_3 = False

        # ── NEW: decade_freq_30 ─────────────────────────────────────────
        decade_freq_30 = round(decade_count.get(pair // 10, 0.0), 4)

        # ── NEW: mirror_freq_30 ─────────────────────────────────────────
        # Cặp đảo: 23 → 32 (hoán vị chữ số)
        mirror_pair = (pair % 10) * 10 + (pair // 10)
        mirror_raw = mirror_count.get(mirror_pair, 0)
        mirror_freq_30 = round(mirror_raw / max(n_30, 1), 4)

        # ── NEW: month_freq ─────────────────────────────────────────────
        if n_month > 0:
            month_appeared = same_month_history["tail_set"].apply(lambda s: pair in s)
            month_freq = round(month_appeared.sum() / n_month, 4)
        else:
            month_freq = round(freq_100, 4)   # fallback: dùng freq_100

        # ── Label ───────────────────────────────────────────────────────
        hit = None
        if target_tail_set is not None:
            hit = pair in target_tail_set

        rows.append({
            "feature_date":    target_date.isoformat(),
            "pair":            pair,
            # v1 features
            "freq_30":         round(freq_30, 4),
            "freq_60":         round(freq_60, 4),
            "freq_100":        round(freq_100, 4),
            "gap_since_last":  gap_since_last,
            "avg_gap_100":     round(avg_gap, 2),
            "std_gap_100":     round(std_gap, 2),
            "gap_zscore":      round(gap_zscore, 4),
            "is_even":         is_even,
            "is_high":         is_high,
            "sum_digits":      sum_digits,
            "day_of_week":     dow,
            # v2 features (new)
            "freq_7":          round(freq_7, 4),
            "consecutive_miss":consecutive_miss,
            "is_hot_3":        is_hot_3,
            "decade_freq_30":  decade_freq_30,
            "mirror_freq_30":  mirror_freq_30,
            "month_freq":      month_freq,
            # label
            "hit":             hit,
        })

    return rows


def build_feature_matrix(feature_rows: List[Dict]) -> pd.DataFrame:
    """
    Chuyển list feature dicts thành DataFrame sẵn sàng cho XGBoost.

    Returns:
        X: DataFrame features (100 rows × 17 cols)
        y: Series labels (100 rows, bool) hoặc None nếu không có hit
    """
    from src.models.xgb_model import FEATURE_COLS

    df = pd.DataFrame(feature_rows)
    X = df[FEATURE_COLS].astype(float)

    if "hit" in df.columns and df["hit"].notna().all():
        y = df["hit"].astype(int)
    else:
        y = None

    return X, y
