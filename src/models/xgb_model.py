"""
xgb_model.py
XGBoost wrapper cho bài toán dự đoán 2 số cuối xổ số.

Mỗi instance là 1 model cho 1 đài (region + province).
Input: 100 feature vectors (1 per pair 00–99)
Output: top-k pairs có xác suất xuất hiện cao nhất
"""

import os
import joblib
import numpy as np
import pandas as pd
from typing import List, Tuple, Optional

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("⚠️ XGBoost not available. Install: pip install xgboost")


FEATURE_COLS = [
    "freq_30", "freq_60", "freq_100",
    "gap_since_last", "avg_gap_100", "std_gap_100", "gap_zscore",
    "is_even", "is_high", "sum_digits",
    "day_of_week",
]


class LotteryXGB:
    """XGBoost model wrapper cho dự đoán 2 số cuối."""

    def __init__(
        self,
        n_estimators: int = 300,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        random_state: int = 42,
    ):
        self.params = dict(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            subsample=subsample,
            colsample_bytree=colsample_bytree,
            random_state=random_state,
            eval_metric="auc",
            use_label_encoder=False,
        )
        self.model = None
        self.feature_cols = FEATURE_COLS

    def train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> dict:
        """
        Train XGBoost classifier.
        Returns dict metrics: auc_val, hit_rate_top3_val.
        """
        if not XGB_AVAILABLE:
            raise ImportError("XGBoost not installed")

        self.model = xgb.XGBClassifier(**self.params)

        eval_set = [(X_val, y_val)] if X_val is not None else None
        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            verbose=False,
        )

        metrics = {}
        if X_val is not None and y_val is not None:
            from sklearn.metrics import roc_auc_score
            probs = self.model.predict_proba(X_val)[:, 1]
            try:
                metrics["auc"] = round(float(roc_auc_score(y_val, probs)), 4)
            except Exception:
                metrics["auc"] = 0.5

            # Hit-rate@3: mỗi kỳ trong val, top-3 có trúng không?
            metrics["hit_rate_top3"] = self._backtest_hit_rate(X_val, y_val, k=3)

        return metrics

    def _backtest_hit_rate(
        self, X: pd.DataFrame, y: pd.Series, k: int = 3
    ) -> float:
        """
        Tính hit-rate khi chọn top-k pairs.
        Lưu ý: mỗi 100 rows liên tiếp là 1 kỳ (pair 0..99).
        """
        if len(X) < 100:
            return 0.0

        probs = self.model.predict_proba(X)[:, 1]
        hits = 0
        total_draws = len(X) // 100

        for i in range(total_draws):
            start = i * 100
            p_slice = probs[start:start + 100]
            y_slice = y.iloc[start:start + 100]

            top_k_indices = np.argsort(p_slice)[-k:]
            if y_slice.iloc[top_k_indices].any():
                hits += 1

        return round(hits / total_draws, 4) if total_draws > 0 else 0.0

    def predict_proba_all(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict xác suất cho 100 cặp (00–99).
        X phải có đúng 100 rows, index = pair (0..99).
        """
        if self.model is None:
            raise ValueError("Model chưa được train hoặc load!")
        probs = self.model.predict_proba(X[self.feature_cols])[:, 1]
        return probs

    def top_k(self, X: pd.DataFrame, k: int = 3) -> List[Tuple[int, float]]:
        """
        Trả về top-k cặp số có xác suất cao nhất.

        Returns:
            List of (pair, probability) sorted by prob desc
        """
        probs = self.predict_proba_all(X)
        top_indices = np.argsort(probs)[-k:][::-1]
        return [(int(idx), round(float(probs[idx]), 4)) for idx in top_indices]

    def save(self, filepath: str):
        """Lưu model ra file .pkl"""
        if self.model is None:
            raise ValueError("Không có model để lưu!")
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        joblib.dump({"model": self.model, "feature_cols": self.feature_cols}, filepath)
        print(f"✅ Model saved: {filepath}")

    def load(self, filepath: str):
        """Load model từ file .pkl"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Không tìm thấy model: {filepath}")
        data = joblib.load(filepath)
        self.model = data["model"]
        self.feature_cols = data.get("feature_cols", FEATURE_COLS)
        print(f"✅ Model loaded: {filepath}")
