"""
ensemble_engine.py — XSMB Precision Ensemble v5.0

Kết hợp output từ 10 sub-models thành Top 3 cuối cùng.

Models (v4.2 — 10 models):
  A. frequency      — Multi-window frequency analysis
  B. gap_overdue    — Weekday-specific gap/overdue
  C. markov         — Second-order Markov Chain
  D. xgboost_core   — XGBoost v4 (25 features)
  E. lstm           — Bi-LSTM + Attention
  F. bayesian       — Bayesian posterior estimation
  G. cyclic         — Cyclic Pattern FFT detector
  H. stats_freq_gap — Descriptive frequency/gap statistics
  I. chisquare_gof  — Chi-square goodness-of-fit
  J. chisquare_independence — Chi-square independence/homogeneity

Aggregation (v5.0 — Precision Ensemble):
  1. Proportional Score Normalization — giữ raw score ratio giữa pick #1 và #2
  2. Weighted Aggregation — weight × confidence × normalized_score
  3. Continuous Consensus Amplifier — multiplicative, no threshold cliff
  4. Single-pass History Guard — one multiplicative modifier, no double penalty
  5. Pure Top 3 Selection — trust scoring, no artificial diversity

v5.0 Changes (vs v4.2):
  - Borda rank-based → Proportional raw score fusion
  - Additive consensus threshold bonus → Continuous multiplicative amplifier
  - Double penalty (additive + multiplicative) → Single multiplicative history guard
  - MMR diversity selection → Removed (clean scoring = natural diversity)
  - ~15 hyperparameters → ~6 hyperparameters

Config: scoring.yaml → xsmb_v5 section
"""

import os
from typing import List, Dict, Tuple, Optional
import numpy as np


# ─── Config ───────────────────────────────────────────────────────────[...]

def _load_scoring_config() -> dict:
    """Load scoring config from config/scoring.yaml."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "scoring.yaml"
    )
    try:
        import yaml
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}

_CFG = _load_scoring_config()

# XSMB v5 config
_V5_CFG = _CFG.get("xsmb_v5", {})

# Default weights (10 models, sum ≈ 1.0)
_w_cfg = _V5_CFG.get("weights", _CFG.get("xsmb_v4", {}).get("weights", {}))
DEFAULT_WEIGHTS: dict[str, float] = {
    "frequency":    _w_cfg.get("frequency",    0.07),
    "gap_overdue":  _w_cfg.get("gap_overdue",  0.07),
    "markov":       _w_cfg.get("markov",       0.12),
    "xgboost_core": _w_cfg.get("xgboost_core", 0.14),
    "lstm":         _w_cfg.get("lstm",         0.10),
    "bayesian":     _w_cfg.get("bayesian",     0.14),
    "cyclic":       _w_cfg.get("cyclic",       0.12),
    "stats_freq_gap": _w_cfg.get("stats_freq_gap", 0.06),
    "chisquare_gof": _w_cfg.get("chisquare_gof", 0.05),
    "chisquare_independence": _w_cfg.get("chisquare_independence", 0.05),
    "cdm":          _w_cfg.get("cdm",          0.08),
}

# ─── v5.0 Scoring Parameters ────────────────────────────────────────────────

# Consensus Amplifier: score *= 1 + ALPHA * (vote_count - 1), capped at MAX
_cons_cfg = _V5_CFG.get("consensus", {})
CONSENSUS_ALPHA = float(_cons_cfg.get("alpha", 0.20))
MAX_CONSENSUS_MULTIPLIER = float(_cons_cfg.get("max_multiplier", 1.8))

# History Guard: single multiplicative modifier
_hist_cfg = _V5_CFG.get("history_modifier", {})
HIST_HOT_4_PLUS = float(_hist_cfg.get("hot_4_plus", 0.00))
HIST_HOT_3 = float(_hist_cfg.get("hot_3", 0.40))
HIST_WARM_2 = float(_hist_cfg.get("warm_2", 0.70))
HIST_NEUTRAL_1 = float(_hist_cfg.get("neutral_1", 1.00))
HIST_COLD_PRESSURE = float(_hist_cfg.get("cold_pressure", 1.15))
HIST_COLD_MILD = float(_hist_cfg.get("cold_mild", 1.05))
HIST_TOXIC_COLD = float(_hist_cfg.get("toxic_cold", 0.60))

# History lookback
HIST_LOOKBACK_SAME_WEEKDAY = int(_V5_CFG.get("history_lookback_same_weekday", 5))
HIST_EXTENDED_LOOKBACK = int(_V5_CFG.get("history_extended_lookback", 10))

# Auto-weight (legacy, kept for compatibility)
_aw_cfg = _V5_CFG.get("auto_weight", _CFG.get("xsmb_v4", {}).get("auto_weight", {}))
AUTO_WEIGHT_ENABLED = bool(_aw_cfg.get("enabled", True))
AUTO_WEIGHT_SMOOTHING = float(_aw_cfg.get("smoothing", 0.7))
AUTO_WEIGHT_MIN = float(_aw_cfg.get("min_weight", 0.05))
AUTO_WEIGHT_MAX = float(_aw_cfg.get("max_weight", 0.35))


# ─── Model Display Names ────────────────────────────────────────────────────

MODEL_DISPLAY_NAME = {
    "frequency":    "Freq",
    "gap_overdue":  "Gap",
    "markov":       "Markov²",
    "xgboost_core": "XGB",
    "lstm":         "BiLSTM",
    "bayesian":     "Bayes",
    "cyclic":       "Cyclic",
    "stats_freq_gap": "StatsFG",
    "chisquare_gof": "ChiGOF",
    "chisquare_independence": "ChiInd",
    "cdm":          "CDM",
}

TOTAL_MODELS = 11


# ─── Candidate Shortlist (for Telegram audit log) ───────────────────────────

def _build_candidate_shortlist(
    sorted_pairs: list[tuple[int, float]],
    pair_vote_count: dict[int, int],
    pair_unique_models: dict[int, set],
    *,
    limit: int = 10,
) -> tuple[list[dict], str]:
    """Build a compact Top-N candidate audit log for Telegram."""
    candidates = []
    lines = ["📌 <b>Top 10 ứng viên multi-model (mỗi model Top 2)</b>"]

    for rank, (pair, score) in enumerate(sorted_pairs[:limit], start=1):
        model_names = sorted(pair_unique_models.get(pair, set()))
        display_models = [MODEL_DISPLAY_NAME.get(m, m) for m in model_names]
        vote_count = pair_vote_count.get(pair, 0)

        candidates.append({
            "rank": rank,
            "pair": pair,
            "score": round(score, 4),
            "vote_count": vote_count,
            "unique_model_count": len(model_names),
            "models": model_names,
        })

        source_str = ", ".join(display_models) if display_models else "-"
        lines.append(
            f"   {rank:02d}. <code>{pair:02d}</code> = {score:.3f}đ"
            f" | votes={vote_count}"
            f" | {source_str}"
        )

    return candidates, "\n".join(lines) if candidates else ""


# ─── Main Ensemble Function ─────────────────────────────────────────────────

def compute_xsmb_ensemble(
    model_results: List[Dict],
    recent_tails: List[int],
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
    extended_tails: Optional[List[int]] = None,
    model_confidences: Optional[dict[str, float]] = None,
) -> Dict:
    """
    XSMB v5.0 Precision Ensemble Scoring.

    Kết hợp output từ 10 models thành Top 3 cuối cùng.

    Scoring Pipeline:
      1. Proportional Score Normalization — raw_score / sum(scores) per model
      2. Weighted Aggregation — Σ weight_m × confidence_m × norm_score_m(pair)
      3. Consensus Amplifier — score × (1 + α × (vote_count - 1))
      4. History Guard — score × modifier (single multiplicative pass)
      5. Sort & Pick Top 3

    Args:
        model_results: List of dicts từ 10 sub-models (each has top_pairs)
        recent_tails: tails từ 5 kỳ cùng thứ gần nhất
        weights: override weights (default from config)
        top_n_output: số cặp output (default 3)
        extended_tails: tails 10 kỳ cho Toxic Gap check
        model_confidences: dict model_name → confidence [0, 1.2]

    Returns:
        Dict chứa top_pairs, scoring_log, metadata
    """
    w = weights if weights else DEFAULT_WEIGHTS.copy()

    # Filter successful results
    valid_results = [r for r in model_results if r.get("status") == "success" and r.get("top_pairs")]

    if not valid_results:
        return {
            "top_pairs": [],
            "contributing_models": [],
            "ensemble_method": "xsmb_precision_v5.0",
            "borda_details": {},
            "consensus_pairs": [],
            "scoring_log": "",
            "candidate_log": "",
            "top_candidates": [],
            "models_active": 0,
            "models_total": TOTAL_MODELS,
        }

    # Re-normalize weights to active models only
    active_model_names = {r["model_name"] for r in valid_results}
    w = {k: v for k, v in w.items() if k in active_model_names}
    w_sum = sum(w.values())
    if w_sum > 0:
        w = {k: v / w_sum for k, v in w.items()}
    print(f"     🔧 Active models: {len(active_model_names)}/{TOTAL_MODELS}, "
          f"weights re-normalized: {', '.join(f'{k}={v:.2f}' for k,v in w.items())}")

    if model_confidences is None:
        model_confidences = {}

    # ── Step 1 & 2: Proportional Score Normalization + Weighted Aggregation ──
    pair_scores: dict[int, float] = {}
    pair_vote_count: dict[int, int] = {}
    pair_unique_models: dict[int, set] = {}
    pair_model_details: dict[int, list] = {}  # For scoring log
    contributing = []

    for result in valid_results:
        model_name = result["model_name"]
        contributing.append(model_name)
        weight = w.get(model_name, 0.10)
        conf = model_confidences.get(model_name, 1.0)

        top_pairs = result["top_pairs"]

        # ── FIX: Filter out None scores before normalization ──
        # This prevents TypeError: bad operand type for abs(): 'NoneType'
        valid_pairs = [(pair, score) for pair, score in top_pairs if score is not None]
        
        if not valid_pairs:
            # Model returned all None scores → skip this model
            print(f"     ⚠️  Model {model_name}: all scores are None, skipping")
            contributing.remove(model_name)
            continue

        # Proportional normalization: score / sum(scores)
        # This preserves the confidence gap between pick #1 and #2
        raw_scores = [abs(s) for _, s in valid_pairs]
        score_sum = sum(raw_scores)

        for rank_idx, (pair, raw_score) in enumerate(valid_pairs):
            # Normalize: proportional share of model's total score output
            if score_sum > 0:
                norm_score = abs(raw_score) / score_sum
            else:
                # Equal split fallback
                norm_score = 1.0 / max(len(valid_pairs), 1)

            # Weighted contribution: weight × confidence × normalized_score
            weighted_pts = weight * conf * norm_score

            pair_scores[pair] = pair_scores.get(pair, 0) + weighted_pts
            pair_vote_count[pair] = pair_vote_count.get(pair, 0) + 1

            if pair not in pair_unique_models:
                pair_unique_models[pair] = set()
            pair_unique_models[pair].add(model_name)

            if pair not in pair_model_details:
                pair_model_details[pair] = []
            pair_model_details[pair].append({
                "model": model_name,
                "rank": rank_idx + 1,
                "raw_score": raw_score,
                "norm_score": norm_score,
                "weight": weight,
                "conf": conf,
                "contribution": weighted_pts,
            })

    # ── Step 3: Continuous Consensus Amplifier ──
    consensus_applied: dict[int, float] = {}
    for pair in list(pair_scores.keys()):
        vote_count = len(pair_unique_models.get(pair, set()))
        if vote_count > 1:
            # Multiplicative: 1 + α × (votes - 1), capped
            multiplier = min(
                1.0 + CONSENSUS_ALPHA * (vote_count - 1),
                MAX_CONSENSUS_MULTIPLIER,
            )
            pair_scores[pair] *= multiplier
            consensus_applied[pair] = multiplier
        else:
            consensus_applied[pair] = 1.0

    # ── Step 4: Single-pass History Guard ──
    recent_counts: dict[int, int] = {}
    for t in recent_tails:
        recent_counts[t] = recent_counts.get(t, 0) + 1

    extended_counts: dict[int, int] = {}
    if extended_tails:
        for t in extended_tails:
            extended_counts[t] = extended_counts.get(t, 0) + 1

    history_applied: dict[int, tuple[float, str]] = {}  # modifier, label
    for pair in list(pair_scores.keys()):
        count_5 = recent_counts.get(pair, 0)
        count_10 = extended_counts.get(pair, 0)

        if count_5 >= 4:
            modifier = HIST_HOT_4_PLUS
            label = f"🔥 Loại (nổ {count_5}/5 tuần)"
        elif count_5 == 3:
            modifier = HIST_HOT_3
            label = f"🔥 Giảm mạnh ({count_5}/5 tuần)"
        elif count_5 == 2:
            modifier = HIST_WARM_2
            label = f"⚡ Giảm vừa ({count_5}/5 tuần)"
        elif count_5 == 1:
            modifier = HIST_NEUTRAL_1
            label = f"● Trung lập ({count_5}/5 tuần)"
        elif count_5 == 0 and count_10 >= 2:
            modifier = HIST_COLD_PRESSURE
            label = f"🧊 Áp suất tích lũy (0/5, {count_10}/10 tuần)"
        elif count_5 == 0 and count_10 == 1:
            modifier = HIST_COLD_MILD
            label = f"❄️ Áp suất nhẹ (0/5, {count_10}/10 tuần)"
        elif count_5 == 0 and count_10 == 0 and extended_tails:
            modifier = HIST_TOXIC_COLD
            label = f"💀 Quá lạnh (0/5, 0/10 tuần)"
        else:
            modifier = HIST_NEUTRAL_1
            label = "● Trung lập"

        pair_scores[pair] *= modifier
        history_applied[pair] = (modifier, label)

        # Remove excluded pairs (modifier = 0.0)
        if modifier == 0.0:
            del pair_scores[pair]

    # ── Step 5: Sort & Pick Top 3 ──
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)
    top_candidates, candidate_log = _build_candidate_shortlist(
        sorted_pairs, pair_vote_count, pair_unique_models, limit=10
    )

    top_pairs = [(pair, round(score, 4)) for pair, score in sorted_pairs[:top_n_output]]
    print(f"     🎯 Top {top_n_output}: {[f'{p:02d}' for p, _ in top_pairs]}")

    # ── Scoring Log cho Telegram ──
    scoring_log = _build_scoring_log(
        top_pairs, pair_model_details, pair_unique_models,
        consensus_applied, history_applied, model_confidences,
    )

    consensus_list = [p for p, models in pair_unique_models.items() if len(models) >= 2]

    return {
        "top_pairs": top_pairs,
        "contributing_models": list(set(contributing)),
        "ensemble_method": "xsmb_precision_v5.0",
        "borda_details": {p: round(s, 4) for p, s in sorted_pairs},
        "consensus_pairs": consensus_list,
        "scoring_log": scoring_log,
        "candidate_log": candidate_log,
        "top_candidates": top_candidates,
        "models_active": len(active_model_names),
        "models_total": TOTAL_MODELS,
    }


def _build_scoring_log(
    top_pairs: list,
    pair_model_details: dict,
    pair_unique_models: dict,
    consensus_applied: dict,
    history_applied: dict,
    model_confidences: dict,
) -> str:
    """Build human-readable scoring breakdown cho Telegram (v5.0)."""
    log_entries = []

    for pair, final_score in top_pairs:
        lines = []
        details = pair_model_details.get(pair, [])

        # Header
        vote_count = len(pair_unique_models.get(pair, set()))
        lines.append(f"🔸 <b>[{pair:02d}]</b> = {final_score:.3f}đ")

        # Model contributions
        base_sum = sum(d["contribution"] for d in details)
        model_parts = []
        for d in sorted(details, key=lambda x: x["contribution"], reverse=True):
            m_name = MODEL_DISPLAY_NAME.get(d["model"], d["model"])
            norm_pct = d["norm_score"] * 100
            model_parts.append(f"{m_name}(T{d['rank']}, {norm_pct:.0f}%)")
        lines.append(f"   ├ Cơ sở: {base_sum:.3f}đ từ {', '.join(model_parts)}")

        # Consensus
        cons_mult = consensus_applied.get(pair, 1.0)
        if cons_mult > 1.0:
            pct_boost = (cons_mult - 1.0) * 100
            lines.append(f"   ├ Đồng thuận: ×{cons_mult:.2f} (+{pct_boost:.0f}%, {vote_count} models)")

        # History
        hist_info = history_applied.get(pair)
        if hist_info:
            modifier, label = hist_info
            if modifier != 1.0:
                lines.append(f"   ├ Lịch sử: ×{modifier:.2f} — {label}")
            else:
                lines.append(f"   ├ Lịch sử: {label}")

        # Model sources
        pair_models = pair_unique_models.get(pair, set())
        model_tags = sorted([MODEL_DISPLAY_NAME.get(m, m) for m in pair_models])
        lines.append(f"   └ Sources: {', '.join(model_tags)}")

        log_entries.append("\n".join(lines))

    return "\n\n".join(log_entries)


# ─── Format Functions (compatible with XSMN ensemble_engine interface) ──────

def format_ensemble_result(
    region: str,
    province: Optional[str],
    ensemble_output: Dict,
    target_date,
) -> Dict:
    """Format ensemble output cho prediction_results table."""
    top = ensemble_output["top_pairs"]

    while len(top) < 3:
        top.append((-1, 0.0))

    return {
        "prediction_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
        "region": region,
        "province": province,
        "pair_1": top[0][0],
        "pair_2": top[1][0],
        "pair_3": top[2][0],
        "prob_1": top[0][1],
        "prob_2": top[1][1],
        "prob_3": top[2][1],
        "model_version": "ensemble_v5.0",
        "ensemble_method": ensemble_output["ensemble_method"],
        "contributing_models": ensemble_output["contributing_models"],
        "final_scores": [s for _, s in top[:3]],
        "hit": None,
        "matched_pairs": None,
        "tail_set": None,
        "scoring_log": ensemble_output.get("scoring_log", ""),
        "candidate_log": ensemble_output.get("candidate_log", ""),
    }


def format_model_prediction_log(
    region: str,
    province: Optional[str],
    model_result: Dict,
    target_date,
) -> Dict:
    """Format sub-model result cho model_predictions table."""
    top = model_result.get("top_pairs", [])
    while len(top) < 5:
        top.append((None, None))

    model_name = model_result.get("model_name", "unknown")
    if model_name in (
        "frequency", "gap_overdue", "markov", "bayesian", "cyclic",
        "stats_freq_gap", "chisquare_gof", "chisquare_independence", "cdm",
    ):
        model_type = "rule_based"
    elif model_name in ("xgboost_core", "lstm"):
        model_type = "ml"
    else:
        model_type = "unknown"

    return {
        "prediction_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
        "region": region,
        "province": province,
        "model_name": model_name,
        "model_type": model_type,
        "pair_1": top[0][0] if top[0][0] is not None else None,
        "pair_2": top[1][0] if top[1][0] is not None else None,
        "pair_3": top[2][0] if top[2][0] is not None else None,
        "pair_4": top[3][0] if top[3][0] is not None else None,
        "pair_5": top[4][0] if top[4][0] is not None else None,
        "score_1": top[0][1] if top[0][1] is not None else None,
        "score_2": top[1][1] if top[1][1] is not None else None,
        "score_3": top[2][1] if top[2][1] is not None else None,
        "score_4": top[3][1] if top[3][1] is not None else None,
        "score_5": top[4][1] if top[4][1] is not None else None,
        "execution_time_ms": model_result.get("execution_time_ms"),
        "error_message": model_result.get("error_message"),
        "status": model_result.get("status", "unknown"),
    }
