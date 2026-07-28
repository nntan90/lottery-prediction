"""
model_markov.py — Model C: Second-Order Markov Chain (XSMB v4)

Enhanced Markov Chain cho XSMB:
  - Second-order: P(pair_j | kỳ t-1 context, kỳ t-2 context)
    State compression: chỉ track top-20 pairs per draw → 400×100 matrix
  - Sequential-conditioned: ma trận transition theo các kỳ liên tiếp không phân biệt thứ
  - Decay-weighted: kỳ gần weight cao hơn (exponential decay λ=0.95)
  - Multi-context: avg P(j | context_t-1) + 0.3 × P(j | context_t-2)

XSMB có ~27 tails/kỳ → mỗi kỳ chỉ lấy top-K pairs frequent nhất
để giảm state space cho second-order Markov.
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional, List
from collections import defaultdict

from src.xsmb_ensemble.data_utils import _load_tails_by_draws, _load_tails_by_weekday


def _compress_context_by_frequency(
    values,
    context_frequency: np.ndarray,
    top_k: int,
) -> list[int]:
    """Select context pairs by evidence strength with deterministic ties."""
    valid = {int(pair) for pair in values if 0 <= int(pair) <= 99}
    return sorted(
        valid,
        key=lambda pair: (-context_frequency[pair], pair),
    )[:top_k]


def _load_tails_sequential(
    db,
    region: str = "XSMB",
    province: Optional[str] = None,
    n_draws: int = 180,
    before_date: Optional[date] = None,
) -> List[frozenset]:
    """
    Lấy N kỳ quay gần nhất, trả về list of frozensets.
    Sorted by draw_date ASC (cũ → mới).
    """
    query = db.supabase.table("tails_2d") \
        .select("draw_date,tail_2d") \
        .eq("region", region) \
        .order("draw_date", desc=True)

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    if before_date:
        query = query.lt("draw_date", before_date.isoformat())

    query = query.limit(n_draws * 30)
    rows = query.execute().data

    if not rows:
        return []

    date_groups: dict[str, set] = defaultdict(set)
    for r in rows:
        date_groups[r["draw_date"]].add(r["tail_2d"])

    sorted_dates = sorted(date_groups.keys())[-n_draws:]
    return [frozenset(date_groups[d]) for d in sorted_dates]



def predict_markov(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model C: Second-order Markov Chain cho XSMB.

    Xây dựng 2 loại transition matrix:
    1. First-order: P(pair_j | pair_i ∈ kỳ t-1) — standard
    2. Second-order compressed: P(pair_j | top_pair ∈ kỳ t-1, top_pair ∈ kỳ t-2)
       State compression: chỉ track top-15 frequent pairs mỗi kỳ

    Final prediction = 0.6 × first_order + 0.4 × second_order

    Args:
        db: LotteryDB instance
        province: None cho XSMB
        target_date: ngày cần dự đoán
        n_draws: số kỳ lookback
        top_n: số cặp top-N output
        region: always 'XSMB'

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()
    DECAY_LAMBDA = 0.95
    TOP_K_COMPRESS = 15  # state compression: chỉ track top-15 mỗi kỳ

    try:
        draws = _load_tails_sequential(db, region, province, n_draws, before_date=target_date)
        n = len(draws)

        if n < 15:
            return _error_result("markov", province, n,
                                 f"Không đủ lịch sử: {n} kỳ (cần ≥ 15)", start_ms)

        # ── First-order transition matrix (decay-weighted) ──
        transition_1st = np.zeros((100, 100), dtype=float)

        for t in range(n - 1):
            current_set = draws[t]
            next_set = draws[t + 1]
            # Decay weight: kỳ gần nhất weight = 1.0, xa hơn giảm dần
            weight = DECAY_LAMBDA ** (n - 2 - t)

            for i in current_set:
                if 0 <= i <= 99:
                    for j in next_set:
                        if 0 <= j <= 99:
                            transition_1st[i][j] += weight

        # Normalize rows → probability
        row_sums_1 = transition_1st.sum(axis=1, keepdims=True)
        row_sums_1[row_sums_1 == 0] = 1
        prob_1st = transition_1st / row_sums_1

        # ── Second-order transition (compressed state space) ──
        # State = (top_pair_from_t-2, top_pair_from_t-1) → pair_j
        # Compress: mỗi kỳ chỉ lấy top-K pairs (frequency-based qua context)
        # Dùng dict thay vì full matrix để tiết kiệm RAM
        transition_2nd: dict[tuple[int, int], np.ndarray] = {}

        # Compression must be driven by evidence, not by the numeric value of
        # a pair.  The previous ``sorted(... )[:K]`` implementation silently
        # favoured 00..14 whenever a draw contained more than K unique tails.
        # Use decay-weighted global frequency as a deterministic context rank.
        context_frequency = np.zeros(100, dtype=float)
        for draw_index, draw in enumerate(draws):
            draw_weight = DECAY_LAMBDA ** (n - 1 - draw_index)
            for pair in draw:
                if 0 <= pair <= 99:
                    context_frequency[pair] += draw_weight

        if n >= 3:
            for t in range(n - 2):
                set_t0 = draws[t]       # kỳ t-2
                set_t1 = draws[t + 1]   # kỳ t-1
                set_t2 = draws[t + 2]   # kỳ t (target)

                weight = DECAY_LAMBDA ** (n - 3 - t)

                # Compress: chỉ lấy top-K pairs mỗi kỳ
                ctx0 = _compress_context_by_frequency(
                    set_t0, context_frequency, TOP_K_COMPRESS
                )
                ctx1 = _compress_context_by_frequency(
                    set_t1, context_frequency, TOP_K_COMPRESS
                )

                for i in ctx0:
                    for k in ctx1:
                        state = (i, k)
                        if state not in transition_2nd:
                            transition_2nd[state] = np.zeros(100, dtype=float)
                        for j in set_t2:
                            if 0 <= j <= 99:
                                transition_2nd[state][j] += weight

            # Normalize second-order
            for state in transition_2nd:
                s = transition_2nd[state].sum()
                if s > 0:
                    transition_2nd[state] /= s

        # ── Predict: combine first + second order ──
        context_t1 = draws[-1] if draws else set()  # kỳ mới nhất
        context_t2 = draws[-2] if len(draws) >= 2 else set()  # kỳ trước đó

        # First-order prediction
        pred_1st = np.zeros(100, dtype=float)
        valid_ctx1 = [i for i in context_t1 if 0 <= i <= 99]
        if valid_ctx1:
            for i in valid_ctx1:
                pred_1st += prob_1st[i]
            pred_1st /= len(valid_ctx1)

        # Second-order prediction
        pred_2nd = np.zeros(100, dtype=float)
        n_2nd_states = 0
        if transition_2nd and context_t1 and context_t2:
            ctx0_comp = _compress_context_by_frequency(
                context_t2, context_frequency, TOP_K_COMPRESS
            )
            ctx1_comp = _compress_context_by_frequency(
                context_t1, context_frequency, TOP_K_COMPRESS
            )

            for i in ctx0_comp:
                for k in ctx1_comp:
                    state = (i, k)
                    if state in transition_2nd:
                        pred_2nd += transition_2nd[state]
                        n_2nd_states += 1

            if n_2nd_states > 0:
                pred_2nd /= n_2nd_states

        # ── Combine: 60% first-order + 40% second-order ──
        if n_2nd_states > 0:
            predictions = 0.6 * pred_1st + 0.4 * pred_2nd
        else:
            predictions = pred_1st  # fallback to first-order only

        # Top N
        top_indices = np.argsort(predictions)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(predictions[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "markov",
            "source_family": "transition",
            "province": province,
            "top_pairs": top_pairs,
            "score_vector": [float(score) for score in predictions],
            "score_semantics": "relative_score_uncalibrated",
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("markov", province, 0, str(e), start_ms)


def _error_result(model_name: str, province: Optional[str],
                  n_draws: int, error_msg: str, start_ms: float) -> Dict:
    """Helper tạo error result dict."""
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
