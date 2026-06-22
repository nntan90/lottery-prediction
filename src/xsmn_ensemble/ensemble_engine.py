"""
ensemble_engine.py — Weighted Borda Count + CombSUM Aggregation Engine (v3.3)
Kết hợp output từ 5 sub-models thành Top 3 cuối cùng.

Models (v3.2+):
  1. frequency      — Pure frequency/hot-cool scoring
  2. gap_overdue    — Pure gap/overdue scoring
  3. markov         — Markov Chain transition
  4. xgboost_core   — XGBoost ML classifier
  5. lstm           — LSTM/GRU sequence model

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
  - XSMN: giữ nguyên logic v3.2 (backward compatible)
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

        candidates.append({
            "rank": rank,
            "pair": pair,
            "score": round(score, 4),
            "support_count": support_count,
            "unique_model_count": len(source_keys),
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


def compute_global_borda(
    model_results: List[Dict],
    recent_tails: List[int],
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
    region: Optional[str] = None,
    extended_tails: Optional[List[int]] = None,
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
        }

    # ── Scoring Mode Note ──
    # CombSUM (XSMN default): weight × normalized_score — tốt khi có 15+ data points
    # Pure Borda (XSMB v3.3): weight × rank_points — preserve rank info, tránh score compression

    # ── Tính Borda scores ──
    pair_scores: dict[int, float] = {}
    pair_model_count: dict[int, int] = {}  # đếm tổng model results chọn pair
    pair_unique_models: dict[int, set] = {}  # XSMN counts unique model@province sources per pair
    pair_model_names: dict[int, list] = {}  # track source names per pair

    contributing = []

    for result in valid_results:
        model_name = result["model_name"]
        prov = result.get("province", "unknown")
        source = _source_key(model_name, prov, is_xsmb=is_xsmb)
        weight = w.get(model_name, 0.15)  # fallback weight
        contributing.append(source)

        for rank_idx, (pair, raw_score) in enumerate(result["top_pairs"]):
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
    # XSMN pools all selected pairs from the two daily provinces first, then
    # scores/ranks once globally. Consensus therefore counts unique model@province
    # sources, e.g. frequency@tp-hcm and frequency@dong-thap are two sources.
    for pair in list(pair_scores.keys()):
        unique_count = len(pair_unique_models.get(pair, set()))
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
            # XSMN (v3.2): 3 tuần
            if count >= 2:
                pair_scores[pair] += hist_overdue
            elif count == 1:
                pair_scores[pair] += hist_sweetspot
            else:
                pair_scores[pair] += hist_potential
                
    # ── Recency Dampener ──
    if rd_enabled:
        for pair in pair_scores:
            count = recent_counts.get(pair, 0)
            if count >= rd_threshold:
                decay = rd_decay ** (count - 1)
                pair_scores[pair] *= decay

    # ── Sort & pick top N (with optional diversity enforcement) ──
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)
    top_candidates, candidate_log = _build_candidate_shortlist(
        sorted_pairs, pair_model_count, pair_unique_models, limit=10
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
            for r_idx, (p, raw_s) in enumerate(result.get('top_pairs', [])):
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

        # 2. Consensus Bonus (unique models)
        c_unique = len(pair_unique_models.get(pair, set()))
        if c_unique >= cons_gold_threshold:
            log_lines.append(f"   ├ Đồng thuận: +{bonus_gold}đ ({c_unique} nguồn đồng ý)")
        elif c_unique >= cons_silver_threshold:
            log_lines.append(f"   ├ Đồng thuận: +{bonus_silver}đ ({c_unique} nguồn đồng ý)")

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
            if h_count >= 2:
                log_lines.append(f"   └ Lịch sử: {hist_overdue}đ (Nổ {h_count} lần/{n_weeks} tuần)")
            elif h_count == 1:
                log_lines.append(f"   └ Lịch sử: +{hist_sweetspot}đ (Nổ đúng 1 nhịp/{n_weeks} tuần)")
            else:
                log_lines.append(f"   └ Lịch sử: +{hist_potential}đ (Đang nén {n_weeks} tuần chưa ra)")

        # 4. Diversity tag (XSMB v3.3)
        if use_diversity:
            pair_models_set = pair_unique_models.get(pair, set())
            model_tags = [_display_source(m) for m in pair_models_set]
            log_lines.append(f"   └ Sources: {', '.join(sorted(model_tags))}")

        scoring_log.append('\n'.join(log_lines))

    consensus_pairs_list = [p for p, models in pair_unique_models.items() if len(models) >= cons_silver_threshold]

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
        # Legacy DB columns keep the old prob_* names; ensemble values are
        # relative scores unless a calibrated model explicitly writes probs.
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
        "scoring_log": ensemble_output.get('scoring_log', ''),
        "candidate_log": ensemble_output.get('candidate_log', ''),
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
