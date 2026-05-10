"""
ensemble_engine.py — Weighted Borda Count Aggregation Engine
Kết hợp output từ 3 sub-models (freq_gap, markov, xgboost_core) thành Top 3 cuối cùng.

Borda Score:
  Rank 1 → 5 điểm
  Rank 2 → 4 điểm
  Rank 3 → 3 điểm
  Rank 4 → 2 điểm
  Rank 5 → 1 điểm
  Ngoài Top 5 → 0 điểm

Final Score:
  Borda(pair) = Σ_m (w_m × pts_m(pair))

Trọng số Phase 1:
  freq_gap:     0.25
  markov:       0.25
  xgboost_core: 0.50

Guardrails:
  - Ưu tiên consensus (≥2/3 model cùng chọn)
  - 1 model lỗi → ensemble vẫn chạy với 2 model còn lại
"""

from typing import List, Dict, Tuple, Optional


# Borda points by rank position (1-indexed)
BORDA_POINTS = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}

# Default weights (Phase 1)
DEFAULT_WEIGHTS: dict[str, float] = {
    "freq_gap": 0.25,
    "markov": 0.25,
    "xgboost_core": 0.50,
}

# Consensus and History rules
CONSENSUS_THRESHOLD_GOLD = 3
CONSENSUS_THRESHOLD_SILVER = 2
BONUS_GOLD = 5.0
BONUS_SILVER = 2.0

HISTORY_PENALTY_OVERDUE = -2.0  # Nổ >= 2 lần trong 3 tuần
HISTORY_BONUS_SWEETSPOT = 2.0   # Nổ đúng 1 lần
HISTORY_BONUS_POTENTIAL = 1.0   # Chưa nổ lần nào


def compute_global_borda(
    model_results: List[Dict],
    recent_tails: List[int],
    weights: Optional[dict[str, float]] = None,
    top_n_output: int = 3,
) -> Dict:
    """
    Tính Global Weighted Borda Count từ tất cả model results (từ nhiều đài).
    Kết hợp thuật toán phân tích lịch sử 3 kỳ quay gần nhất.

    Args:
        model_results: List of dicts từ nhiều province
        recent_tails: List các 2 số cuối (tails) xuất hiện trong 3 kỳ gần nhất
        weights: dict model_name -> weight
        top_n_output: số cặp cuối cùng output (default 3)

    Returns:
        Dict chứa top 3 global pairs và scoring log.
    """
    w = {
        'freq_gap': 1.0,
        'markov': 1.0,
        'xgboost_core': 2.0,  # AI is weighted heavily
    }

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

    # Tính Borda scores
    pair_scores: dict[int, float] = {}
    pair_model_count: dict[int, int] = {}  # đếm số model chọn pair này

    contributing = []

    for result in valid_results:
        model_name = result["model_name"]
        prov = result.get("province", "unknown")
        weight = w.get(model_name, 0.25)  # fallback weight
        contributing.append(f"{model_name}_{prov}")

        for rank_idx, (pair, _raw_score) in enumerate(result["top_pairs"]):
            rank = rank_idx + 1  # 1-indexed
            borda_pts = BORDA_POINTS.get(rank, 0)

            if borda_pts > 0:
                weighted_pts = weight * borda_pts
                pair_scores[pair] = pair_scores.get(pair, 0) + weighted_pts
                pair_model_count[pair] = pair_model_count.get(pair, 0) + 1

    # Consensus bonus
    for pair, count in pair_model_count.items():
        if count >= CONSENSUS_THRESHOLD_GOLD:
            pair_scores[pair] += BONUS_GOLD
        elif count >= CONSENSUS_THRESHOLD_SILVER:
            pair_scores[pair] += BONUS_SILVER

    # --- Thuật toán kết hợp lịch sử 3 kỳ gần nhất CÙNG THỨ ---
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

    # Sort & pick top N
    sorted_pairs = sorted(pair_scores.items(), key=lambda x: x[1], reverse=True)
    top_pairs = [(pair, round(score, 4)) for pair, score in sorted_pairs[:top_n_output]]

    # Tạo Telegram Log cho Top 3
    scoring_log = ['📝 <b>CHI TIẾT CHẤM ĐIỂM (EXPERT SCORING):</b>']
    for pair, score in top_pairs:
        log_lines = []
        # 1. Base Score
        base_points = 0
        models_hit = []
        for result in valid_results:
            for r_idx, (p, _) in enumerate(result.get('top_pairs', [])):
                if p == pair:
                    weight = w.get(result['model_name'], 1.0)
                    pts = BORDA_POINTS.get(r_idx + 1, 0) * weight
                    base_points += pts
                    m_name = 'XGB' if 'xgboost' in result['model_name'] else ('Freq' if 'freq' in result['model_name'] else 'Markov')
                    models_hit.append(f"{m_name}(Top{r_idx+1})")
        
        log_lines.append(f"🔸 <b>[{pair:02d}]</b> = {score:.2f}đ")
        log_lines.append(f"   ├ Cơ sở: {base_points}đ từ {', '.join(models_hit)}")
        
        # 2. Consensus Bonus
        c_count = pair_model_count.get(pair, 0)
        if c_count >= CONSENSUS_THRESHOLD_GOLD:
            log_lines.append(f"   ├ Đồng thuận: +{BONUS_GOLD}đ (Cả {c_count} model chốt)")
        elif c_count >= CONSENSUS_THRESHOLD_SILVER:
            log_lines.append(f"   ├ Đồng thuận: +{BONUS_SILVER}đ ({c_count} model chốt)")
            
        # 3. History Bonus
        h_count = recent_counts.get(pair, 0)
        if h_count >= 2:
            log_lines.append(f"   └ Lịch sử: {HISTORY_PENALTY_OVERDUE}đ (Nổ {h_count} lần/3 tuần)")
        elif h_count == 1:
            log_lines.append(f"   └ Lịch sử: +{HISTORY_BONUS_SWEETSPOT}đ (Nổ đúng 1 nhịp/3 tuần)")
        else:
            log_lines.append(f"   └ Lịch sử: +{HISTORY_BONUS_POTENTIAL}đ (Đang nén 3 tuần chưa ra)")
            
        scoring_log.append('\n'.join(log_lines))

    consensus_pairs_list = [p for p, count in pair_model_count.items() if count >= CONSENSUS_THRESHOLD_SILVER]

    return {
        'top_pairs': top_pairs,
        'contributing_models': list(set(contributing)),
        'ensemble_method': 'expert_borda_history_v2',
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
        "model_version": "ensemble_v3.1",
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

    return {
        "prediction_date": target_date.isoformat() if hasattr(target_date, "isoformat") else str(target_date),
        "region": region,
        "province": province,
        "model_name": model_result.get("model_name", "unknown"),
        "model_type": "rule_based" if model_result.get("model_name") in ("freq_gap", "markov") else "ml",
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
