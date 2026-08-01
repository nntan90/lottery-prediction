"""
ensemble_engine.py — Weighted Borda Count + CombSUM Aggregation Engine (v3.5)
Kết hợp output từ 6 sub-models thành Top 3 cuối cùng.

Models (v3.5):
  1. frequency      — Pure frequency/hot-cool scoring
  2. gap_overdue    — Pure gap/overdue scoring
  3. markov         — Markov Chain transition
  4. xgboost_core   — XGBoost ML classifier
  5. lstm           — LSTM/GRU sequence model
  6. cdm            — Dirichlet-Multinomial posterior scorer

Scoring config loaded from config/scoring.yaml (falls back to hardcoded defaults).
XSMB-specific overrides: xsmb_overrides section in scoring.yaml.

v3.3 Changes (XSMB-specific):
  - Reduced consensus bonus (5.0 → 1.5) — bonus không vượt base score
  - Rebalanced weights: Freq+Gap giảm (trùng features XGBoost), Markov/XGB/LSTM tăng
  - Pure Borda mode (CombSUM off) — preserve rank diversity
  - MMR Diversity Enforcement — top 3 picks anti-cluster
  - Reduced history adjustment — giảm gambler's fallacy bias

Aggregation Methods:
  - Weighted Borda Count: mỗi model trả top 5, rank → điểm (5→1), × trọng số
  - CombSUM (optional, disabled for XSMB): chuẩn hóa score min-max
  - Consensus bonus: region-specific thresholds
  - History adjustment: 3-week same-weekday lookback
  - Diversity (XSMB): MMR selection to prevent clustering

Guardrails:
  - 1-2 model lỗi → ensemble vẫn chạy với model còn lại
  - XSMN: six-model merged scope with backward-compatible storage contracts
"""

import os
from itertools import combinations
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
_DEFAULT_BORDA_POINTS = {
    1: 5, 2: 4, 3: 3, 4: 2, 5: 1,
    6: 0.5, 7: 0.4, 8: 0.3, 9: 0.2, 10: 0.1,
}
BORDA_POINTS = {int(k): v for k, v in _CFG.get("borda_points", _DEFAULT_BORDA_POINTS).items()}

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
    # v3.3: CDM model
    "cdm":          _w_cfg.get("cdm",          0.13),
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
_XSMN_COMBO_CFG = _CFG.get("xsmn_overrides", {}).get("combo_selector", {})
COMBO_HISTORY_BONUS_WEIGHT = float(_XSMN_COMBO_CFG.get("history_bonus_weight", 0.30))
COMBO_HISTORY_PRIOR_DRAWS = int(_XSMN_COMBO_CFG.get("history_prior_draws", 30))


# ─── XSMB-Specific Config Resolver (v3.3) ──────────────────────────────────

def _get_region_config(region: Optional[str] = None) -> dict:
    """
    Trả về scoring config cho region cụ thể.
    XSMB: dùng xsmb_overrides (nếu có), fallback global.
    XSMN/None: dùng global config (backward compatible).

    Returns:
        dict với keys: weights, consensus_gold_threshold, consensus_silver_threshold,
        bonus_gold, bonus_silver, history_overdue, history_sweetspot,
        history_potential, combsum_enabled, diversity_enabled, diversity_lambda
    """
    # Global defaults (= XSMN behavior)
    cfg = {
        "weights": DEFAULT_WEIGHTS.copy(),
        "consensus_gold_threshold": CONSENSUS_THRESHOLD_GOLD,
        "consensus_silver_threshold": CONSENSUS_THRESHOLD_SILVER,
        "bonus_gold": BONUS_GOLD,
        "bonus_silver": BONUS_SILVER,
        "history_overdue": HISTORY_PENALTY_OVERDUE,
        "history_sweetspot": HISTORY_BONUS_SWEETSPOT,
        "history_potential": HISTORY_BONUS_POTENTIAL,
        "combsum_enabled": COMBSUM_ENABLED,
        "diversity_enabled": False,
        "diversity_lambda": 0.6,
        "cap_to_base": False,
        "recency_dampener_enabled": False,
        "recency_dampener_threshold": 2,
        "recency_dampener_decay": 0.7,
        "recent_repeat_penalty_enabled": False,
        "recent_repeat_penalty": -0.25,
        "max_pairs_per_source": None,
        "require_distinct_unit_digits": False,
    }

    if region and region.upper() == "XSMB":
        xsmb = _CFG.get("xsmb_overrides", {})
        if not xsmb:
            return cfg

        # Weights override
        xsmb_w = xsmb.get("weights", {})
        if xsmb_w:
            # Start from default, override with XSMB values
            w = DEFAULT_WEIGHTS.copy()
            for k, v in xsmb_w.items():
                w[k] = float(v)
            cfg["weights"] = w

        # Consensus override
        xsmb_cons = xsmb.get("consensus", {})
        if xsmb_cons:
            cfg["consensus_gold_threshold"] = xsmb_cons.get("gold_threshold", cfg["consensus_gold_threshold"])
            cfg["consensus_silver_threshold"] = xsmb_cons.get("silver_threshold", cfg["consensus_silver_threshold"])
            cfg["bonus_gold"] = float(xsmb_cons.get("gold_bonus", cfg["bonus_gold"]))
            cfg["bonus_silver"] = float(xsmb_cons.get("silver_bonus", cfg["bonus_silver"]))
            cfg["cap_to_base"] = bool(xsmb_cons.get("cap_to_base", cfg["cap_to_base"]))

        # History override
        xsmb_hist = xsmb.get("history", {})
        if xsmb_hist:
            cfg["history_overdue"] = float(xsmb_hist.get("overdue_penalty", cfg["history_overdue"]))
            cfg["history_sweetspot"] = float(xsmb_hist.get("sweetspot_bonus", cfg["history_sweetspot"]))
            cfg["history_potential"] = float(xsmb_hist.get("potential_bonus", cfg["history_potential"]))

        # CombSUM override
        xsmb_comb = xsmb.get("combsum", {})
        if xsmb_comb:
            cfg["combsum_enabled"] = bool(xsmb_comb.get("enabled", cfg["combsum_enabled"]))

        # Diversity override
        xsmb_div = xsmb.get("diversity", {})
        if xsmb_div:
            cfg["diversity_enabled"] = bool(xsmb_div.get("enabled", False))
            cfg["diversity_lambda"] = float(xsmb_div.get("lambda", 0.6))
            
        # Recency Dampener
        xsmb_rd = xsmb.get("recency_dampener", {})
        if xsmb_rd:
            cfg["recency_dampener_enabled"] = bool(xsmb_rd.get("enabled", False))
            cfg["recency_dampener_threshold"] = int(xsmb_rd.get("threshold", 2))
            cfg["recency_dampener_decay"] = float(xsmb_rd.get("decay_base", 0.7))

    elif region and region.upper() == "XSMN":
        xsmn = _CFG.get("xsmn_overrides", {})
        if not xsmn:
            return cfg

        # Weights override
        xsmn_w = xsmn.get("weights", {})
        if xsmn_w:
            w = DEFAULT_WEIGHTS.copy()
            for k, v in xsmn_w.items():
                w[k] = float(v)
            cfg["weights"] = w

        # Consensus override
        xsmn_cons = xsmn.get("consensus", {})
        if xsmn_cons:
            cfg["consensus_gold_threshold"] = xsmn_cons.get("gold_threshold", cfg["consensus_gold_threshold"])
            cfg["consensus_silver_threshold"] = xsmn_cons.get("silver_threshold", cfg["consensus_silver_threshold"])
            cfg["bonus_gold"] = float(xsmn_cons.get("gold_bonus", cfg["bonus_gold"]))
            cfg["bonus_silver"] = float(xsmn_cons.get("silver_bonus", cfg["bonus_silver"]))
            cfg["cap_to_base"] = bool(xsmn_cons.get("cap_to_base", cfg["cap_to_base"]))

        # History override
        xsmn_hist = xsmn.get("history", {})
        if xsmn_hist:
            cfg["history_overdue"] = float(xsmn_hist.get("overdue_penalty", cfg["history_overdue"]))
            cfg["history_sweetspot"] = float(xsmn_hist.get("sweetspot_bonus", cfg["history_sweetspot"]))
            cfg["history_potential"] = float(xsmn_hist.get("potential_bonus", cfg["history_potential"]))

        # CombSUM override
        xsmn_comb = xsmn.get("combsum", {})
        if xsmn_comb:
            cfg["combsum_enabled"] = bool(xsmn_comb.get("enabled", cfg["combsum_enabled"]))

        # Diversity override
        xsmn_div = xsmn.get("diversity", {})
        if xsmn_div:
            cfg["diversity_enabled"] = bool(xsmn_div.get("enabled", False))
            cfg["diversity_lambda"] = float(xsmn_div.get("lambda", 0.6))
            
        # Recency Dampener
        xsmn_rd = xsmn.get("recency_dampener", {})
        if xsmn_rd:
            cfg["recency_dampener_enabled"] = bool(xsmn_rd.get("enabled", False))
            cfg["recency_dampener_threshold"] = int(xsmn_rd.get("threshold", 2))
            cfg["recency_dampener_decay"] = float(xsmn_rd.get("decay_base", 0.7))

        xsmn_rr = xsmn.get("recent_repeat_penalty", {})
        if xsmn_rr:
            cfg["recent_repeat_penalty_enabled"] = bool(xsmn_rr.get("enabled", False))
            cfg["recent_repeat_penalty"] = float(xsmn_rr.get("penalty", cfg["recent_repeat_penalty"]))

        xsmn_combo = xsmn.get("combo_selector", {})
        if xsmn_combo:
            max_pairs = xsmn_combo.get("max_pairs_per_source")
            cfg["max_pairs_per_source"] = max(1, int(max_pairs)) if max_pairs is not None else None
            cfg["require_distinct_unit_digits"] = bool(
                xsmn_combo.get("require_distinct_unit_digits", False)
            )

    return cfg



# ─── Unit Digit Diversity Selection (v3.4) ──────────────────────────────────

def _select_unit_digit_diversity(
    all_scored_pairs: list[tuple[int, float]],
    n: int = 3,
    pool_size: int = 10,
) -> tuple[list[tuple[int, float]], str]:
    """
    Unit Digit (Hàng đơn vị) Diversity Selection.
    
    1. Gom tổng điểm của tất cả các cặp số theo hàng đơn vị (0-9).
    2. Xếp hạng các hàng đơn vị từ điểm cao nhất đến thấp nhất.
    3. Tìm trong Top `pool_size` (mặc định Top 10) các cặp số tốt nhất thoả mãn:
       - Mỗi cặp số được chọn phải có hàng đơn vị ứng với các hàng đơn vị top đầu.
       - Không chọn trùng hàng đơn vị.
    4. Nếu Top 10 không đủ các hàng đơn vị khác nhau để lấy đủ N số, lấy thêm các số điểm cao nhất còn lại trong Top 10.
    
    Args:
        all_scored_pairs: List of (pair, score) đã được sort theo score giảm dần
        n: Số lượng cặp số cần chọn (mặc định 3)
        pool_size: Kích thước danh sách ứng viên (mặc định 10)
        
    Returns:
        diverse_selection: List of (pair, score)
        log_message: Thông báo log để in ra Telegram
    """
    # 1. Tính điểm cho từng hàng đơn vị (0-9)
    unit_digit_scores = {d: 0.0 for d in range(10)}
    for pair, score in all_scored_pairs:
        unit = pair % 10
        unit_digit_scores[unit] += score
        
    # 2. Xếp hạng hàng đơn vị
    ranked_units = sorted(unit_digit_scores.items(), key=lambda x: x[1], reverse=True)
    unit_rank_list = [d for d, s in ranked_units]
    
    # Tạo chuỗi log
    log_msg = f"🎲 Xếp hạng đuôi (Unit Digit): " + ", ".join([f"{d}" for d in unit_rank_list[:5]])
    
    # 3. Lấy Top 10 để chọn
    pool = all_scored_pairs[:pool_size]
    
    selected = []
    used_units = set()
    
    # Tìm theo thứ tự unit_rank_list
    for target_unit in unit_rank_list:
        if len(selected) >= n:
            break
            
        # Tìm pair có target_unit trong pool (đã sort theo score giảm dần)
        for pair, score in pool:
            if pair % 10 == target_unit and pair not in [p for p, s in selected]:
                selected.append((pair, score))
                used_units.add(target_unit)
                break  # Đã tìm được pair tốt nhất cho unit này, chuyển sang unit tiếp theo
                
    # 4. Fallback: Nếu vẫn chưa đủ n số (do pool 10 không đủ đa dạng đuôi)
    # Lấy thêm các số điểm cao nhất trong pool mà chưa được pick
    if len(selected) < n:
        for pair, score in pool:
            if len(selected) >= n:
                break
            if pair not in [p for p, s in selected]:
                selected.append((pair, score))
                used_units.add(pair % 10)
                
    return selected, log_msg


# ─── Model Name Display Mapping ─────────────────────────────────────────────
MODEL_DISPLAY_NAME = {
    "frequency":    "Freq",
    "gap_overdue":  "Gap",
    "markov":       "Markov",
    "xgboost_core": "XGB",
    "lstm":         "LSTM",
    "cdm":          "CDM",
    # legacy
    "freq_gap":     "Freq/Gap",
}


def _source_key(model_name: str, province: Optional[str], *, is_xsmb: bool) -> str:
    """Return the source id used for consensus counting."""
    if is_xsmb:
        return model_name
    return f"{model_name}@{province or 'ALL'}"


def _display_source(source_key: str) -> str:
    """Human-readable source label for Telegram logs."""
    if "@" not in source_key:
        return MODEL_DISPLAY_NAME.get(source_key, source_key)

    model_name, province = source_key.split("@", 1)
    model_label = MODEL_DISPLAY_NAME.get(model_name, model_name)
    return f"{model_label}/{province}"


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


def _build_candidate_shortlist(
    sorted_pairs: list[tuple[int, float]],
    pair_model_count: dict[int, int],
    pair_unique_models: dict[int, set],
    pair_model_families: Optional[dict[int, set]] = None,
    pair_provinces: Optional[dict[int, set]] = None,
    *,
    limit: int = 10,
) -> tuple[list[dict], str]:
    """Build a compact Top-N candidate audit log for Telegram."""
    candidates = []
    lines = ["📌 <b>Top 10 ứng viên multi-model (pool chung)</b>"]

    for rank, (pair, score) in enumerate(sorted_pairs[:limit], start=1):
        source_keys = sorted(pair_unique_models.get(pair, set()))
        display_models = [_display_source(s) for s in source_keys]
        support_count = pair_model_count.get(pair, 0)

        model_families = (pair_model_families or {}).get(pair, set())
        provinces = (pair_provinces or {}).get(pair, set())
        candidates.append({
            "rank": rank,
            "pair": pair,
            "score": round(score, 4),
            "support_count": support_count,
            "unique_model_count": len(source_keys),
            "model_family_count": len(model_families) or len(source_keys),
            "province_count": len(provinces),
            "models": source_keys,
            "sources": source_keys,
        })

        source_str = ", ".join(display_models) if display_models else "-"
        lines.append(
            f"   {rank:02d}. <code>{pair:02d}</code> = {score:.2f}đ"
            f" | votes={support_count}, sources={len(source_keys)}"
            f" | {source_str}"
        )

    return candidates, "\n".join(lines) if candidates else ""


def _candidate_hit_strength(candidate: dict, max_support: int) -> float:
    """Convert an ensemble candidate into a bounded hit-strength proxy."""
    score_strength = float(candidate.get("score_norm", 0.0))
    support_count = int(candidate.get("support_count", 0))
    unique_model_count = int(
        candidate.get("model_family_count", candidate.get("unique_model_count", 0))
    )
    support_strength = min(support_count / max(max_support, 1), 1.0)
    model_strength = min(unique_model_count / max(max_support, 1), 1.0)

    strength = (
        0.65 * score_strength
        + 0.25 * support_strength
        + 0.10 * model_strength
    )
    return max(0.01, min(0.99, strength))


def _combo_history_strength(combo: tuple[int, ...], history_tail_sets: Optional[list[set[int]]] = None) -> float:
    """Score co-hits with shrinkage toward zero for sparse matched history."""
    if not history_tail_sets:
        return 0.0

    combo_hits = 0
    pair_hits = 0
    pair_checks = 0
    combo_set = set(combo)
    combo_pairs = list(combinations(combo, 2))

    for tail_set in history_tail_sets:
        hits = len(combo_set & tail_set)
        if hits >= 2:
            combo_hits += 1
        for pair_a, pair_b in combo_pairs:
            pair_checks += 1
            if pair_a in tail_set and pair_b in tail_set:
                pair_hits += 1

    combo_rate = combo_hits / len(history_tail_sets)
    pair_rate = pair_hits / max(pair_checks, 1)
    raw_strength = 0.70 * combo_rate + 0.30 * pair_rate
    shrinkage = len(history_tail_sets) / (len(history_tail_sets) + COMBO_HISTORY_PRIOR_DRAWS)
    return raw_strength * shrinkage


def _score_two_of_three_combo(
    combo: tuple[int, ...],
    candidate_by_pair: dict[int, dict],
    history_tail_sets: Optional[list[set[int]]] = None,
) -> float:
    """Compute a relative ranking score for the >=2/3 objective."""
    probs = [_candidate_hit_strength(candidate_by_pair[p], max_support=6) for p in combo]
    p1, p2, p3 = probs
    p_exact_two = (
        p1 * p2 * (1.0 - p3)
        + p1 * p3 * (1.0 - p2)
        + p2 * p3 * (1.0 - p1)
    )
    p_three = p1 * p2 * p3
    support_bonus = sum(candidate_by_pair[p].get("support_count", 0) for p in combo) * 0.001
    # Same province-pair history is directly aligned with the XSMN/all win
    # rule, so it must be strong enough to break pure individual-score ties.
    history_strength = _combo_history_strength(combo, history_tail_sets)
    history_bonus = (history_strength ** 2) * COMBO_HISTORY_BONUS_WEIGHT
    return p_exact_two + p_three + support_bonus + history_bonus


def _select_best_two_of_three_combo(
    candidates: list[dict],
    *,
    top_n_output: int = 3,
    candidate_pool_size: int = 10,
    history_tail_sets: Optional[list[set[int]]] = None,
    require_distinct_unit_digits: bool = False,
) -> dict:
    """Pick the strongest >=2/3 combo, optionally with distinct unit digits."""
    pool = candidates[:candidate_pool_size]
    if len(pool) < top_n_output:
        if require_distinct_unit_digits:
            return {
                "top_pairs": [],
                "combo_score": 0.0,
                "candidate_pool": pool,
                "combo_candidates": [],
                "history_strength": 0.0,
                "score_type": "ranking_score_uncalibrated",
                "selection_status": "insufficient_digit_diversity",
                "diversity_constraint": "distinct_unit_digits",
            }
        top_pairs = [
            (int(candidate["pair"]), float(candidate.get("score", 0.0)))
            for candidate in pool
        ]
        return {
            "top_pairs": top_pairs,
            "combo_score": sum(score for _, score in top_pairs),
            "candidate_pool": pool,
            "score_type": "ranking_score_uncalibrated",
            "selection_status": "success",
            "diversity_constraint": "none",
        }

    scores = [float(candidate.get("score", 0.0)) for candidate in pool]
    score_min = min(scores)
    score_max = max(scores)
    score_range = score_max - score_min
    normalized_pool = []
    for candidate in pool:
        normalized = candidate.copy()
        raw_score = float(candidate.get("score", 0.0))
        normalized["score_norm"] = (
            1.0 if score_range < 1e-10 else (raw_score - score_min) / score_range
        )
        normalized_pool.append(normalized)

    candidate_by_pair = {int(candidate["pair"]): candidate for candidate in normalized_pool}
    best_combo: Optional[tuple[int, ...]] = None
    best_score = -1.0
    best_strength = -1.0

    for combo in combinations(candidate_by_pair.keys(), top_n_output):
        if (
            require_distinct_unit_digits
            and len({pair % 10 for pair in combo}) != top_n_output
        ):
            continue
        combo_score = _score_two_of_three_combo(combo, candidate_by_pair, history_tail_sets)
        combo_strength = sum(
            _candidate_hit_strength(candidate_by_pair[p], max_support=6)
            for p in combo
        )
        if (
            combo_score > best_score
            or (
                abs(combo_score - best_score) < 1e-12
                and combo_strength > best_strength
            )
        ):
            best_combo = combo
            best_score = combo_score
            best_strength = combo_strength

    if best_combo is None:
        return {
            "top_pairs": [],
            "combo_score": 0.0,
            "candidate_pool": normalized_pool,
            "combo_candidates": [],
            "history_strength": 0.0,
            "score_type": "ranking_score_uncalibrated",
            "selection_status": "insufficient_digit_diversity",
            "diversity_constraint": "distinct_unit_digits",
        }

    selected_pairs = list(best_combo)
    selected_pairs.sort(
        key=lambda pair: (
            candidate_by_pair[pair].get("score", 0.0),
            candidate_by_pair[pair].get("support_count", 0),
        ),
        reverse=True,
    )
    return {
        "top_pairs": [
            (pair, round(float(candidate_by_pair[pair].get("score", 0.0)), 4))
            for pair in selected_pairs
        ],
        "combo_score": round(best_score, 6),
        "candidate_pool": normalized_pool,
        "combo_candidates": [
            {
                **candidate_by_pair[pair],
                "hit_strength": round(
                    _candidate_hit_strength(candidate_by_pair[pair], max_support=6),
                    4,
                ),
            }
            for pair in selected_pairs
        ],
        "history_strength": round(_combo_history_strength(tuple(selected_pairs), history_tail_sets), 4),
        "score_type": "ranking_score_uncalibrated",
        "selection_status": "success",
        "diversity_constraint": (
            "distinct_unit_digits" if require_distinct_unit_digits else "none"
        ),
    }


def _source_contribution_pairs(result: Dict, max_pairs_per_source: Optional[int]) -> list:
    """Return a non-mutating ranking view eligible to vote in aggregation."""
    top_pairs = result.get("top_pairs", [])
    if max_pairs_per_source is None:
        return top_pairs
    return top_pairs[:max_pairs_per_source]


def compute_global_borda(
    model_results: List[Dict],
    recent_tails: List[int],
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
    region: Optional[str] = None,
    extended_tails: Optional[List[int]] = None,
    recent_province_tails: Optional[dict[str, set[int]]] = None,
) -> Dict:
    """
    Tính Global Weighted Borda Count + CombSUM từ tất cả model results.
    Kết hợp thuật toán phân tích lịch sử 3 kỳ quay gần nhất.

    v3.3: Region-aware scoring.
      - XSMB: Pure Borda + reduced consensus + MMR diversity
      - XSMN: giữ nguyên logic v3.2 (backward compatible)

    Aggregation formula:
      XSMN (CombSUM): FinalScore(n) = Σ w_m · s_m_norm(n) + consensus + history
      XSMB (Borda):   FinalScore(n) = Σ w_m · borda_pts(n) + consensus + history
                      → MMR diversity selection for top 3

    Args:
        model_results: List of dicts từ nhiều province
        recent_tails: List các 2 số cuối (tails) xuất hiện trong 3 kỳ gần nhất
        weights: dict model_name -> weight (override region config nếu cung cấp)
        top_n_output: số cặp cuối cùng output (default 3)
        region: 'XSMB' | 'XSMN' | None — determines scoring pipeline

    Returns:
        Dict chứa top 3 global pairs và scoring log.
    """
    # ── Load region-specific config ──
    rcfg = _get_region_config(region)
    w = weights if weights else rcfg["weights"]
    use_combsum = rcfg["combsum_enabled"]
    cons_gold_threshold = rcfg["consensus_gold_threshold"]
    cons_silver_threshold = rcfg["consensus_silver_threshold"]
    bonus_gold = rcfg["bonus_gold"]
    bonus_silver = rcfg["bonus_silver"]
    hist_overdue = rcfg["history_overdue"]
    hist_sweetspot = rcfg["history_sweetspot"]
    hist_potential = rcfg["history_potential"]
    use_diversity = rcfg["diversity_enabled"]
    diversity_lambda = rcfg["diversity_lambda"]
    cap_to_base = rcfg["cap_to_base"]
    rd_enabled = rcfg["recency_dampener_enabled"]
    rd_threshold = rcfg["recency_dampener_threshold"]
    rd_decay = rcfg["recency_dampener_decay"]
    repeat_penalty_enabled = rcfg["recent_repeat_penalty_enabled"]
    repeat_penalty = rcfg["recent_repeat_penalty"]
    max_pairs_per_source = rcfg["max_pairs_per_source"]

    is_xsmb = region and region.upper() == "XSMB"
    if is_xsmb:
        mode_label = "Borda" if not use_combsum else "CombSUM"
        print(f"     🔧 XSMB v3.3: {mode_label} mode, diversity={'ON' if use_diversity else 'OFF'}, "
              f"consensus=({cons_gold_threshold}/{cons_silver_threshold}), "
              f"bonus=({bonus_gold}/{bonus_silver})")

    # Filter thành công
    valid_results = [r for r in model_results if r.get("status") == "success" and r.get("top_pairs")]

    # Re-normalize weights cho models active
    active_model_names = {r["model_name"] for r in valid_results}
    w = {k: v for k, v in w.items() if k in active_model_names}
    w_sum = sum(w.values())
    if w_sum > 0:
        w = {k: v / w_sum for k, v in w.items()}

    if not valid_results:
        return {
            "top_pairs": [],
            "contributing_models": [],
            "ensemble_method": "weighted_borda",
            "borda_details": {},
            "consensus_pairs": [],
            "candidate_log": "",
            "top_candidates": [],
            "effective_weights": w,
        }

    # ── Scoring Mode Note ──
    # CombSUM (XSMN default): weight × normalized_score — tốt khi có 15+ data points
    # Pure Borda (XSMB v3.3): weight × rank_points — preserve rank info, tránh score compression

    # ── Tính Borda scores ──
    pair_scores: dict[int, float] = {}
    pair_model_count: dict[int, int] = {}  # đếm tổng model results chọn pair
    pair_unique_models: dict[int, set] = {}  # XSMN counts unique model@province sources per pair
    pair_model_families: dict[int, set] = {}
    pair_provinces: dict[int, set] = {}
    pair_model_names: dict[int, list] = {}  # track source names per pair

    contributing = []

    for result in valid_results:
        model_name = result["model_name"]
        prov = result.get("province", "unknown")
        source = _source_key(model_name, prov, is_xsmb=is_xsmb)
        weight = w.get(model_name, 0.15)  # fallback weight
        contributing.append(source)

        contribution_pairs = _source_contribution_pairs(result, max_pairs_per_source)
        for rank_idx, (pair, raw_score) in enumerate(contribution_pairs):
            rank = rank_idx + 1  # 1-indexed
            borda_pts = BORDA_POINTS.get(rank, 0)

            if borda_pts > 0:
                if use_combsum:
                    # CombSUM: weight × normalized_score
                    # Models đã output [0,1], không nhân thêm borda_pts
                    weighted_pts = weight * raw_score
                else:
                    # Pure Borda: weight × rank_points (5,4,3,2,1)
                    # Preserves rank diversity — rank 5 vẫn đóng góp 1×weight
                    weighted_pts = weight * borda_pts

                pair_scores[pair] = pair_scores.get(pair, 0) + weighted_pts
                pair_model_count[pair] = pair_model_count.get(pair, 0) + 1
                if pair not in pair_unique_models:
                    pair_unique_models[pair] = set()
                pair_unique_models[pair].add(source)
                pair_model_families.setdefault(pair, set()).add(model_name)
                if prov and prov != "unknown":
                    pair_provinces.setdefault(pair, set()).add(prov)
                if pair not in pair_model_names:
                    pair_model_names[pair] = []
                pair_model_names[pair].append(source)

    # ── Minimum Source Agreement Filter ──
    if MIN_MODELS_AGREE > 1:
        pair_scores = {
            p: s for p, s in pair_scores.items()
            if pair_model_count.get(p, 0) >= MIN_MODELS_AGREE
        }

    # ── Consensus bonus ──
    # Consensus is based on independent model families. Source and province
    # counts are still retained for audit, but the same algorithm at two
    # provinces cannot manufacture an extra model vote.
    for pair in list(pair_scores.keys()):
        unique_count = len(pair_model_families.get(pair, set()))
        base_score = pair_scores[pair]
        bonus = 0.0
        
        if unique_count >= cons_gold_threshold:
            bonus = bonus_gold
        elif unique_count >= cons_silver_threshold:
            bonus = bonus_silver
            
        if bonus > 0:
            if cap_to_base:
                cap = max(abs(base_score), 0.5)
                bonus = min(bonus, cap)
            pair_scores[pair] += bonus

    # ── Thuật toán kết hợp lịch sử N kỳ gần nhất CÙNG THỨ ──
    recent_counts = {}
    for t in recent_tails:
        recent_counts[t] = recent_counts.get(t, 0) + 1
        
    extended_counts = {}
    if extended_tails:
        for t in extended_tails:
            extended_counts[t] = extended_counts.get(t, 0) + 1

    for pair in pair_scores:
        count = recent_counts.get(pair, 0)
        
        if is_xsmb:
            # XSMB (v3.3): 5 tuần + Toxic Gap 10 tuần
            if count >= 3:
                pair_scores[pair] += hist_overdue
            elif count == 2:
                pair_scores[pair] += 0.6  # Momentum mạnh
            elif count == 1:
                pair_scores[pair] += 0.3  # Momentum vừa
            else:
                # count == 0: Xét Toxic Gap
                ext_count = extended_counts.get(pair, 0)
                if ext_count == 0 and extended_tails:
                    # Gan nguy hiểm (10 tuần không ra)
                    pair_scores[pair] += hist_overdue 
                else:
                    pair_scores[pair] += hist_potential
        else:
            # XSMN: chỉ phạt khi nổ đủ 3/3 kỳ cùng thứ gần nhất.
            # 2/3 là tín hiệu còn nhịp, nhưng không boost để tránh chasing.
            if count >= 3:
                pair_scores[pair] += hist_overdue
            elif count == 1:
                pair_scores[pair] += hist_sweetspot
            elif count == 0:
                pair_scores[pair] += hist_potential
                
    # ── Recency Dampener ──
    if rd_enabled:
        for pair in pair_scores:
            count = recent_counts.get(pair, 0)
            if count >= rd_threshold:
                decay = rd_decay ** (count - 1)
                pair_scores[pair] *= decay

    # ── XSMN Recent Repeat Penalty ──
    # Same-weekday history misses stations that draw twice per week (e.g. TP.HCM).
    # Penalize lightly if a pair appeared in the station's most recent draw.
    repeat_penalty_applied: dict[int, set[str]] = {}
    if (not is_xsmb) and repeat_penalty_enabled and recent_province_tails:
        for pair in list(pair_scores.keys()):
            repeat_provinces = set()
            for source in pair_unique_models.get(pair, set()):
                if "@" not in source:
                    continue
                _model_name, province = source.split("@", 1)
                if pair in recent_province_tails.get(province, set()):
                    repeat_provinces.add(province)

            if repeat_provinces:
                pair_scores[pair] += repeat_penalty * len(repeat_provinces)
                repeat_penalty_applied[pair] = repeat_provinces

    # ── Sort & pick top N (with optional diversity enforcement) ──
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)
    top_candidates, candidate_log = _build_candidate_shortlist(
        sorted_pairs,
        pair_model_count,
        pair_unique_models,
        pair_model_families,
        pair_provinces,
        limit=10,
    )

    if use_diversity and len(sorted_pairs) > top_n_output:
        # Unit Digit Diversity selection uses the same Top 10 pool shown in Telegram.
        diverse_selection, div_log = _select_unit_digit_diversity(
            sorted_pairs, n=top_n_output, pool_size=10
        )
        top_pairs = [(pair, round(score, 4)) for pair, score in diverse_selection]
        print(f"     {div_log}")
        print(f"     🎲 Diversity picked: {[f'{p:02d}' for p,_ in top_pairs]}")
        # Thêm log message vào candidate_log để in ra Telegram
        candidate_log = div_log + "\n" + candidate_log
    else:
        top_pairs = [(pair, round(score, 4)) for pair, score in sorted_pairs[:top_n_output]]

    # ── Tạo Telegram Log cho Top 3 ──
    scoring_log = []
    for pair, score in top_pairs:
        log_lines = []
        # 1. Base Score
        base_points = 0
        models_hit = []
        for result in valid_results:
            contribution_pairs = _source_contribution_pairs(result, max_pairs_per_source)
            for r_idx, (p, raw_s) in enumerate(contribution_pairs):
                if p == pair:
                    wt = w.get(result['model_name'], 0.15)
                    if use_combsum:
                        pts = wt * raw_s
                    else:
                        pts = BORDA_POINTS.get(r_idx + 1, 0) * wt
                    base_points += pts
                    source = _source_key(
                        result['model_name'], result.get("province", "unknown"), is_xsmb=is_xsmb
                    )
                    models_hit.append(f"{_display_source(source)}(Top{r_idx+1})")

        log_lines.append(f"🔸 <b>[{pair:02d}]</b> = {score:.2f}đ")
        log_lines.append(f"   ├ Cơ sở: {base_points:.2f}đ từ {', '.join(models_hit)}")

        # 2. Consensus Bonus (independent model families)
        c_unique = len(pair_model_families.get(pair, set()))
        displayed_bonus = 0.0
        if c_unique >= cons_gold_threshold:
            displayed_bonus = bonus_gold
        elif c_unique >= cons_silver_threshold:
            displayed_bonus = bonus_silver
        if displayed_bonus > 0:
            if cap_to_base:
                displayed_bonus = min(displayed_bonus, max(abs(base_points), 0.5))
            log_lines.append(
                f"   ├ Đồng thuận: +{displayed_bonus:.2f}đ "
                f"({c_unique} model độc lập đồng ý)"
            )

        # 3. History Bonus
        h_count = recent_counts.get(pair, 0)
        n_weeks = 5 if is_xsmb else 3
        
        if is_xsmb:
            if h_count >= 3:
                log_lines.append(f"   └ Lịch sử: {hist_overdue}đ (Nổ {h_count} lần/{n_weeks} tuần)")
            elif h_count == 2:
                log_lines.append(f"   └ Lịch sử: +0.6đ (Nổ {h_count} nhịp/{n_weeks} tuần)")
            elif h_count == 1:
                log_lines.append(f"   └ Lịch sử: +0.3đ (Nổ {h_count} nhịp/{n_weeks} tuần)")
            else:
                ext_count = extended_counts.get(pair, 0)
                if ext_count == 0 and extended_tails:
                    log_lines.append(f"   └ Lịch sử: {hist_overdue}đ (Gan nguy hiểm 10 tuần)")
                else:
                    log_lines.append(f"   └ Lịch sử: +{hist_potential}đ (Đang nén {n_weeks} tuần chưa ra)")
        else:
            if h_count >= 3:
                log_lines.append(f"   └ Lịch sử: {hist_overdue}đ (Nổ {h_count} lần/{n_weeks} tuần)")
            elif h_count == 2:
                log_lines.append(f"   └ Lịch sử: 0đ (Nổ 2/{n_weeks}, chưa phạt)")
            elif h_count == 1:
                log_lines.append(f"   └ Lịch sử: +{hist_sweetspot}đ (Nổ đúng 1 nhịp/{n_weeks} tuần)")
            else:
                log_lines.append(f"   └ Lịch sử: +{hist_potential}đ (Đang nén {n_weeks} tuần chưa ra)")

            if pair in repeat_penalty_applied:
                provs = ", ".join(sorted(repeat_penalty_applied[pair]))
                log_lines.append(
                    f"   └ Gần nhất: {repeat_penalty:+.2f}đ (vừa ra ở {provs})"
                )

        # 4. Diversity tag (XSMB v3.3)
        if use_diversity:
            pair_models_set = pair_unique_models.get(pair, set())
            model_tags = [_display_source(m) for m in pair_models_set]
            log_lines.append(f"   └ Sources: {', '.join(sorted(model_tags))}")

        scoring_log.append('\n'.join(log_lines))

    consensus_pairs_list = [
        pair for pair, families in pair_model_families.items()
        if len(families) >= cons_silver_threshold
    ]

    # Determine ensemble method label
    if is_xsmb:
        method_label = 'expert_borda_v3.3_diverse' if use_diversity else 'expert_borda_v3.3'
    else:
        method_label = 'expert_borda_combsum_v3.2'

    return {
        'top_pairs': top_pairs,
        'contributing_models': list(set(contributing)),
        'ensemble_method': method_label,
        'borda_details': {p: round(s, 4) for p, s in sorted_pairs},
        'consensus_pairs': consensus_pairs_list,
        'scoring_log': '\n\n'.join(scoring_log),
        'candidate_log': candidate_log,
        'top_candidates': top_candidates,
        'effective_weights': w,
    }


def compute_xsmn_province_representative_ensemble(
    model_results: List[Dict],
    provinces: List[str],
    recent_tails_by_province: Optional[dict[str, List[int]]] = None,
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
    representatives_per_province: int = 2,
    recent_province_tails: Optional[dict[str, set[int]]] = None,
) -> Dict:
    """
    XSMN province-first consensus picker.

    Quy tắc:
      1. Chấm điểm riêng từng tỉnh để ưu tiên nguồn cùng tỉnh, cùng nhịp.
      2. Mỗi tỉnh cử ``representatives_per_province`` số điểm cao nhất.
      3. Merge các đại diện; nếu trùng số giữa nhiều tỉnh thì cộng thêm phần
         điểm cross-province nhẹ.
      4. Chọn ``top_n_output`` số có điểm đại diện cao nhất.

    This keeps local station signal quality from being buried by global
    model@province vote counting.
    """
    recent_tails_by_province = recent_tails_by_province or {}

    if not provinces:
        flattened_recent = [
            tail
            for tails in recent_tails_by_province.values()
            for tail in tails
        ]
        return compute_global_borda(
            model_results,
            flattened_recent,
            weights=weights,
            top_n_output=top_n_output,
            region="XSMN",
            recent_province_tails=recent_province_tails,
        )

    province_outputs: dict[str, Dict] = {}
    representatives: list[dict] = []
    contributing: set[str] = set()

    for province in provinces:
        province_results = [
            r for r in model_results
            if r.get("province") == province
            and r.get("status") == "success"
            and r.get("top_pairs")
        ]
        if not province_results:
            continue

        province_repeat_tails = None
        if recent_province_tails and province in recent_province_tails:
            province_repeat_tails = {province: recent_province_tails[province]}

        province_output = compute_global_borda(
            province_results,
            recent_tails_by_province.get(province, []),
            weights=weights,
            top_n_output=max(top_n_output, representatives_per_province),
            region="XSMN",
            recent_province_tails=province_repeat_tails,
        )
        province_outputs[province] = province_output

        for source in province_output.get("contributing_models", []):
            contributing.add(source)

        province_candidates = province_output.get("top_candidates", [])
        if not province_candidates:
            province_candidates = [
                {
                    "rank": idx + 1,
                    "pair": pair,
                    "score": score,
                    "support_count": 0,
                    "unique_model_count": 0,
                    "models": [],
                    "sources": [],
                }
                for idx, (pair, score) in enumerate(province_output.get("top_pairs", []))
            ]

        for candidate in province_candidates[:representatives_per_province]:
            representatives.append({
                "province": province,
                "pair": int(candidate["pair"]),
                "province_rank": int(candidate["rank"]),
                "province_score": float(candidate["score"]),
                "support_count": int(candidate.get("support_count", 0)),
                "unique_model_count": int(candidate.get("unique_model_count", 0)),
                "sources": list(candidate.get("sources") or candidate.get("models") or []),
            })

    if not representatives:
        return {
            "top_pairs": [],
            "contributing_models": [],
            "ensemble_method": "xsmn_province_representative_v3.4",
            "borda_details": {},
            "consensus_pairs": [],
            "scoring_log": "",
            "candidate_log": "",
            "top_candidates": [],
            "province_outputs": province_outputs,
            "province_representatives": [],
        }

    merged: dict[int, dict] = {}
    for rep in representatives:
        pair = rep["pair"]
        item = merged.setdefault(pair, {
            "pair": pair,
            "representatives": [],
            "best_score": 0.0,
            "combined_score": 0.0,
            "province_count": 0,
            "sources": set(),
        })
        item["representatives"].append(rep)
        item["best_score"] = max(item["best_score"], rep["province_score"])
        item["sources"].update(rep["sources"])

    for item in merged.values():
        reps = item["representatives"]
        scores = sorted((rep["province_score"] for rep in reps), reverse=True)
        best_score = scores[0]
        secondary_score = sum(scores[1:])
        province_count = len({rep["province"] for rep in reps})
        cross_province_bonus = 0.35 * secondary_score
        duplicate_bonus = 0.25 * max(province_count - 1, 0)

        item["province_count"] = province_count
        item["combined_score"] = best_score + cross_province_bonus + duplicate_bonus

    sorted_items = sorted(
        merged.values(),
        key=lambda item: (
            item["combined_score"],
            item["province_count"],
            -min(rep["province_rank"] for rep in item["representatives"]),
            -item["pair"],
        ),
        reverse=True,
    )

    top_items = sorted_items[:top_n_output]
    top_pairs = [
        (item["pair"], round(item["combined_score"], 4))
        for item in top_items
    ]

    top_candidates = []
    for rank, item in enumerate(sorted_items, start=1):
        reps = item["representatives"]
        sources = sorted(item["sources"])
        top_candidates.append({
            "rank": rank,
            "pair": item["pair"],
            "score": round(item["combined_score"], 4),
            "support_count": sum(rep["support_count"] for rep in reps),
            "unique_model_count": len(sources),
            "province_count": item["province_count"],
            "models": sources,
            "sources": sources,
            "representatives": reps,
        })

    candidate_lines = [
        f"📌 <b>Đại diện XSMN theo tỉnh (Top {representatives_per_province}/tỉnh)</b>"
    ]
    for province in provinces:
        reps = [rep for rep in representatives if rep["province"] == province]
        if not reps:
            candidate_lines.append(f"   • {province}: không có ứng viên hợp lệ")
            continue
        formatted = ", ".join(
            f"<code>{rep['pair']:02d}</code>({rep['province_score']:.2f}đ)"
            for rep in reps
        )
        candidate_lines.append(f"   • {province}: {formatted}")

    candidate_lines.append("📌 <b>Top ứng viên sau merge đại diện</b>")
    for candidate in top_candidates[:10]:
        provs = sorted({
            rep["province"]
            for rep in candidate["representatives"]
        })
        prov_str = ", ".join(provs)
        candidate_lines.append(
            f"   {candidate['rank']:02d}. <code>{candidate['pair']:02d}</code>"
            f" = {candidate['score']:.2f}đ | tỉnh={candidate['province_count']}"
            f" | {prov_str}"
        )

    scoring_logs = []
    for item in top_items:
        reps = item["representatives"]
        detail = ", ".join(
            f"{rep['province']}#{rep['province_rank']}={rep['province_score']:.2f}đ"
            for rep in reps
        )
        source_labels = sorted({_display_source(src) for src in item["sources"]})
        scoring_logs.append(
            f"🔸 <b>[{item['pair']:02d}]</b> = {item['combined_score']:.2f}đ\n"
            f"   ├ Đại diện tỉnh: {detail}\n"
            f"   ├ Số tỉnh cử: {item['province_count']}\n"
            f"   └ Sources: {', '.join(source_labels) if source_labels else '-'}"
        )

    consensus_pairs = [
        item["pair"]
        for item in sorted_items
        if item["province_count"] > 1
    ]

    return {
        "top_pairs": top_pairs,
        "contributing_models": sorted(contributing),
        "ensemble_method": "xsmn_province_representative_v3.4",
        "borda_details": {
            item["pair"]: round(item["combined_score"], 4)
            for item in sorted_items
        },
        "consensus_pairs": consensus_pairs,
        "scoring_log": "\n\n".join(scoring_logs),
        "candidate_log": "\n".join(candidate_lines),
        "top_candidates": top_candidates,
        "province_outputs": province_outputs,
        "province_representatives": representatives,
        "target_provinces": list(provinces),
        "effective_weights": next(iter(province_outputs.values()), {}).get("effective_weights", {}),
    }


def compute_xsmn_merged_combo_selector_ensemble(
    model_results: List[Dict],
    provinces: List[str],
    recent_tails_by_province: Optional[dict[str, List[int]]] = None,
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
    representatives_per_province: int = 2,
    recent_province_tails: Optional[dict[str, set[int]]] = None,
    combo_history_tail_sets: Optional[list[set[int]]] = None,
) -> Dict:
    """
    XSMN merged-province combo picker.

    Quy tắc:
      1. Chấm điểm pool chung cho toàn bộ tỉnh XSMN xổ trong ngày.
      2. Lấy Top 10 ứng viên merged từ tất cả model@province sources.
      3. Sinh toàn bộ combo 3 số trong pool và score mục tiêu >=2/3.
      4. Chọn combo có expected hit-strength cao nhất trên tail set merged.

    This matches the configured XSMN/all rule where target provinces for a day
    are merged before counting whether at least 2 of 3 numbers hit.
    """
    recent_tails_by_province = recent_tails_by_province or {}

    if not provinces:
        flattened_recent = [
            tail
            for tails in recent_tails_by_province.values()
            for tail in tails
        ]
        return compute_global_borda(
            model_results,
            flattened_recent,
            weights=weights,
            top_n_output=top_n_output,
            region="XSMN",
            recent_province_tails=recent_province_tails,
        )

    flattened_recent = [
        tail
        for tails in recent_tails_by_province.values()
        for tail in tails
    ]
    global_output = compute_global_borda(
        model_results,
        flattened_recent,
        weights=weights,
        top_n_output=top_n_output,
        region="XSMN",
        recent_province_tails=recent_province_tails,
    )

    province_outputs: dict[str, Dict] = {}
    representatives: list[dict] = []
    contributing: set[str] = set()

    for province in provinces:
        province_results = [
            r for r in model_results
            if r.get("province") == province
            and r.get("status") == "success"
            and r.get("top_pairs")
        ]
        if not province_results:
            continue

        province_repeat_tails = None
        if recent_province_tails and province in recent_province_tails:
            province_repeat_tails = {province: recent_province_tails[province]}

        province_output = compute_global_borda(
            province_results,
            recent_tails_by_province.get(province, []),
            weights=weights,
            top_n_output=max(top_n_output, representatives_per_province),
            region="XSMN",
            recent_province_tails=province_repeat_tails,
        )
        province_outputs[province] = province_output

        for source in province_output.get("contributing_models", []):
            contributing.add(source)

        # Representatives must be the highest scored candidates, not the
        # diversity-filtered top_pairs.
        province_candidates = province_output.get("top_candidates", [])
        if not province_candidates:
            province_candidates = [
                {
                    "rank": idx + 1,
                    "pair": pair,
                    "score": score,
                    "support_count": 0,
                    "unique_model_count": 0,
                    "models": [],
                    "sources": [],
                }
                for idx, (pair, score) in enumerate(province_output.get("top_pairs", []))
            ]

        for candidate in province_candidates[:representatives_per_province]:
            representatives.append({
                "province": province,
                "pair": int(candidate["pair"]),
                "province_rank": int(candidate["rank"]),
                "province_score": float(candidate["score"]),
                "support_count": int(candidate.get("support_count", 0)),
                "unique_model_count": int(candidate.get("unique_model_count", 0)),
                "sources": list(candidate.get("sources") or candidate.get("models") or []),
            })

    merged_candidates = global_output.get("top_candidates", [])
    if not merged_candidates:
        return {
            "top_pairs": [],
            "contributing_models": [],
            "ensemble_method": "xsmn_merged_combo_selector_v3.5",
            "borda_details": {},
            "consensus_pairs": [],
            "scoring_log": "",
            "candidate_log": "",
            "top_candidates": [],
            "province_outputs": province_outputs,
            "merged_combo_output": {},
            "province_representatives": [],
        }

    merged_combo_output = _select_best_two_of_three_combo(
        merged_candidates,
        top_n_output=top_n_output,
        candidate_pool_size=10,
        history_tail_sets=combo_history_tail_sets,
        require_distinct_unit_digits=_get_region_config("XSMN")[
            "require_distinct_unit_digits"
        ],
    )
    top_pairs = merged_combo_output["top_pairs"]

    province_presence: dict[int, set[str]] = {}
    for candidate in merged_combo_output.get("candidate_pool", []):
        for source in candidate.get("sources", []):
            if "@" not in source:
                continue
            _model_name, province = source.split("@", 1)
            province_presence.setdefault(int(candidate["pair"]), set()).add(province)

    merged_top_candidates = []
    for candidate in merged_combo_output.get("candidate_pool", []):
        candidate_with_provinces = candidate.copy()
        candidate_provinces = sorted(province_presence.get(int(candidate["pair"]), set()))
        candidate_with_provinces["province_count"] = len(candidate_provinces)
        candidate_with_provinces["provinces"] = candidate_provinces
        merged_top_candidates.append(candidate_with_provinces)

    candidate_lines = [
        "📌 <b>XSMN merged combo selector: chọn bộ 3 tối ưu mục tiêu ≥2/3</b>"
    ]
    combo_pairs = ", ".join(
        f"<code>{pair:02d}</code>"
        for pair, _score in merged_combo_output.get("top_pairs", [])
    )
    candidate_lines.append(
        f"   • Merged provinces: {', '.join(provinces)}"
        f" | combo [{combo_pairs}]"
        f" | score={merged_combo_output.get('combo_score', 0.0):.4f}"
        f" | history={merged_combo_output.get('history_strength', 0.0):.2f}"
        f" | diversity={merged_combo_output.get('diversity_constraint', 'none')}"
        f" | status={merged_combo_output.get('selection_status', 'success')}"
    )
    candidate_lines.append("📌 <b>Top 10 ứng viên merged</b>")
    formatted = ", ".join(
        f"<code>{int(candidate['pair']):02d}</code>({float(candidate.get('score', 0.0)):.2f})"
        for candidate in merged_combo_output.get("candidate_pool", [])[:10]
    )
    candidate_lines.append(f"   • all: {formatted}")

    scoring_logs = []
    selected_candidates = {
        int(candidate["pair"]): candidate
        for candidate in merged_combo_output.get("combo_candidates", [])
    }
    for pair, score in top_pairs:
        candidate = selected_candidates.get(pair, {})
        source_labels = sorted(
            {_display_source(src) for src in candidate.get("sources", [])}
        )
        scoring_logs.append(
            f"🔸 <b>[{pair:02d}]</b> = {score:.2f}đ\n"
            f"   ├ Scope: merged XSMN/all\n"
            f"   ├ Hit-strength: {candidate.get('hit_strength', 0.0):.2f}\n"
            f"   ├ Support: {candidate.get('support_count', 0)} nguồn\n"
            f"   └ Sources: {', '.join(source_labels) if source_labels else '-'}"
        )

    consensus_pairs = [
        pair
        for pair, province_set in province_presence.items()
        if len(province_set) > 1
    ]

    return {
        "top_pairs": top_pairs,
        "contributing_models": sorted(set(contributing) | set(global_output.get("contributing_models", []))),
        "ensemble_method": "xsmn_merged_combo_selector_v3.5",
        "borda_details": {
            int(candidate["pair"]): round(float(candidate.get("score", 0.0)), 4)
            for candidate in merged_combo_output.get("candidate_pool", [])
        },
        "consensus_pairs": consensus_pairs,
        "scoring_log": "\n\n".join(scoring_logs),
        "candidate_log": "\n".join(candidate_lines),
        "top_candidates": merged_top_candidates,
        "selected_province": "all",
        "combo_score": merged_combo_output.get("combo_score", 0.0),
        "combo_score_type": merged_combo_output.get("score_type", "ranking_score_uncalibrated"),
        "selection_status": merged_combo_output.get("selection_status", "success"),
        "diversity_constraint": merged_combo_output.get("diversity_constraint", "none"),
        "province_outputs": province_outputs,
        "merged_combo_output": merged_combo_output,
        "province_representatives": representatives,
        "target_provinces": list(provinces),
        "effective_weights": global_output.get("effective_weights", {}),
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
    top = list(ensemble_output["top_pairs"])

    if len(top) < 3:
        # Pad with -1 if not enough results
        while len(top) < 3:
            top.append((-1, 0.0))

    run_metadata = {
        "score_type": ensemble_output.get("combo_score_type", "ranking_score_uncalibrated"),
        "target_provinces": ensemble_output.get("target_provinces", []),
        "effective_weights": ensemble_output.get("effective_weights", {}),
        "combo_score": ensemble_output.get("combo_score"),
        "data_cutoff": ensemble_output.get("data_cutoff"),
        "model_versions": ensemble_output.get("model_versions", {}),
        "top_candidates": ensemble_output.get("top_candidates", [])[:10],
    }

    return {
        "prediction_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
        "region": region,
        "province": province,  # Có thể là None cho Global
        "pair_1": top[0][0],
        "pair_2": top[1][0],
        "pair_3": top[2][0],
        # Legacy DB columns keep the old prob_* names; ensemble values are
        # relative scores unless a calibrated model explicitly writes probs.
        "prob_1": top[0][1],
        "prob_2": top[1][1],
        "prob_3": top[2][1],
        "model_version": "ensemble_v3.5",
        "ensemble_method": ensemble_output["ensemble_method"],
        "contributing_models": ensemble_output["contributing_models"],
        "final_scores": [s for _, s in top[:3]],
        "hit": None,
        "matched_pairs": None,
        "tail_set": None,
        "scoring_log": ensemble_output.get('scoring_log', ''),
        "candidate_log": ensemble_output.get('candidate_log', ''),
        "run_metadata": run_metadata,
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
    top = list(model_result.get("top_pairs", []))

    # Pad to 5
    while len(top) < 5:
        top.append((None, None))

    # Determine model type
    model_name = model_result.get("model_name", "unknown")
    if model_name in ("frequency", "gap_overdue", "markov", "freq_gap", "cdm"):
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
