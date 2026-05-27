"""
xsmb_ensemble — XSMB-Dedicated Multi-Model Ensemble (v4.0)

Package chứa 7 sub-models tối ưu cho XSMB, tận dụng data profile
dày đặc (1,095 kỳ/năm vs XSMN 156 kỳ/tỉnh/năm).

Models:
  A. frequency      — Multi-window frequency analysis
  B. gap_overdue    — Weekday-specific gap/overdue scoring
  C. markov         — Second-order Markov Chain
  D. xgboost_core   — XGBoost v4 (25 features)
  E. lstm           — Bi-LSTM + Attention
  F. bayesian       — Bayesian posterior estimation
  G. cyclic         — Cyclic Pattern FFT detector
"""

from src.xsmb_ensemble.model_frequency import predict_frequency
from src.xsmb_ensemble.model_gap import predict_gap
from src.xsmb_ensemble.model_markov import predict_markov
from src.xsmb_ensemble.model_xgboost import predict_xgboost
from src.xsmb_ensemble.model_lstm import predict_lstm
from src.xsmb_ensemble.model_bayesian import predict_bayesian
from src.xsmb_ensemble.model_cyclic import predict_cyclic
from src.xsmb_ensemble.ensemble_engine import (
    compute_xsmb_ensemble,
    format_ensemble_result,
    format_model_prediction_log,
)

__all__ = [
    "predict_frequency",
    "predict_gap",
    "predict_markov",
    "predict_xgboost",
    "predict_lstm",
    "predict_bayesian",
    "predict_cyclic",
    "compute_xsmb_ensemble",
    "format_ensemble_result",
    "format_model_prediction_log",
]
