"""
xsmb_ensemble — XSMB-Dedicated Multi-Model Ensemble (v4.2)

Package chứa 10 sub-models tối ưu cho XSMB, tận dụng data profile
dày đặc (1,095 kỳ/năm vs XSMN 156 kỳ/tỉnh/năm).

Models:
  A. frequency      — Multi-window frequency analysis
  B. gap_overdue    — Weekday-specific gap/overdue scoring
  C. markov         — Second-order Markov Chain
  D. xgboost_core   — XGBoost v4 (25 features)
  E. lstm           — Bi-LSTM + Attention
  F. bayesian       — Bayesian posterior estimation
  G. cyclic         — Cyclic Pattern FFT detector
  H. stats_freq_gap — Descriptive frequency/gap statistics
  I. chisquare_gof  — Chi-square goodness-of-fit
  J. chisquare_independence — Chi-square independence/homogeneity
  K. cdm             — Compound Dirichlet-Multinomial
"""

from src.xsmb_ensemble.model_frequency import predict_frequency
from src.xsmb_ensemble.model_gap import predict_gap
from src.xsmb_ensemble.model_markov import predict_markov
from src.xsmb_ensemble.model_xgboost import predict_xgboost
from src.xsmb_ensemble.model_lstm import predict_lstm
from src.xsmb_ensemble.model_bayesian import predict_bayesian
from src.xsmb_ensemble.model_cyclic import predict_cyclic
from src.xsmb_ensemble.model_stats_freq_gap import predict_stats_freq_gap
from src.xsmb_ensemble.model_chisquare_gof import predict_chisquare_gof
from src.xsmb_ensemble.model_chisquare_independence import predict_chisquare_independence
from src.xsmb_ensemble.model_cdm import predict_cdm
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
    "predict_stats_freq_gap",
    "predict_chisquare_gof",
    "predict_chisquare_independence",
    "predict_cdm",
    "compute_xsmb_ensemble",
    "format_ensemble_result",
    "format_model_prediction_log",
]
