"""
xsmn_ensemble — Multi-Model Ensemble pipeline cho XSMN (v3.2 — 5 Models).

Modules:
  - resolve_provinces: Dynamic province resolution theo DOW
  - model_frequency:  Model 1 — Frequency/Hot-Cool scoring (Rule-based)
  - model_gap:        Model 2 — Gap/Overdue scoring (Rule-based)
  - model_markov:     Model 3 — Markov Chain transition probability
  - model_xgboost:    Model 4 — XGBoost classifier wrapper
  - model_lstm:       Model 5 — LSTM/GRU sequence model (PyTorch)
  - ensemble_engine:  Weighted Borda Count + CombSUM aggregation

  - model_freq_gap:   [LEGACY v3.1] Combined Freq+Gap (kept for backward compat)
"""
