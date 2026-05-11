"""
ensemble_engine.py — Weighted Borda Count + CombSUM Aggregation Engine (v3.2)
Kết hợp output từ 5 sub-models thành Top 3 cuối cùng.

Models (v3.2):
  1. frequency      — Pure frequency/hot-cool scoring
  2. gap_overdue    — Pure gap/overdue scoring
  3. markov         — Markov Chain transition
  4. xgboost_core   — XGBoost ML classifier
  5. lstm           — LSTM/GRU sequence model

Scoring config loaded from config/scoring.yaml (falls back to hardcoded defaults).

Aggregation Methods:
  - Weighted Borda Count: mỗi model trả top 5, rank → điểm (5→1), × trọng số
  - CombSUM (optional): chuẩn hóa score min-max trước khi cộng có trọng số
  - Consensus bonus: ≥2 model chọn cùng pair → bonus
  - History adjustment: 3-week same-weekday lookback

Guardrails:
  - 1-2 model lỗi → ensemble vẫn chạy với model còn lại
  - Dynamic consensus threshold scales with model count
"""

import os
from typing import List, Dict, Tuple, Optional


# ─── Load config from YAML (with hardcoded fallback) ───────────────────────
def _load_scoring_config() -> dict:
    """Load scoring config from config/scoring.yaml, fallback to defaults."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "config", "scoring.yaml"
    )
    try:
        import yaml
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    except Exception:
        return {}  # Use hardcoded defaults

_CFG = _load_scoring_config()

# Borda points by rank position (1-indexed)
BORDA_POINTS = {int(k): v for k, v in _CFG.get("borda_points", {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}).items()}

# Default expert weights (v3.2 — 5 models)
_w_cfg = _CFG.get("weights", {})
DEFAULT_WEIGHTS: dict[str, float] = {
    "frequency":    _w_cfg.get("frequency",    0.20),
    "gap_overdue":  _w_cfg.get("gap_overdue",  0.20),
    "markov":       _w_cfg.get("markov",       0.15),
    "xgboost_core": _w_cfg.get("xgboost_core", 0.20),
    "lstm":         _w_cfg.get("lstm",         0.25),
    # v3.1 legacy fallback
    "freq_gap":     _w_cfg.get("freq_gap",     0.20),
}

# Consensus rules
_cons_cfg = _CFG.get("consensus", {})
CONSENSUS_THRESHOLD_GOLD = _cons_cfg.get("gold_threshold", 3)
CONSENSUS_THRESHOLD_SILVER = _cons_cfg.get("silver_threshold", 2)
BONUS_GOLD = _cons_cfg.get("gold_bonus", 5.0)
BONUS_SILVER = _cons_cfg.get("silver_bonus", 2.0)

# History rules
_hist_cfg = _CFG.get("history", {})
HISTORY_PENALTY_OVERDUE = _hist_cfg.get("overdue_penalty", -2.0)
HISTORY_BONUS_SWEETSPOT = _hist_cfg.get("sweetspot_bonus", 2.0)
HISTORY_BONUS_POTENTIAL = _hist_cfg.get("potential_bonus", 1.0)

# CombSUM config
_comb_cfg = _CFG.get("combsum", {})
COMBSUM_ENABLED = _comb_cfg.get("enabled", True)
COMBSUM_METHOD = _comb_cfg.get("method", "minmax")

# Minimum model agreement
MIN_MODELS_AGREE = _CFG.get("min_models_agree", 1)


# ─── Model Name Display Mapping ─────────────────────────────────────────────
MODEL_DISPLAY_NAME = {
    "frequency":    "Freq",
    "gap_overdue":  "Gap",
    "markov":       "Markov",
    "xgboost_core": "XGB",
    "lstm":         "LSTM",
    # legacy
    "freq_gap":     "Freq/Gap",
}


def _normalize_scores_minmax(top_pairs: list) -> list:
    """
    Normalize raw scores to [0, 1] range using min-max.
    Input: [(pair, raw_score), ...]
    Output: [(pair, norm_score), ...]
    """
    if not top_pairs:
        return top_pairs
    scores = [s for _, s in top_pairs]
    s_min = min(scores)
    s_max = max(scores)
    rng = s_max - s_min
    if rng < 1e-10:
        return [(p, 1.0) for p, _ in top_pairs]
    return [(p, (s - s_min) / rng) for p, s in top_pairs]


def compute_global_borda(
    model_results: List[Dict],
    recent_tails: List[int],
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
) -> Dict:
    """
    Tính Global Weighted Borda Count + CombSUM từ tất cả model results.
    Kết hợp thuật toán phân tích lịch sử 3 kỳ quay gần nhất.

    Aggregation formula (CombSUM mode):
        FinalScore(n) = Σ w_m · s_m_normalized(n)  +  consensus_bonus  +  history_adj

    Args:
        model_results: List of dicts từ nhiều province
        recent_tails: List các 2 số cuối (tails) xuất hiện trong 3 kỳ gần nhất
        weights: dict model_name -> weight
        top_n_output: số cặp cuối cùng output (default 3)

    Returns:
        Dict chứa top 3 global pairs và scoring log.
    """
    w = weights if weights else DEFAULT_WEIGHTS

    # Filter thành công
    valid_results = [r for r in model_results if r.get("status") == "success" and r.get("top_pairs")]

    if not valid_results:
        return {
            "top_pairs": [],
            "contributing_models": [],
            "ensemble_method": "weighted_borda",
            "borda_details": {},
            "consensus_pairs": [],
        }

    # ── CombSUM Note ──
    # Models đã normalize output về [0,1] bên trong (min-max across 100 pairs).
    # KHÔNG re-normalize ở đây — tránh double normalization làm rank 5 bị zero-out.

    # ── Tính Borda scores ──
    pair_scores: dict[int, float] = {}
    pair_model_count: dict[int, int] = {}  # đếm tổng model results chọn pair
    pair_unique_models: dict[int, set] = {}  # đếm UNIQUE model names per pair
    pair_model_names: dict[int, list] = {}  # track model names per pair

    contributing = []

    for result in valid_results:
        model_name = result["model_name"]
        prov = result.get("province", "unknown")
        weight = w.get(model_name, 0.15)  # fallback weight
        contributing.append(f"{model_name}_{prov}")

        for rank_idx, (pair, raw_score) in enumerate(result["top_pairs"]):
            rank = rank_idx + 1  # 1-indexed
            borda_pts = BORDA_POINTS.get(rank, 0)

            if borda_pts > 0:
                if COMBSUM_ENABLED:
                    # Pure CombSUM: weight × normalized_score
                    # Models đã output [0,1], không nhân thêm borda_pts (tránh double-count ranking)
                    weighted_pts = weight * raw_score
                else:
                    # Pure Borda: rank-based only
                    weighted_pts = weight * borda_pts

                pair_scores[pair] = pair_scores.get(pair, 0) + weighted_pts
                pair_model_count[pair] = pair_model_count.get(pair, 0) + 1
                if pair not in pair_unique_models:
                    pair_unique_models[pair] = set()
                pair_unique_models[pair].add(model_name)
                if pair not in pair_model_names:
                    pair_model_names[pair] = []
                pair_model_names[pair].append(model_name)

    # ── Minimum Model Agreement Filter ──
    if MIN_MODELS_AGREE > 1:
        pair_scores = {
            p: s for p, s in pair_scores.items()
            if pair_model_count.get(p, 0) >= MIN_MODELS_AGREE
        }

    # ── Consensus bonus (đếm theo UNIQUE model names, tránh inflate cross-province) ──
    for pair in list(pair_scores.keys()):
        unique_count = len(pair_unique_models.get(pair, set()))
        if unique_count >= CONSENSUS_THRESHOLD_GOLD:
            pair_scores[pair] += BONUS_GOLD
        elif unique_count >= CONSENSUS_THRESHOLD_SILVER:
            pair_scores[pair] += BONUS_SILVER

    # ── Thuật toán kết hợp lịch sử 3 kỳ gần nhất CÙNG THỨ ──
    recent_counts = {}
    for t in recent_tails:
        recent_counts[t] = recent_counts.get(t, 0) + 1

    for pair in pair_scores:
        count = recent_counts.get(pair, 0)
        if count >= 2:
            # Nổ >= 2 lần trong 3 tuần cùng thứ -> Phạt
            pair_scores[pair] += HISTORY_PENALTY_OVERDUE
        elif count == 1:
            # Nổ đúng 1 lần -> Rơi đúng nhịp chuẩn -> Thưởng
            pair_scores[pair] += HISTORY_BONUS_SWEETSPOT
        else:
            # Chưa nổ -> Tiềm năng (Gan ngắn) -> Thưởng
            pair_scores[pair] += HISTORY_BONUS_POTENTIAL

    # ── Sort & pick top N ──
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)
    top_pairs = [(pair, round(score, 4)) for pair, score in sorted_pairs[:top_n_output]]

    # ── Tạo Telegram Log cho Top 3 ──
    scoring_log = ['📝 <b>CHI TIẾT CHẤM ĐIỂM (EXPERT SCORING v3.2 — 5 Models):</b>']
    for pair, score in top_pairs:
        log_lines = []
        # 1. Base Score
        base_points = 0
        models_hit = []
        for result in valid_results:
            for r_idx, (p, raw_s) in enumerate(result.get('top_pairs', [])):
                if p == pair:
                    wt = w.get(result['model_name'], 0.15)
                    if COMBSUM_ENABLED:
                        pts = wt * raw_s
                    else:
                        pts = BORDA_POINTS.get(r_idx + 1, 0) * wt
                    base_points += pts
                    m_name = MODEL_DISPLAY_NAME.get(result['model_name'], result['model_name'])
                    models_hit.append(f"{m_name}(Top{r_idx+1})")

        log_lines.append(f"🔸 <b>[{pair:02d}]</b> = {score:.2f}đ")
        log_lines.append(f"   ├ Cơ sở: {base_points:.2f}đ từ {', '.join(models_hit)}")

        # 2. Consensus Bonus (unique models)
        c_unique = len(pair_unique_models.get(pair, set()))
        if c_unique >= CONSENSUS_THRESHOLD_GOLD:
            log_lines.append(f"   ├ Đồng thuận: +{BONUS_GOLD}đ ({c_unique} model đồng ý)")
        elif c_unique >= CONSENSUS_THRESHOLD_SILVER:
            log_lines.append(f"   ├ Đồng thuận: +{BONUS_SILVER}đ ({c_unique} model đồng ý)")

        # 3. History Bonus
        h_count = recent_counts.get(pair, 0)
        if h_count >= 2:
            log_lines.append(f"   └ Lịch sử: {HISTORY_PENALTY_OVERDUE}đ (Nổ {h_count} lần/3 tuần)")
        elif h_count == 1:
            log_lines.append(f"   └ Lịch sử: +{HISTORY_BONUS_SWEETSPOT}đ (Nổ đúng 1 nhịp/3 tuần)")
        else:
            log_lines.append(f"   └ Lịch sử: +{HISTORY_BONUS_POTENTIAL}đ (Đang nén 3 tuần chưa ra)")

        scoring_log.append('\n'.join(log_lines))

    consensus_pairs_list = [p for p, models in pair_unique_models.items() if len(models) >= CONSENSUS_THRESHOLD_SILVER]

    return {
        'top_pairs': top_pairs,
        'contributing_models': list(set(contributing)),
        'ensemble_method': 'expert_borda_combsum_v3.2',
        'borda_details': {p: round(s, 4) for p, s in sorted_pairs},
        'consensus_pairs': consensus_pairs_list,
        'scoring_log': '\n\n'.join(scoring_log)
    }


def format_ensemble_result(
    region: str,
    province: Optional[str],
    ensemble_output: Dict,
    target_date,
) -> Dict:
    """
    Format ensemble output thành dict sẵn sàng insert vào prediction_results.

    Returns:
        Dict ready for supabase.table('prediction_results').upsert(...)
    """
    top = ensemble_output["top_pairs"]

    if len(top) < 3:
        # Pad with -1 if not enough results
        while len(top) < 3:
            top.append((-1, 0.0))

    return {
        "prediction_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
        "region": region,
        "province": province,  # Có thể là None cho Global
        "pair_1": top[0][0],
        "pair_2": top[1][0],
        "pair_3": top[2][0],
        "prob_1": top[0][1],
        "prob_2": top[1][1],
        "prob_3": top[2][1],
        "model_version": "ensemble_v3.2",
        "ensemble_method": ensemble_output["ensemble_method"],
        "contributing_models": ensemble_output["contributing_models"],
        "final_scores": [s for _, s in top[:3]],
        "hit": None,
        "matched_pairs": None,
        "tail_set": None,
        "scoring_log": ensemble_output.get('scoring_log', '')
    }


def format_model_prediction_log(
    region: str,
    province: Optional[str],
    model_result: Dict,
    target_date,
) -> Dict:
    """
    Format sub-model result thành dict cho model_predictions table.
    """
    top = model_result.get("top_pairs", [])

    # Pad to 5
    while len(top) < 5:
        top.append((None, None))

    # Determine model type
    model_name = model_result.get("model_name", "unknown")
    if model_name in ("frequency", "gap_overdue", "markov", "freq_gap"):
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
