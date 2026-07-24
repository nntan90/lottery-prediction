"""
model_xgboost.py — Model C: XGBoost Classifier Wrapper cho XSMN Ensemble
XSMN-specific: Lookback theo KỲ QUAY, tái sử dụng LotteryXGB class hiện có.

Phase 1: 1 model chung cho tất cả 7 tỉnh XSMN
         (gộp ~1,092 rows — đủ train)

Input:  pair_features hoặc on-the-fly từ tails_2d
Output: Top 5 cặp số + probability
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


def _load_tails_for_features(
    db,
    region: str,
    province: Optional[str] = None,
    n_draws: int = 250,
    before_date: Optional[date] = None,
    target_weekday: Optional[int] = None,
) -> pd.DataFrame:
    """
    Lấy tails_2d cho province/region để build features on-the-fly.
    Lookback theo kỳ quay (LIMIT). Pagination implemented to bypass 1000 limit.
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

        unique_dates = {
            r["draw_date"]
            for r in all_rows
            if target_weekday is None
            or date.fromisoformat(r["draw_date"]).weekday() == target_weekday
        }
        if len(unique_dates) >= n_draws:
            break

        if len(chunk) < limit:
            break

        offset += limit

    if target_weekday is not None:
        all_rows = [
            row for row in all_rows
            if date.fromisoformat(row["draw_date"]).weekday() == target_weekday
        ]
    return _extract_history(all_rows, max_rows=n_draws) if all_rows else pd.DataFrame()


def _get_active_model(db, region: str, province: Optional[str] = None, weekday: Optional[int] = None) -> Optional[dict]:
    """
    Lấy model active cho region/province.
    Ưu tiên: model có province == province, sau đó fallback province IS NULL (model chung).
    """
    def _query(prov_value, wd, family: Optional[str]):
        q = db.supabase.table("model_registry") \
            .select("*") \
            .eq("region", region) \
            .eq("status", "active") \
            .order("trained_at", desc=True) \
            .limit(1)

        if family is None:
            q = q.is_("model_name", "null")
        else:
            q = q.eq("model_name", family)

        if prov_value:
            q = q.eq("province", prov_value)
        else:
            q = q.is_("province", "null")

        if wd is not None:
            q = q.eq("weekday", wd)
        else:
            q = q.is_("weekday", "null")

        return q.execute().data

    locations = []
    if weekday is not None:
        locations.append((province, weekday))
    locations.append((province, None))
    if weekday is not None:
        locations.append((None, weekday))
    locations.append((None, None))

    # Preserve province/weekday specificity; within each location prefer exact family.
    for prov_value, wd in locations:
        for family in ("xgboost_core", "xgboost", None):
            result = _query(prov_value, wd, family)
            if result:
                return result[0]
    return None


def predict_xgboost(
    db,
    storage,
    province: Optional[str],
    target_date: date,
    region: str = "XSMN",
    n_draws: int = 250,
    top_n: int = 5,
    tmpdir: Optional[str] = None,
) -> Dict:
    """
    Model C: XGBoost classifier cho XSMN.

    Args:
        db: LotteryDB instance
        storage: LotteryStorage instance
        province: slug tỉnh
        target_date: ngày cần dự đoán
        n_draws: số kỳ lookback để build features
        top_n: số cặp top-N output
        tmpdir: thư mục tạm để cache model

    Returns:
        {
            'model_name': 'xgboost_core',
            'province': province,
            'top_pairs': [(pair, proba), ...],
            'n_draws_used': int,
            'model_version': str | None,
            'status': 'success' | 'error',
            'error_message': str | None,
            'execution_time_ms': int,
        }
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
                "error_message": f"Không có model XSMN active cho {province}",
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

        # 3. Build features on-the-fly (lookback theo kỳ)
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
            history = _load_tails_for_features(
                db,
                region,
                province,
                n_draws,
                before_date=target_date,
                target_weekday=weekday if region.upper() == "XSMN" else None,
            )
            if len(history) < 10:
                return {
                    "model_name": "xgboost_core",
                    "region": region,
                    "province": province,
                    "top_pairs": [],
                    "n_draws_used": len(history),
                    "model_version": registry.get("version"),
                    "status": "error",
                    "error_message": f"Không đủ history cho features: {len(history)} kỳ",
                    "execution_time_ms": int((time.time() - start_ms) * 1000),
                }

            feature_rows = build_features_for_day(target_date, history, target_tail_set=None)
            feat_df = pd.DataFrame(feature_rows)

        # 4. Predict
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
