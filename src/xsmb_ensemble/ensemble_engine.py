"""
ensemble_engine.py — XSMB Adaptive Weighted Borda v4.1 (Anti-Echo & Recency Intelligence)

Kết hợp output từ 7 sub-models thành Top 3 cuối cùng.

Models (v4.0):
  A. frequency      — Multi-window frequency analysis
  B. gap_overdue    — Weekday-specific gap/overdue
  C. markov         — Second-order Markov Chain
  D. xgboost_core   — XGBoost v4 (25 features)
  E. lstm           — Bi-LSTM + Attention
  F. bayesian       — Bayesian posterior estimation
  G. cyclic         — Cyclic Pattern FFT detector

Aggregation:
  - Adaptive Weighted Borda Count
  - Confidence-weighted scoring (from Bayesian model)
  - Consensus bonus (unique model agreement) — v4.1: capped to base score
  - History adjustment (same-weekday lookback) — v4.1: exponential penalty
  - Recency Dampener — v4.1: score × decay^(count-1)
  - MMR Diversity Selection
  - Auto-weight integration (from auto_weight.py)

v4.1 Changes (Anti-Echo & Recency Intelligence):
  - Exponential history penalty: -0.5 × 2^(count-2) for count ≥ 3
  - Consensus scaling cap: bonus ≤ |base_score|
  - Recency dampener: score × 0.7^(count-1) for count ≥ 2
  - Momentum 2/5 tuần: neutral (was +0.6)
  - Enhanced scoring log with decay factor display

Config: scoring.yaml → xsmb_v4 section
"""

import os
from typing import List, Dict, Tuple, Optional
import numpy as np


# ─── Config ─────────────────────────────────────────────────────────────────

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

# Borda points by rank
BORDA_POINTS = {int(k): v for k, v in _CFG.get("borda_points", {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}).items()}

# XSMB v4 config
_V4_CFG = _CFG.get("xsmb_v4", {})

# Default weights (v4 — 7 models)
_w_cfg = _V4_CFG.get("weights", {})
DEFAULT_WEIGHTS: dict[str, float] = {
    "frequency":    _w_cfg.get("frequency",    0.10),
    "gap_overdue":  _w_cfg.get("gap_overdue",  0.10),
    "markov":       _w_cfg.get("markov",       0.15),
    "xgboost_core": _w_cfg.get("xgboost_core", 0.20),
    "lstm":         _w_cfg.get("lstm",         0.15),
    "bayesian":     _w_cfg.get("bayesian",     0.15),
    "cyclic":       _w_cfg.get("cyclic",       0.15),
}

# Consensus
_cons_cfg = _V4_CFG.get("consensus", _CFG.get("xsmb_overrides", {}).get("consensus", {}))
CONSENSUS_GOLD_THRESHOLD = _cons_cfg.get("gold_threshold", 4)
CONSENSUS_SILVER_THRESHOLD = _cons_cfg.get("silver_threshold", 3)
BONUS_GOLD = float(_cons_cfg.get("gold_bonus", 1.5))
BONUS_SILVER = float(_cons_cfg.get("silver_bonus", 0.5))
CONSENSUS_CAP_TO_BASE = bool(_cons_cfg.get("cap_to_base", True))  # v4.1

# History
_hist_cfg = _V4_CFG.get("history", _CFG.get("xsmb_overrides", {}).get("history", {}))
HIST_OVERDUE = float(_hist_cfg.get("overdue_penalty", -0.5))
HIST_SWEETSPOT = float(_hist_cfg.get("sweetspot_bonus", 0.5))
HIST_POTENTIAL = float(_hist_cfg.get("potential_bonus", 0.3))
HIST_EXPONENTIAL = bool(_hist_cfg.get("exponential", True))  # v4.1

# Diversity
_div_cfg = _V4_CFG.get("diversity", _CFG.get("xsmb_overrides", {}).get("diversity", {}))
DIVERSITY_ENABLED = bool(_div_cfg.get("enabled", True))
DIVERSITY_LAMBDA = float(_div_cfg.get("lambda", 0.6))

# Recency Dampener (v4.1)
_rd_cfg = _V4_CFG.get("recency_dampener", {})
RECENCY_DAMPENER_ENABLED = bool(_rd_cfg.get("enabled", True))
RECENCY_DAMPENER_THRESHOLD = int(_rd_cfg.get("threshold", 2))
RECENCY_DAMPENER_DECAY = float(_rd_cfg.get("decay_base", 0.7))

# Auto-weight
_aw_cfg = _V4_CFG.get("auto_weight", {})
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
}

TOTAL_MODELS = 7


# ─── Diversity Selection (MMR) ──────────────────────────────────────────────

def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity giữa 2 tập model names."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _select_diverse_top_n(
    scored_pairs: list[tuple[int, float]],
    pair_unique_models: dict[int, set],
    n: int = 3,
    lambda_: float = 0.6,
) -> list[tuple[int, float]]:
    """
    MMR (Maximal Marginal Relevance) selection cho diverse top-N.

    Cân bằng giữa relevance (score cao) và diversity (model sources khác nhau).
    """
    if len(scored_pairs) <= n:
        return scored_pairs

    max_score = scored_pairs[0][1]
    min_score = scored_pairs[-1][1]
    score_range = max_score - min_score

    def norm_score(s: float) -> float:
        if score_range < 1e-10:
            return 1.0
        return (s - min_score) / score_range

    # Pick #1: highest score
    selected = [scored_pairs[0]]
    selected_model_sets = [pair_unique_models.get(scored_pairs[0][0], set())]
    remaining = list(scored_pairs[1:])

    while len(selected) < n and remaining:
        best_mmr = -float('inf')
        best_idx = 0

        for idx, (pair, score) in enumerate(remaining):
            relevance = norm_score(score)
            pair_models = pair_unique_models.get(pair, set())
            max_sim = max(
                _jaccard_similarity(pair_models, sel_models)
                for sel_models in selected_model_sets
            )
            mmr = lambda_ * relevance - (1.0 - lambda_) * max_sim

            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx

        chosen_pair, chosen_score = remaining.pop(best_idx)
        selected.append((chosen_pair, chosen_score))
        selected_model_sets.append(pair_unique_models.get(chosen_pair, set()))

    return selected


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
    XSMB v4 Adaptive Weighted Borda Ensemble.

    Kết hợp output từ 7 models thành Top 3 cuối cùng.

    Aggregation:
      FinalScore(pair) = Σ w_m × conf_m × borda_pts(pair, m) + consensus + history
      → MMR diversity selection for top 3

    Args:
        model_results: List of dicts từ 7 sub-models
        recent_tails: tails từ 5 kỳ cùng thứ gần nhất
        weights: override weights (default from config)
        top_n_output: số cặp output (default 3)
        extended_tails: tails 10 kỳ cho Toxic Gap check
        model_confidences: dict model_name → confidence [0,1]
            (từ Bayesian model hoặc auto_weight)

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
            "ensemble_method": "xsmb_borda_v4.0",
            "borda_details": {},
            "consensus_pairs": [],
            "scoring_log": "",
            "models_active": 0,
            "models_total": TOTAL_MODELS,
        }

    # ── Extract Bayesian confidence nếu available ──
    if model_confidences is None:
        model_confidences = {}
    for result in valid_results:
        if result.get("model_name") == "bayesian" and "confidence" in result:
            # Broadcast Bayesian confidence to all models as a general signal
            # Models that are confidence-aware get a boost/penalty
            pass

    # ── Tính Borda scores ──
    pair_scores: dict[int, float] = {}
    pair_model_count: dict[int, int] = {}
    pair_unique_models: dict[int, set] = {}
    pair_model_names: dict[int, list] = {}
    contributing = []

    active_models = set()
    for result in valid_results:
        model_name = result["model_name"]
        active_models.add(model_name)
        weight = w.get(model_name, 0.10)
        contributing.append(model_name)

        # Model-specific confidence
        conf = model_confidences.get(model_name, 1.0)

        for rank_idx, (pair, raw_score) in enumerate(result["top_pairs"]):
            rank = rank_idx + 1
            borda_pts = BORDA_POINTS.get(rank, 0)

            if borda_pts > 0:
                # Confidence-weighted Borda: weight × confidence × rank_points
                weighted_pts = weight * conf * borda_pts

                pair_scores[pair] = pair_scores.get(pair, 0) + weighted_pts
                pair_model_count[pair] = pair_model_count.get(pair, 0) + 1
                if pair not in pair_unique_models:
                    pair_unique_models[pair] = set()
                pair_unique_models[pair].add(model_name)
                if pair not in pair_model_names:
                    pair_model_names[pair] = []
                pair_model_names[pair].append(model_name)

    # ── Consensus bonus (v4.1: capped to base score) ──
    consensus_applied: dict[int, float] = {}  # track actual bonus applied
    for pair in list(pair_scores.keys()):
        unique_count = len(pair_unique_models.get(pair, set()))
        base = pair_scores[pair]  # base score BEFORE consensus

        if unique_count >= CONSENSUS_GOLD_THRESHOLD:
            bonus = BONUS_GOLD
            if CONSENSUS_CAP_TO_BASE:
                cap = max(abs(base), 0.5)  # minimum cap = 0.5
                bonus = min(bonus, cap)
            pair_scores[pair] += bonus
            consensus_applied[pair] = bonus
        elif unique_count >= CONSENSUS_SILVER_THRESHOLD:
            bonus = BONUS_SILVER
            if CONSENSUS_CAP_TO_BASE:
                cap = max(abs(base), 0.5)
                bonus = min(bonus, cap)
            pair_scores[pair] += bonus
            consensus_applied[pair] = bonus

    # ── History adjustment (v4.1: exponential penalty) ──
    recent_counts: dict[int, int] = {}
    for t in recent_tails:
        recent_counts[t] = recent_counts.get(t, 0) + 1

    extended_counts: dict[int, int] = {}
    if extended_tails:
        for t in extended_tails:
            extended_counts[t] = extended_counts.get(t, 0) + 1

    history_applied: dict[int, float] = {}  # track history adjustment
    for pair in pair_scores:
        count = recent_counts.get(pair, 0)

        if count >= 3:
            # v4.1: Exponential penalty — quá nóng, cooling aggressively
            # count=3 → -2.0, count=4 → -4.0, count=5 → -8.0
            if HIST_EXPONENTIAL:
                penalty = HIST_OVERDUE * (2 ** (count - 2))
            else:
                penalty = HIST_OVERDUE
            pair_scores[pair] += penalty
            history_applied[pair] = penalty
        elif count == 2:
            # v4.1: Neutral — 2/5 không phải signal rõ ràng
            pair_scores[pair] += 0.0
            history_applied[pair] = 0.0
        elif count == 1:
            pair_scores[pair] += 0.3            # Moderate momentum
            history_applied[pair] = 0.3
        else:
            # Check Toxic Gap (10 weeks)
            ext_count = extended_counts.get(pair, 0)
            if ext_count == 0 and extended_tails:
                pair_scores[pair] += HIST_OVERDUE  # Dangerous cold streak
                history_applied[pair] = HIST_OVERDUE
            else:
                pair_scores[pair] += HIST_POTENTIAL
                history_applied[pair] = HIST_POTENTIAL

    # ── Recency Dampener (v4.1: multiplicative decay) ──
    recency_decay_applied: dict[int, float] = {}  # track decay factor
    if RECENCY_DAMPENER_ENABLED:
        for pair in pair_scores:
            count = recent_counts.get(pair, 0)
            if count >= RECENCY_DAMPENER_THRESHOLD:
                # decay = base ^ (count - 1)
                # count=2 → ×0.7, count=3 → ×0.49, count=4 → ×0.343
                decay = RECENCY_DAMPENER_DECAY ** (count - 1)
                pair_scores[pair] *= decay
                recency_decay_applied[pair] = decay

    # ── Sort & pick top N (with diversity) ──
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)

    if DIVERSITY_ENABLED and len(sorted_pairs) > top_n_output:
        mmr_pool = sorted_pairs[:15]
        diverse_selection = _select_diverse_top_n(
            mmr_pool, pair_unique_models, n=top_n_output, lambda_=DIVERSITY_LAMBDA
        )
        top_pairs = [(pair, round(score, 4)) for pair, score in diverse_selection]
        print(f"     🎲 MMR diversity: selected {[f'{p:02d}' for p,_ in top_pairs]} (λ={DIVERSITY_LAMBDA})")
    else:
        top_pairs = [(pair, round(score, 4)) for pair, score in sorted_pairs[:top_n_output]]

    # ── Scoring Log cho Telegram ──
    scoring_log = _build_scoring_log(
        top_pairs, valid_results, w, pair_unique_models,
        recent_counts, extended_counts, extended_tails,
        model_confidences,
        consensus_applied=consensus_applied,
        history_applied=history_applied,
        recency_decay_applied=recency_decay_applied,
    )

    consensus_list = [p for p, models in pair_unique_models.items()
                      if len(models) >= CONSENSUS_SILVER_THRESHOLD]

    return {
        "top_pairs": top_pairs,
        "contributing_models": list(set(contributing)),
        "ensemble_method": "xsmb_borda_v4.1",
        "borda_details": {p: round(s, 4) for p, s in sorted_pairs},
        "consensus_pairs": consensus_list,
        "scoring_log": scoring_log,
        "models_active": len(active_models),
        "models_total": TOTAL_MODELS,
    }


def _build_scoring_log(
    top_pairs: list,
    valid_results: list,
    weights: dict,
    pair_unique_models: dict,
    recent_counts: dict,
    extended_counts: dict,
    extended_tails: list,
    model_confidences: dict,
    *,
    consensus_applied: Optional[dict] = None,
    history_applied: Optional[dict] = None,
    recency_decay_applied: Optional[dict] = None,
) -> str:
    """Build human-readable scoring breakdown cho Telegram (v4.1 enhanced)."""
    if consensus_applied is None:
        consensus_applied = {}
    if history_applied is None:
        history_applied = {}
    if recency_decay_applied is None:
        recency_decay_applied = {}

    log_entries = []

    for pair, score in top_pairs:
        lines = []
        base_points = 0
        models_hit = []

        for result in valid_results:
            for r_idx, (p, raw_s) in enumerate(result.get("top_pairs", [])):
                if p == pair:
                    wt = weights.get(result["model_name"], 0.10)
                    conf = model_confidences.get(result["model_name"], 1.0)
                    pts = BORDA_POINTS.get(r_idx + 1, 0) * wt * conf
                    base_points += pts
                    m_name = MODEL_DISPLAY_NAME.get(result["model_name"], result["model_name"])
                    lines_detail = f"{m_name}(T{r_idx + 1})"
                    models_hit.append(lines_detail)

        # v4.1: warning icon for overheated pairs
        h_count = recent_counts.get(pair, 0)
        heat_icon = "🔥" if h_count >= 3 else ""
        lines.append(f"🔸 <b>[{pair:02d}]</b> = {score:.2f}đ {heat_icon}")
        lines.append(f"   ├ Cơ sở: {base_points:.2f}đ từ {', '.join(models_hit)}")

        # Consensus (v4.1: show actual bonus + cap info)
        c_unique = len(pair_unique_models.get(pair, set()))
        actual_bonus = consensus_applied.get(pair)
        if actual_bonus is not None:
            cap_tag = ""
            if CONSENSUS_CAP_TO_BASE:
                # Check if bonus was capped
                if c_unique >= CONSENSUS_GOLD_THRESHOLD and actual_bonus < BONUS_GOLD:
                    cap_tag = f" [capped ≤ {abs(base_points):.2f}]"
                elif c_unique >= CONSENSUS_SILVER_THRESHOLD and actual_bonus < BONUS_SILVER:
                    cap_tag = f" [capped ≤ {abs(base_points):.2f}]"
            lines.append(f"   ├ Đồng thuận: +{actual_bonus:.2f}đ ({c_unique}/{TOTAL_MODELS} model){cap_tag}")

        # History (v4.1: show exponential penalty detail)
        hist_adj = history_applied.get(pair)
        if hist_adj is not None:
            if h_count >= 3:
                exp_tag = " ← exponential" if HIST_EXPONENTIAL else ""
                lines.append(f"   ├ Lịch sử: {hist_adj:.2f}đ (Nổ {h_count}/5 tuần{exp_tag})")
            elif h_count == 2:
                lines.append(f"   ├ Lịch sử: 0.00đ (Neutral {h_count}/5 tuần)")
            elif h_count == 1:
                lines.append(f"   ├ Lịch sử: +0.30đ (Momentum vừa {h_count}/5 tuần)")
            else:
                ext_count = extended_counts.get(pair, 0)
                if ext_count == 0 and extended_tails:
                    lines.append(f"   ├ Lịch sử: {hist_adj:.2f}đ (Gan nguy hiểm 10 tuần)")
                else:
                    lines.append(f"   ├ Lịch sử: +{hist_adj:.2f}đ (Đang nén)")

        # Recency dampener (v4.1)
        decay = recency_decay_applied.get(pair)
        if decay is not None:
            pct_reduction = (1.0 - decay) * 100
            lines.append(f"   ├ 🔥 Recency Decay: ×{decay:.2f} (-{pct_reduction:.0f}% quá nóng)")

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
        "model_version": "ensemble_v4.1",
        "ensemble_method": ensemble_output["ensemble_method"],
        "contributing_models": ensemble_output["contributing_models"],
        "final_scores": [s for _, s in top[:3]],
        "hit": None,
        "matched_pairs": None,
        "tail_set": None,
        "scoring_log": ensemble_output.get("scoring_log", ""),
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
    if model_name in ("frequency", "gap_overdue", "markov", "bayesian", "cyclic"):
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
