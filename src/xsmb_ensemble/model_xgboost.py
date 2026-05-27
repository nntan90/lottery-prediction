"""
model_xgboost.py — Model D: XGBoost v4 Wrapper (XSMB v4)

XSMB-optimized XGBoost với 25 features (thêm 8 features mới):
  - freq_3, freq_14:      Ultra-short & medium-term frequency
  - weekday_freq_30:      Frequency tính trên cùng weekday
  - gap_percentile:       Gap hiện tại so với historical distribution
  - neighbor_freq_7:      Tần suất 2 pair lân cận (±1, ±10)
  - last_position:        Giải nào xuất hiện lần cuối (encoded)
  - streak_length:        Số kỳ liên tiếp xuất hiện / vắng mặt
  - cross_pair_corr:      Correlation với pair tương quan cao nhất

Backward compatible: nếu model chỉ có 17 features → dùng 17 features cũ.
"""

import os
import sys
import time
import tempfile
import numpy as np
import pandas as pd
from datetime import date
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.models.xgb_model import LotteryXGB, FEATURE_COLS
from src.features.feature_builder import _extract_history, build_features_for_day

# v4 extended features (thêm 8 features)
XSMB_EXTRA_FEATURES = [
    "freq_3",
    "freq_14",
    "weekday_freq_30",
    "gap_percentile",
    "neighbor_freq_7",
    "last_position_encoded",
    "streak_length",
    "cross_pair_corr",
]

XSMB_FEATURE_COLS_V4 = FEATURE_COLS + XSMB_EXTRA_FEATURES


def _load_tails_for_features(
    db,
    region: str = "XSMB",
    province: Optional[str] = None,
    n_draws: int = 240,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Lấy tails_2d để build features on-the-fly.
    Lookback theo kỳ quay (LIMIT).
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
    return _extract_history(rows, max_rows=n_draws) if rows else pd.DataFrame()


def _get_active_model(
    db,
    region: str = "XSMB",
    province: Optional[str] = None,
    weekday: Optional[int] = None,
) -> Optional[dict]:
    """
    Lấy model active cho XSMB.
    Ưu tiên: weekday-specific → general → fallback.
    """
    def _query(prov_value: Optional[str], wd: Optional[int]):
        q = db.supabase.table("model_registry") \
            .select("*") \
            .eq("region", region) \
            .eq("status", "active") \
            .order("trained_at", desc=True) \
            .limit(1)

        if prov_value:
            q = q.eq("province", prov_value)
        else:
            q = q.is_("province", "null")

        if wd is not None:
            q = q.eq("weekday", wd)
        else:
            q = q.is_("weekday", "null")

        return q.execute().data

    # 1. Weekday-specific model
    if weekday is not None:
        result = _query(province, weekday)
        if result:
            return result[0]

    # 2. General model (no weekday)
    result = _query(province, None)
    if result:
        return result[0]

    # 3. Fallback: province=NULL + no weekday
    if province is not None:
        result = _query(None, weekday)
        if result:
            return result[0]
        result = _query(None, None)
        return result[0] if result else None

    return None


def _enrich_features_v4(
    feat_df: pd.DataFrame,
    history: pd.DataFrame,
    target_date: date,
) -> pd.DataFrame:
    """
    Thêm 8 features v4 vào feature DataFrame.
    Nếu feature đã tồn tại (từ pair_features table), skip.

    Args:
        feat_df: DataFrame 100 rows × 17+ columns (base features)
        history: DataFrame with 'tail_set' column
        target_date: ngày predict

    Returns:
        DataFrame 100 rows × 25 columns
    """
    n = len(history)
    df = feat_df.copy()

    # Pre-compute appeared matrix
    from src.xsmb_ensemble.data_utils import compute_pair_appeared_matrix
    appeared = compute_pair_appeared_matrix(history) if n > 0 else np.zeros((0, 100))

    for col_name in XSMB_EXTRA_FEATURES:
        if col_name in df.columns and df[col_name].notna().all():
            continue  # Đã có trong DB, skip

        if col_name == "freq_3" and n >= 3:
            df["freq_3"] = [appeared[-3:, p].mean() for p in range(100)]
        elif col_name == "freq_14" and n >= 14:
            df["freq_14"] = [appeared[-14:, p].mean() for p in range(100)]
        elif col_name == "weekday_freq_30":
            # Tính frequency chỉ trên cùng weekday
            weekday = target_date.weekday()
            if "draw_date" in history.columns and n > 0:
                wd_mask = history["draw_date"].dt.weekday == weekday
                wd_history = history[wd_mask]
                if len(wd_history) >= 3:
                    wd_appeared = compute_pair_appeared_matrix(wd_history)
                    n_wd = len(wd_history)
                    wd_window = min(n_wd, 30)
                    df["weekday_freq_30"] = [wd_appeared[-wd_window:, p].mean() for p in range(100)]
                else:
                    df["weekday_freq_30"] = df.get("freq_30", 0.0)
            else:
                df["weekday_freq_30"] = df.get("freq_30", 0.0)
        elif col_name == "gap_percentile":
            percs = []
            for p in range(100):
                col = appeared[:, p] if n > 0 else np.array([])
                positions = np.where(col > 0)[0]
                if len(positions) >= 3:
                    gaps = np.diff(positions)
                    current_gap = n - 1 - positions[-1] if len(positions) > 0 else n
                    percs.append(float(np.mean(gaps <= current_gap)))
                else:
                    percs.append(0.5)
            df["gap_percentile"] = percs
        elif col_name == "neighbor_freq_7":
            # Tần suất pair lân cận: avg(freq_7 of pair±1, pair±10)
            if n >= 7:
                freq7_all = np.array([appeared[-7:, p].mean() for p in range(100)])
                neighbor_freqs = []
                for p in range(100):
                    neighbors = []
                    for delta in [-1, 1, -10, 10]:
                        nb = p + delta
                        if 0 <= nb <= 99:
                            neighbors.append(freq7_all[nb])
                    neighbor_freqs.append(float(np.mean(neighbors)) if neighbors else 0.0)
                df["neighbor_freq_7"] = neighbor_freqs
            else:
                df["neighbor_freq_7"] = 0.0
        elif col_name == "last_position_encoded":
            # Simplified: 0 = chưa từng, 1 = xuất hiện gần, 2 = xa
            if n > 0:
                last_pos = []
                for p in range(100):
                    col = appeared[:, p]
                    positions = np.where(col > 0)[0]
                    if len(positions) > 0:
                        gap = n - 1 - positions[-1]
                        if gap <= 3:
                            last_pos.append(2)  # very recent
                        elif gap <= 10:
                            last_pos.append(1)  # recent
                        else:
                            last_pos.append(0)  # far
                    else:
                        last_pos.append(0)
                df["last_position_encoded"] = last_pos
            else:
                df["last_position_encoded"] = 0
        elif col_name == "streak_length":
            # Streak = liên tiếp xuất hiện (positive) hoặc vắng (negative)
            if n > 0:
                streaks = []
                for p in range(100):
                    col = appeared[:, p]
                    streak = 0
                    last_val = col[-1]
                    for val in reversed(col):
                        if val == last_val:
                            streak += 1
                        else:
                            break
                    # Positive streak = xuất hiện liên tiếp, negative = vắng liên tiếp
                    streaks.append(streak if last_val > 0.5 else -streak)
                df["streak_length"] = streaks
            else:
                df["streak_length"] = 0
        elif col_name == "cross_pair_corr":
            # Cross-correlation: max correlation với bất kỳ pair nào khác
            if n >= 20:
                corr_matrix = np.corrcoef(appeared.T)
                np.fill_diagonal(corr_matrix, 0)  # exclude self
                # NaN → 0
                corr_matrix = np.nan_to_num(corr_matrix, 0.0)
                df["cross_pair_corr"] = [float(corr_matrix[p].max()) for p in range(100)]
            else:
                df["cross_pair_corr"] = 0.0
        else:
            # Column không nhận ra → fill 0
            if col_name not in df.columns:
                df[col_name] = 0.0

    return df


def predict_xgboost(
    db,
    storage,
    province: Optional[str],
    target_date: date,
    region: str = "XSMB",
    n_draws: int = 240,
    top_n: int = 5,
    tmpdir: Optional[str] = None,
) -> Dict:
    """
    Model D: XGBoost v4 cho XSMB.

    Backward compatible:
    - Nếu model trained với 17 features → dùng 17 features
    - Nếu model trained với 25 features (v4) → dùng 25 features

    Args:
        db: LotteryDB instance
        storage: LotteryStorage instance
        province: None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ lookback để build features
        top_n: số cặp top-N output
        tmpdir: thư mục tạm

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()
    _tmpdir = tmpdir or tempfile.mkdtemp()

    try:
        weekday = target_date.weekday()

        # 1. Tìm model active
        registry = _get_active_model(db, region, province, weekday)
        if not registry:
            return {
                "model_name": "xgboost_core",
                "region": region,
                "province": province,
                "top_pairs": [],
                "n_draws_used": 0,
                "model_version": None,
                "status": "error",
                "error_message": f"Không có model XSMB active cho weekday={weekday}",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        # 2. Download & load model
        file_path = registry["file_path"]
        local_path = os.path.join(_tmpdir, os.path.basename(file_path))

        if not os.path.exists(local_path):
            if not storage.download_model(file_path, local_path):
                return {
                    "model_name": "xgboost_core",
                    "region": region,
                    "province": province,
                    "top_pairs": [],
                    "n_draws_used": 0,
                    "model_version": registry.get("version"),
                    "status": "error",
                    "error_message": f"Download model thất bại: {file_path}",
                    "execution_time_ms": int((time.time() - start_ms) * 1000),
                }

        model = LotteryXGB()
        model.load(local_path)

        # 3. Build features
        # Ưu tiên pair_features DB, fallback tails_2d
        feat_query = db.supabase.table("pair_features") \
            .select(",".join(FEATURE_COLS + ["pair"])) \
            .eq("feature_date", target_date.isoformat()) \
            .eq("region", region) \
            .order("pair")

        if province:
            feat_query = feat_query.eq("province", province)
        else:
            feat_query = feat_query.is_("province", "null")

        feat_result = feat_query.execute()

        if feat_result.data and len(feat_result.data) == 100:
            feat_df = pd.DataFrame(feat_result.data)
        else:
            # Build on-the-fly
            history = _load_tails_for_features(db, region, province, n_draws, before_date=target_date)
            if len(history) < 10:
                return {
                    "model_name": "xgboost_core",
                    "region": region,
                    "province": province,
                    "top_pairs": [],
                    "n_draws_used": len(history),
                    "model_version": registry.get("version"),
                    "status": "error",
                    "error_message": f"Không đủ history: {len(history)} kỳ",
                    "execution_time_ms": int((time.time() - start_ms) * 1000),
                }
            feature_rows = build_features_for_day(target_date, history, target_tail_set=None)
            feat_df = pd.DataFrame(feature_rows)

        # 4. Enrich with v4 features if model supports them
        # Check model.feature_cols to see what it expects
        model_feature_cols = getattr(model, "feature_cols", FEATURE_COLS)

        if any(f in model_feature_cols for f in XSMB_EXTRA_FEATURES):
            # Model was trained with v4 features → enrich
            history = _load_tails_for_features(db, region, province, n_draws, before_date=target_date)
            feat_df = _enrich_features_v4(feat_df, history, target_date)

        # 5. Predict
        top_k = model.top_k(feat_df, k=top_n)

        return {
            "model_name": "xgboost_core",
            "region": region,
            "province": province,
            "top_pairs": top_k,
            "n_draws_used": len(feat_df) // 100 if len(feat_df) >= 100 else 0,
            "model_version": registry.get("version"),
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return {
            "model_name": "xgboost_core",
            "region": region,
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "model_version": None,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
