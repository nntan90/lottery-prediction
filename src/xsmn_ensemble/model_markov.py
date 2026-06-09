"""
model_markov.py — Model B: Second-Order Markov Chain Transition Probability
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Input:  tails_2d data (250 kỳ gần nhất per province)
Output: Top 5 cặp số + probability

Logic:
  1. Second-order: P(pair_j | kỳ t-1 context, kỳ t-2 context)
  2. Decay-weighted: kỳ gần weight cao hơn
  3. Multi-context: avg P(j | context_t-1) + 0.4 * P(j | context_t-2)
"""

import numpy as np
import time
from datetime import date
from typing import Dict, Optional, List
from collections import defaultdict

def _load_tails_sequential(
    db,
    region: str,
    province: Optional[str] = None,
    n_draws: int = 250,
    before_date: Optional[date] = None,
) -> List[frozenset]:
    """
    Lấy N kỳ quay gần nhất, trả về list of frozensets (mỗi set = tails 1 kỳ).
    Sorted by draw_date ASC (cũ → mới).
    Sử dụng pagination để vượt qua limit 1000 rows.
    """
    limit = 1000
    offset = 0
    all_rows = []

    while True:
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

        query = query.range(offset, offset + limit - 1)
        chunk = query.execute().data

        if not chunk:
            break

        all_rows.extend(chunk)

        unique_dates = set(r["draw_date"] for r in all_rows)
        if len(unique_dates) >= n_draws:
            break

        if len(chunk) < limit:
            break

        offset += limit

    if not all_rows:
        return []

    date_groups = defaultdict(set)
    for r in all_rows:
        date_groups[r["draw_date"]].add(r["tail_2d"])

    # Sort by date ASC, take last n_draws
    sorted_dates = sorted(date_groups.keys())[-n_draws:]
    return [frozenset(date_groups[d]) for d in sorted_dates]


def predict_markov(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 250,
    top_n: int = 5,
    region: str = "XSMN",
) -> Dict:
    """
    Model B: Second-order Markov Chain cho XSMN.
    """
    start_ms = time.time()
    DECAY_LAMBDA = 0.95
    TOP_K_COMPRESS = 15

    try:
        draws = _load_tails_sequential(db, region, province, n_draws, before_date=target_date)
        n = len(draws)

        if n < 15:
            return _error_result("markov", province, n,
                                 f"Không đủ lịch sử: {n} kỳ (cần ≥ 15)", start_ms)

        # ── First-order transition matrix ──
        transition_1st = np.zeros((100, 100), dtype=float)

        for t in range(n - 1):
            current_set = draws[t]
            next_set = draws[t + 1]
            weight = DECAY_LAMBDA ** (n - 2 - t)

            for i in current_set:
                if 0 <= i <= 99:
                    for j in next_set:
                        if 0 <= j <= 99:
                            transition_1st[i][j] += weight

        # Normalize
        row_sums_1 = transition_1st.sum(axis=1, keepdims=True)
        row_sums_1[row_sums_1 == 0] = 1
        prob_1st = transition_1st / row_sums_1

        # ── Second-order transition ──
        transition_2nd: dict[tuple[int, int], np.ndarray] = {}

        if n >= 3:
            for t in range(n - 2):
                set_t0 = draws[t]
                set_t1 = draws[t + 1]
                set_t2 = draws[t + 2]

                weight = DECAY_LAMBDA ** (n - 3 - t)

                ctx0 = sorted([p for p in set_t0 if 0 <= p <= 99])[:TOP_K_COMPRESS]
                ctx1 = sorted([p for p in set_t1 if 0 <= p <= 99])[:TOP_K_COMPRESS]

                for i in ctx0:
                    for k in ctx1:
                        state = (i, k)
                        if state not in transition_2nd:
                            transition_2nd[state] = np.zeros(100, dtype=float)
                        for j in set_t2:
                            if 0 <= j <= 99:
                                transition_2nd[state][j] += weight

            for state in transition_2nd:
                s = transition_2nd[state].sum()
                if s > 0:
                    transition_2nd[state] /= s

        # ── Predict ──
        context_t1 = draws[-1] if draws else set()
        context_t2 = draws[-2] if len(draws) >= 2 else set()

        pred_1st = np.zeros(100, dtype=float)
        valid_ctx1 = [i for i in context_t1 if 0 <= i <= 99]
        if valid_ctx1:
            for i in valid_ctx1:
                pred_1st += prob_1st[i]
            pred_1st /= len(valid_ctx1)

        pred_2nd = np.zeros(100, dtype=float)
        n_2nd_states = 0
        if transition_2nd and context_t1 and context_t2:
            ctx0_comp = sorted([p for p in context_t2 if 0 <= p <= 99])[:TOP_K_COMPRESS]
            ctx1_comp = sorted([p for p in context_t1 if 0 <= p <= 99])[:TOP_K_COMPRESS]

            for i in ctx0_comp:
                for k in ctx1_comp:
                    state = (i, k)
                    if state in transition_2nd:
                        pred_2nd += transition_2nd[state]
                        n_2nd_states += 1

            if n_2nd_states > 0:
                pred_2nd /= n_2nd_states

        if n_2nd_states > 0:
            predictions = 0.6 * pred_1st + 0.4 * pred_2nd
        else:
            predictions = pred_1st

        top_indices = np.argsort(predictions)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(predictions[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "markov",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("markov", province, 0, str(e), start_ms)

def _error_result(model_name: str, province: Optional[str],
                  n_draws: int, error_msg: str, start_ms: float) -> Dict:
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
