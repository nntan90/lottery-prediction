"""
credibility_config.py — Configuration constants for Model Credibility Scoring.

All thresholds, weights, and tuning knobs externalized here
for easy A/B testing without touching scorer logic.
"""

import os
from typing import Dict

# ─── Dimension Weights (sum = 1.0) ──────────────────────────────────────────
# Controls how much each credibility dimension contributes to composite score.
DIM_WEIGHTS: Dict[str, float] = {
    "recency_mrr":        0.30,   # Recent performance (decay-weighted)
    "streak_momentum":    0.25,   # Hot/cold streak pattern
    "ndcg_score":         0.15,   # Ranking quality (DCG-based)
    "consensus_accuracy": 0.10,   # How often model's consensus picks hit
    "stability_index":    0.10,   # Output consistency
    "recovery_speed":     0.10,   # Speed of recovery after miss streaks
}

# ─── Recency Decay ──────────────────────────────────────────────────────────
# decay^(days_ago): 0.85^1=0.85, 0.85^3=0.61, 0.85^7=0.32, 0.85^14=0.10
RECENCY_DECAY: float = 0.85

# ─── Lookback Draws ─────────────────────────────────────────────────────────
# How many past draws to evaluate for credibility.
# XSMB: daily → 14 draws ≈ 2 weeks
# XSMN: weekly per province → 8 draws ≈ 2 months
LOOKBACK_XSMB: int = 14
LOOKBACK_XSMN: int = 8

# ─── Streak Momentum Mapping ────────────────────────────────────────────────
# Consecutive hit/miss patterns → momentum score.
# hot_3+ = hit 3+ kỳ liên tiếp (đang rất nóng)
# cold_3+ = miss 3+ kỳ liên tiếp (đang rất lạnh)
STREAK_SCORES: Dict[str, float] = {
    "hot_3+":  1.00,   # Đang chuỗi trúng ≥3 kỳ
    "hot_2":   0.70,   # Trúng 2 kỳ liên tiếp
    "warm":    0.50,   # Trúng kỳ mới nhất (1 hit)
    "neutral": 0.30,   # Sai kỳ mới nhất (1 miss)
    "cold_2":  0.15,   # Sai 2 kỳ liên tiếp
    "cold_3+": 0.05,   # Sai ≥3 kỳ liên tiếp
}

# ─── Weight Smoothing & Clamping ────────────────────────────────────────────
# Final weight = SMOOTHING × credibility + (1 - SMOOTHING) × config_weight
# This prevents wild swings when credibility changes rapidly.
SMOOTHING: float = 0.60          # 60% credibility-driven, 40% config anchor

# Per-model weight floor/ceiling after smoothing
MIN_WEIGHT: float = 0.05        # No model below 5%
MAX_WEIGHT: float = 0.35        # No model above 35%

# ─── Confidence Mapping ─────────────────────────────────────────────────────
# Maps composite credibility score → confidence multiplier for Borda scoring.
# High credibility → confidence near 1.0 (full Borda points)
# Low credibility → confidence < 1.0 (reduced Borda points)
CONFIDENCE_FLOOR: float = 0.4   # Worst model still gets 40% of its Borda points
CONFIDENCE_CEIL: float = 1.2    # Best model gets up to 120% boost

# ─── Cold-Start Default ─────────────────────────────────────────────────────
# Models with < MIN_EVALUATED draws use config weight directly.
MIN_EVALUATED: int = 3          # Need at least 3 evaluated draws
COLD_START_CONFIDENCE: float = 0.8  # Conservative confidence for new models


def load_credibility_config_from_yaml() -> dict:
    """
    Load credibility config from config/scoring.yaml if available.
    Falls back to module-level constants if not found.

    Returns:
        dict with all credibility configuration values
    """
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "scoring.yaml"
    )
    try:
        import yaml
        with open(config_path, "r") as f:
            full_cfg = yaml.safe_load(f)
        cred_cfg = full_cfg.get("credibility", {})
        if not cred_cfg or not cred_cfg.get("enabled", True):
            return {}
        return cred_cfg
    except Exception:
        return {}
