"""
credibility_scorer.py — Model Credibility Scoring Engine

Pre-prediction engine that evaluates each sub-model's trustworthiness
across 6 dimensions BEFORE ensemble aggregation.

6 Credibility Dimensions:
  1. Recency MRR (30%)        — Decay-weighted recent hit quality
  2. Streak Momentum (25%)    — Hot/cold consecutive patterns
  3. NDCG@5 (15%)             — Ranking quality across lookback
  4. Consensus Accuracy (10%) — Does this model lead consensus to hits?
  5. Stability Index (10%)    — Is output consistent or random?
  6. Recovery Speed (10%)     — How fast does model bounce back after misses?

Usage:
    from src.scoring.credibility_scorer import compute_credibility_scores

    result = compute_credibility_scores(db, "XSMB", target_date)
    weights = result["credibility_weights"]
    confidences = result["confidence_map"]
"""

import math
import numpy as np
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from collections import defaultdict

from src.scoring.credibility_config import (
    DIM_WEIGHTS,
    RECENCY_DECAY,
    LOOKBACK_XSMB,
    LOOKBACK_XSMN,
    STREAK_SCORES,
    SMOOTHING,
    MIN_WEIGHT,
    MAX_WEIGHT,
    CONFIDENCE_FLOOR,
    CONFIDENCE_CEIL,
    MIN_EVALUATED_XSMB,
    MIN_EVALUATED_XSMN,
    COLD_START_CONFIDENCE,
)


# ─── Main API ────────────────────────────────────────────────────────────────

def compute_credibility_scores(
    db,
    region: str,
    target_date: date,
    lookback_draws: Optional[int] = None,
    config_weights: Optional[Dict[str, float]] = None,
) -> Dict:
    """
    Tính credibility scores cho tất cả models của region.

    Chạy TRƯỚC khi ensemble aggregation để cung cấp:
      - credibility_weights: dynamic weights cho Borda (thay auto_weight)
      - confidence_map: diagnostic confidence for audit/reporting
      - scorecard: chi tiết 6 chiều cho logging/Telegram

    Args:
        db: LotteryDB instance
        region: 'XSMB' | 'XSMN'
        target_date: ngày cần dự đoán (scores tính từ data TRƯỚC ngày này)
        lookback_draws: override số kỳ lookback
        config_weights: baseline weights từ scoring.yaml

    Returns:
        Dict with credibility_weights, confidence_map, scorecard, scoring_log
    """
    is_xsmb = region.upper() == "XSMB"

    from src.scoring.credibility_config import load_credibility_config_from_yaml
    yaml_cfg = load_credibility_config_from_yaml()

    if not yaml_cfg.get("enabled", True):
        return _empty_result(_get_default_weights(region), reason="disabled")

    if lookback_draws is None:
        if is_xsmb:
            lookback_draws = yaml_cfg.get("lookback_xsmb", LOOKBACK_XSMB)
        else:
            lookback_draws = yaml_cfg.get("lookback_xsmn", LOOKBACK_XSMN)

    # Load config weights as anchor
    if config_weights is None:
        config_weights = _get_default_weights(region)

    # ── Step 1: Query historical data ──
    target_weekday = None if is_xsmb else target_date.weekday()
    model_history = _query_model_history(
        db, region, target_date, lookback_draws, target_weekday=target_weekday
    )

    if not model_history:
        print("     ⚠️  Credibility: no model_predictions history found")
        return _empty_result(config_weights, reason="no model history")

    actual_tails = _query_actual_tails(
        db, region, target_date, lookback_draws, target_weekday=target_weekday
    )

    if not actual_tails:
        print("     ⚠️  Credibility: no actual tails found for evaluation")
        return _empty_result(config_weights, reason="no actual tails")

    ensemble_predictions = _query_ensemble_predictions(
        db, region, target_date, lookback_draws
    )

    # ── Step 2: Compute 6 dimensions per model ──
    # The configured weight map is the active-model registry. Historical rows
    # from retired aliases must not dilute the current ensemble normalization.
    all_models = set(config_weights)

    scorecard: Dict[str, Dict] = {}

    for model_name in all_models:
        # Extract this model's predictions aligned with dates
        model_preds = _extract_model_predictions(
            model_history, model_name, lookback_draws=lookback_draws
        )
        evaluated = _align_with_actuals(
            model_preds, actual_tails, ensemble_predictions=ensemble_predictions
        )

        min_evaluated = int(yaml_cfg.get(
            "min_evaluated_xsmb" if is_xsmb else "min_evaluated_xsmn",
            MIN_EVALUATED_XSMB if is_xsmb else MIN_EVALUATED_XSMN,
        ))
        if not _has_minimum_scope_samples(evaluated, min_evaluated, is_xsmb=is_xsmb):
            # Cold-start: not enough history
            scorecard[model_name] = _cold_start_scorecard(model_name)
            continue

        evaluated_scopes = _group_by_province(evaluated)
        prediction_scopes = _group_by_province(model_preds)
        recency_decay = float(yaml_cfg.get("recency_decay", RECENCY_DECAY))
        dim1 = _mean_scoped(
            evaluated_scopes,
            lambda rows: _compute_recency_mrr(rows, decay=recency_decay),
        )
        streak_results = [
            _compute_streak_momentum(rows) for rows in evaluated_scopes.values()
        ]
        dim2 = float(np.mean([score for score, _label in streak_results]))
        streak_type = streak_results[0][1] if len(streak_results) == 1 else "mixed"
        dim3 = _mean_scoped(evaluated_scopes, _compute_ndcg)
        dim4 = _mean_scoped(evaluated_scopes, _compute_consensus_accuracy)
        dim5 = _mean_scoped(prediction_scopes, _compute_stability_index)
        dim6 = _mean_scoped(evaluated_scopes, _compute_recovery_speed)

        dim_weights = {**DIM_WEIGHTS, **yaml_cfg.get("dim_weights", {})}
        composite = (
            dim_weights["recency_mrr"]        * dim1 +
            dim_weights["streak_momentum"]    * dim2 +
            dim_weights["ndcg_score"]         * dim3 +
            dim_weights["consensus_accuracy"] * dim4 +
            dim_weights["stability_index"]    * dim5 +
            dim_weights["recovery_speed"]     * dim6
        )

        scorecard[model_name] = {
            "recency_mrr":        round(dim1, 4),
            "streak_momentum":    round(dim2, 4),
            "ndcg_score":         round(dim3, 4),
            "consensus_accuracy": round(dim4, 4),
            "stability_index":    round(dim5, 4),
            "recovery_speed":     round(dim6, 4),
            "composite":          round(composite, 4),
            "streak_type":        streak_type,
            "total_evaluated":    len(evaluated),
            "cold_start":         False,
        }

    # ── Step 3: Convert composite → weights + confidence ──
    credibility_weights = _composite_to_weights(
        scorecard,
        config_weights,
        smoothing=float(yaml_cfg.get("smoothing", SMOOTHING)),
        min_weight=float(yaml_cfg.get("min_weight", MIN_WEIGHT)),
        max_weight=float(yaml_cfg.get("max_weight", MAX_WEIGHT)),
        max_weight_delta=float(yaml_cfg.get("max_weight_delta", 1.0)),
    )
    confidence_map = _composite_to_confidence(
        scorecard,
        floor=float(yaml_cfg.get("confidence_floor", CONFIDENCE_FLOOR)),
        ceil=float(yaml_cfg.get("confidence_ceil", CONFIDENCE_CEIL)),
    )

    # ── Step 4: Build human-readable log ──
    scoring_log = _build_scoring_log(scorecard, credibility_weights, confidence_map)

    # ── Step 5: Cache to DB (optional, fire-and-forget) ──
    try:
        _save_credibility_to_db(db, region, target_date, scorecard, credibility_weights, lookback_draws)
    except Exception as e:
        print(f"     ⚠️  Credibility DB cache failed: {e}")

    return {
        "credibility_weights": credibility_weights,
        "confidence_map": confidence_map,
        "scorecard": scorecard,
        "scoring_log": scoring_log,
        "using_dynamic_weights": any(not card.get("cold_start") for card in scorecard.values()),
    }


def _has_minimum_scope_samples(
    evaluated: List[Dict],
    minimum: int,
    *,
    is_xsmb: bool,
) -> bool:
    """Require enough observations for every province in the merged XSMN scope."""
    if is_xsmb:
        return len(evaluated) >= minimum
    counts: Dict[str, int] = defaultdict(int)
    for row in evaluated:
        if row.get("province"):
            counts[str(row["province"])] += 1
    return len(counts) >= 2 and min(counts.values(), default=0) >= minimum


def _group_by_province(rows: List[Dict]) -> Dict[str, List[Dict]]:
    """Group ordered observations without mixing station timelines."""
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("province") or "all")].append(row)
    return grouped


def _mean_scoped(
    grouped: Dict[str, List[Dict]],
    metric: Callable[[List[Dict]], float],
) -> float:
    """Compute a metric per station timeline, then average station scores."""
    values = [metric(rows) for rows in grouped.values() if rows]
    return float(np.mean(values)) if values else 0.0


# ─── Dimension 1: Recency-Weighted MRR ──────────────────────────────────────

def _compute_recency_mrr(evaluated: List[Dict], decay: float = RECENCY_DECAY) -> float:
    """
    MRR với decay exponential theo thời gian.

    Score = Σ(MRR_i × decay^i) / Σ(decay^i)
    i=0 là kỳ mới nhất, i=N-1 là kỳ xa nhất.
    """
    if not evaluated:
        return 0.0

    weighted_sum = 0.0
    weight_sum = 0.0

    for i, ev in enumerate(evaluated):
        # i=0 → most recent
        recency_weight = decay ** i
        mrr = ev.get("mrr", 0.0)
        weighted_sum += mrr * recency_weight
        weight_sum += recency_weight

    return weighted_sum / weight_sum if weight_sum > 0 else 0.0


# ─── Dimension 2: Streak Momentum ───────────────────────────────────────────

def _compute_streak_momentum(evaluated: List[Dict]) -> Tuple[float, str]:
    """
    Đếm consecutive hits/misses từ kỳ mới nhất.

    Returns:
        (momentum_score, streak_type_label)
    """
    if not evaluated:
        return 0.3, "neutral"

    # Count consecutive from most recent
    consecutive_hits = 0
    consecutive_misses = 0

    for ev in evaluated:
        hit = ev.get("hit", False)
        if hit:
            if consecutive_misses > 0:
                break  # streak broken
            consecutive_hits += 1
        else:
            if consecutive_hits > 0:
                break  # streak broken
            consecutive_misses += 1

    if consecutive_hits >= 3:
        return STREAK_SCORES["hot_3+"], "hot_3+"
    elif consecutive_hits == 2:
        return STREAK_SCORES["hot_2"], "hot_2"
    elif consecutive_hits == 1:
        return STREAK_SCORES["warm"], "warm"
    elif consecutive_misses == 1:
        return STREAK_SCORES["neutral"], "neutral"
    elif consecutive_misses == 2:
        return STREAK_SCORES["cold_2"], "cold_2"
    else:
        return STREAK_SCORES["cold_3+"], "cold_3+"


# ─── Dimension 3: NDCG@5 ────────────────────────────────────────────────────

def _compute_ndcg(evaluated: List[Dict]) -> float:
    """
    Mean NDCG@5 across evaluated draws.

    DCG@5 = Σ hit_binary(rank) / log2(rank + 1)
    IDCG@5 = 1.0 (best case: hit at rank 1)
    NDCG = DCG / IDCG
    """
    if not evaluated:
        return 0.0

    ndcg_scores = []
    for ev in evaluated:
        predicted = ev.get("predicted_pairs", [])
        actual = ev.get("actual_tails", set())
        dcg = 0.0
        for rank, pair in enumerate(predicted[:5], 1):
            if pair in actual:
                dcg += 1.0 / math.log2(rank + 1)
        # IDCG = 1/log2(2) = 1.0
        ndcg = min(dcg / 1.0, 1.0)
        ndcg_scores.append(ndcg)

    return float(np.mean(ndcg_scores)) if ndcg_scores else 0.0


# ─── Dimension 4: Consensus Accuracy ────────────────────────────────────────

def _compute_consensus_accuracy(evaluated: List[Dict]) -> float:
    """
    Tỉ lệ trúng khi model vote pair trùng với top-3 ensemble.

    Tính bằng cách: xem pair nào model chọn mà cũng nằm trong top-3 ensemble,
    rồi check pair đó có trúng actual không.

    Nếu không có dữ liệu ensemble → trả 0.5 (neutral).
    """
    participations = 0
    hits = 0

    for ev in evaluated:
        predicted = set(ev.get("predicted_pairs", [])[:3])
        ensemble_top3 = set(ev.get("ensemble_top3", []))
        actual = ev.get("actual_tails", set())

        if not ensemble_top3:
            continue

        # Pairs that this model voted AND made it to ensemble top-3
        overlap = predicted & ensemble_top3
        if overlap:
            participations += 1
            # Did any of those overlap pairs actually hit?
            if overlap & actual:
                hits += 1

    if participations == 0:
        return 0.5  # No consensus participation → neutral

    return hits / participations


# ─── Dimension 5: Stability Index ───────────────────────────────────────────

def _compute_stability_index(model_preds: List[Dict]) -> float:
    """
    Đo output consistency: overlap(top5_today, top5_yesterday).

    stability = 1 - variance(overlap ratios)
    High stability → reliable model.
    Low stability → erratic/random model.
    """
    if len(model_preds) < 2:
        return 0.5  # Not enough data → neutral

    overlaps = []
    for i in range(len(model_preds) - 1):
        curr_pairs = set(model_preds[i].get("predicted_pairs", [])[:5])
        prev_pairs = set(model_preds[i + 1].get("predicted_pairs", [])[:5])
        if curr_pairs and prev_pairs:
            overlap = len(curr_pairs & prev_pairs) / 5.0
            overlaps.append(overlap)

    if not overlaps:
        return 0.5

    mean_overlap = float(np.mean(overlaps))
    variance = float(np.var(overlaps))
    consistency = max(0.0, 1.0 - 4.0 * variance)
    # Constant zero-overlap is deterministic but not stable in a useful sense.
    stability = 0.5 * mean_overlap + 0.5 * consistency
    return stability


# ─── Dimension 6: Recovery Speed ────────────────────────────────────────────

def _compute_recovery_speed(evaluated: List[Dict]) -> float:
    """
    Trung bình số kỳ cần để hit lại sau miss streak.

    recovery_score = 1 / (1 + avg_recovery)
    """
    if not evaluated:
        return 0.5

    recovery_times = []
    in_miss_streak = False
    miss_count = 0

    # Evaluation rows arrive newest-first; recovery is a forward-time concept.
    for ev in reversed(evaluated):
        hit = ev.get("hit", False)
        if not hit:
            if not in_miss_streak:
                in_miss_streak = True
                miss_count = 1
            else:
                miss_count += 1
        else:
            if in_miss_streak:
                recovery_times.append(miss_count)
                in_miss_streak = False
                miss_count = 0

    if not recovery_times:
        # Never had a miss streak → perfect, or never recovered
        if in_miss_streak:
            return 0.1  # Still in miss streak, not recovered
        return 0.8  # Never missed → great

    avg_recovery = float(np.mean(recovery_times))
    return 1.0 / (1.0 + avg_recovery)


# ─── Weight & Confidence Conversion ─────────────────────────────────────────

def _composite_to_weights(
    scorecard: Dict[str, Dict],
    config_weights: Dict[str, float],
    *,
    smoothing: float = SMOOTHING,
    min_weight: float = MIN_WEIGHT,
    max_weight: float = MAX_WEIGHT,
    max_weight_delta: float = 1.0,
) -> Dict[str, float]:
    """
    Convert composite scores → final weights.

    Algorithm:
      1. Normalize composites → sum = 1.0 (raw credibility weights)
      2. Smooth: final = α × credibility + (1-α) × config_weight
      3. Clamp: [MIN_WEIGHT, MAX_WEIGHT]
      4. Re-normalize → sum = 1.0
    """
    # Step 1: Normalize composites
    composites = {}
    for model, card in scorecard.items():
        if card.get("cold_start"):
            composites[model] = config_weights.get(model, 0.10)
        else:
            composites[model] = card["composite"]

    total_comp = sum(composites.values())
    if total_comp < 1e-10:
        return config_weights.copy()

    cred_weights = {m: c / total_comp for m, c in composites.items()}

    # Step 2: Smooth with config anchor
    final = {}
    for model in set(list(cred_weights.keys()) + list(config_weights.keys())):
        cred_w = cred_weights.get(model, 0.0)
        conf_w = config_weights.get(model, 0.10)
        proposed = smoothing * cred_w + (1.0 - smoothing) * conf_w
        final[model] = max(
            conf_w - max_weight_delta,
            min(conf_w + max_weight_delta, proposed),
        )

    # Step 3: Clamp
    for model in final:
        final[model] = max(min_weight, min(max_weight, final[model]))

    # Step 4: Re-normalize
    total = sum(final.values())
    if total > 0:
        final = {m: round(w / total, 4) for m, w in final.items()}

    return final


def _composite_to_confidence(
    scorecard: Dict[str, Dict],
    *,
    floor: float = CONFIDENCE_FLOOR,
    ceil: float = CONFIDENCE_CEIL,
) -> Dict[str, float]:
    """
    Convert composite scores → confidence multipliers.

    Maps composite [0, 1] → confidence [CONFIDENCE_FLOOR, CONFIDENCE_CEIL].
    This is diagnostic metadata. Dynamic influence is already represented in
    ``credibility_weights`` and must not be applied twice in the ensemble.
    """
    confidence_map = {}
    for model, card in scorecard.items():
        if card.get("cold_start"):
            confidence_map[model] = COLD_START_CONFIDENCE
        else:
            composite = card["composite"]
            # Linear map: composite 0→FLOOR, 1→CEIL
            conf = floor + composite * (ceil - floor)
            confidence_map[model] = round(conf, 3)
    return confidence_map


# ─── Data Query Helpers ──────────────────────────────────────────────────────

def _query_model_history(
    db,
    region: str,
    target_date: date,
    lookback_draws: int,
    target_weekday: Optional[int] = None,
) -> Dict[str, List[Dict]]:
    """
    Query model_predictions grouped by date.

    Returns:
        Dict[date_str, List[prediction_dicts]]
    """
    # Estimate date range (generous — accounts for weekends/holidays)
    day_multiplier = 2 if region.upper() == "XSMB" else 8
    start_date = target_date - timedelta(days=lookback_draws * day_multiplier + 14)

    try:
        q = db.supabase.table("model_predictions") \
            .select("prediction_date,province,model_name,pair_1,pair_2,pair_3,pair_4,pair_5,status,hit,matched_pairs") \
            .eq("region", region) \
            .eq("status", "success") \
            .gte("prediction_date", start_date.isoformat()) \
            .lt("prediction_date", target_date.isoformat()) \
            .order("prediction_date", desc=True)

        rows = _execute_paged(q)

        # Group by date
        by_date: Dict[str, List[Dict]] = {}
        for r in rows:
            d = r["prediction_date"]
            if target_weekday is not None and date.fromisoformat(d).weekday() != target_weekday:
                continue
            if d not in by_date:
                by_date[d] = []
            by_date[d].append(r)

        return by_date

    except Exception as e:
        print(f"     ⚠️  Credibility history query failed: {e}")
        return {}


def _query_actual_tails(
    db,
    region: str,
    target_date: date,
    lookback_draws: int,
    target_weekday: Optional[int] = None,
) -> Dict[Any, set]:
    """
    Query actual tails per date for verification.

    Returns:
        Dict[date_str, set of tail ints]
    """
    day_multiplier = 2 if region.upper() == "XSMB" else 8
    start_date = target_date - timedelta(days=lookback_draws * day_multiplier + 14)

    try:
        q = db.supabase.table("tails_2d") \
            .select("draw_date,province,tail_2d") \
            .eq("region", region) \
            .gte("draw_date", start_date.isoformat()) \
            .lt("draw_date", target_date.isoformat())

        if region.upper() == "XSMB":
            q = q.is_("province", "null")

        rows = _execute_paged(q)

        date_tails: Dict[Any, set] = {}
        for r in rows:
            d = r["draw_date"]
            if target_weekday is not None and date.fromisoformat(d).weekday() != target_weekday:
                continue
            key: Any = d if region.upper() == "XSMB" else (d, r.get("province"))
            date_tails.setdefault(key, set()).add(int(r["tail_2d"]))

        return date_tails

    except Exception as e:
        print(f"     ⚠️  Credibility actual tails query failed: {e}")
        return {}


def _execute_paged(query, page_size: int = 1000) -> List[Dict]:
    """Execute a Supabase query without silently accepting PostgREST's row cap."""
    rows: List[Dict] = []
    offset = 0
    while True:
        page = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def _query_ensemble_predictions(
    db,
    region: str,
    target_date: date,
    lookback_draws: int,
) -> Dict[str, List[int]]:
    """
    Query prediction_results (ensemble top-3) per date.

    Returns:
        Dict[date_str, list of top-3 pair ints]
    """
    day_multiplier = 2 if region.upper() == "XSMB" else 8
    start_date = target_date - timedelta(days=lookback_draws * day_multiplier + 14)

    try:
        q = db.supabase.table("prediction_results") \
            .select("prediction_date,pair_1,pair_2,pair_3") \
            .eq("region", region) \
            .gte("prediction_date", start_date.isoformat()) \
            .lt("prediction_date", target_date.isoformat())

        if region.upper() == "XSMB":
            q = q.is_("province", "null")
        else:
            # XSMN ensemble is province="all"
            q = q.eq("province", "all")

        result = q.execute()
        rows = result.data or []

        ensemble_top3: Dict[str, List[int]] = {}
        for r in rows:
            d = r["prediction_date"]
            pairs = [r.get("pair_1"), r.get("pair_2"), r.get("pair_3")]
            ensemble_top3[d] = [p for p in pairs if p is not None]

        return ensemble_top3

    except Exception as e:
        print(f"     ⚠️  Credibility ensemble query failed: {e}")
        return {}


# ─── Data Transformation Helpers ─────────────────────────────────────────────

def _extract_model_predictions(
    model_history: Dict[str, List[Dict]],
    model_name: str,
    lookback_draws: Optional[int] = None,
) -> List[Dict]:
    """
    Extract and sort a specific model's predictions (most recent first).

    Returns:
        List[Dict] with keys: date, predicted_pairs
    """
    result = []
    for date_str, records in model_history.items():
        model_records = [r for r in records if r["model_name"] == model_name]
        for r in model_records:
            predicted_pairs = []
            for rank in range(1, 6):
                value = r.get(f"pair_{rank}")
                if value is not None and int(value) not in predicted_pairs:
                    predicted_pairs.append(int(value))
            result.append({
                "date": date_str,
                "province": r.get("province"),
                "predicted_pairs": predicted_pairs,
                "hit": bool(r.get("hit")),
                "matched_pairs": list(set(r.get("matched_pairs") or [])),
            })

    # Sort by date descending (most recent first)
    result.sort(key=lambda x: x["date"], reverse=True)
    if lookback_draws is None:
        return result

    limited: List[Dict] = []
    counts: Dict[str, int] = defaultdict(int)
    for row in result:
        province_key = str(row.get("province") or "all")
        if counts[province_key] >= lookback_draws:
            continue
        limited.append(row)
        counts[province_key] += 1
    return limited


def _align_with_actuals(
    model_preds: List[Dict],
    actual_tails: Dict[Any, set],
    ensemble_predictions: Optional[Dict[str, List[int]]] = None,
) -> List[Dict]:
    """
    Align model predictions with actual tails, computing MRR per draw.

    Only includes draws where actual results exist.
    Results sorted most recent first.
    """
    evaluated = []
    for pred in model_preds:
        d = pred["date"]
        province = pred.get("province")
        actual_key: Any = (d, province) if province is not None else d
        if actual_key not in actual_tails:
            continue

        actual = actual_tails[actual_key]
        predicted = pred["predicted_pairs"]

        # Compute MRR@3
        mrr = 0.0
        for rank, pair in enumerate(predicted[:3], 1):
            if pair in actual:
                mrr = 1.0 / rank
                break

        # Hit@5
        hit = any(p in actual for p in predicted[:5])

        evaluated.append({
            "date": d,
            "province": province,
            "predicted_pairs": predicted,
            "actual_tails": actual,
            "mrr": mrr,
            "hit": hit,
            "ensemble_top3": (ensemble_predictions or {}).get(d, []),
        })

    return evaluated


def _cold_start_scorecard(model_name: str) -> Dict:
    """Create a neutral scorecard for models with insufficient history."""
    return {
        "recency_mrr":        0.0,
        "streak_momentum":    0.30,
        "ndcg_score":         0.0,
        "consensus_accuracy": 0.50,
        "stability_index":    0.50,
        "recovery_speed":     0.50,
        "composite":          0.30,
        "streak_type":        "cold_start",
        "total_evaluated":    0,
        "cold_start":         True,
    }


def _get_default_weights(region: str) -> Dict[str, float]:
    """Get config weights for region (fallback to hardcoded defaults)."""
    try:
        if region.upper() == "XSMB":
            from src.xsmb_ensemble.ensemble_engine import ACTIVE_MODEL_NAMES, DEFAULT_WEIGHTS
            active = {name: float(DEFAULT_WEIGHTS[name]) for name in ACTIVE_MODEL_NAMES}
            total = sum(active.values())
            return {name: weight / total for name, weight in active.items()}
        from src.xsmn_ensemble.ensemble_engine import _get_region_config
        configured = _get_region_config("XSMN")["weights"]
        active_models = ("frequency", "gap_overdue", "markov", "xgboost_core", "lstm", "cdm")
        return {name: float(configured[name]) for name in active_models}
    except (ImportError, KeyError):
        if region.upper() == "XSMB":
            return {
                "frequency": 0.10, "gap_overdue": 0.10,
                "markov": 0.13, "xgboost_core": 0.17,
                "lstm": 0.12, "bayesian": 0.12, "cyclic": 0.10,
                "stats_freq_gap": 0.09, "chisquare_gof": 0.08,
                "chisquare_independence": 0.09,
            }
        return {
            "frequency": 1.0 / 6.0, "gap_overdue": 1.0 / 6.0,
            "markov": 1.0 / 6.0, "xgboost_core": 1.0 / 6.0,
            "lstm": 1.0 / 6.0, "cdm": 1.0 / 6.0,
        }


# ─── DB Cache ────────────────────────────────────────────────────────────────

def _save_credibility_to_db(
    db,
    region: str,
    target_date: date,
    scorecard: Dict[str, Dict],
    credibility_weights: Dict[str, float],
    lookback_draws: int,
) -> None:
    """Save credibility scores to model_credibility table (cache)."""
    for model_name, card in scorecard.items():
        row = {
            "score_date":          target_date.isoformat(),
            "region":              region,
            "model_name":          model_name,
            "recency_mrr":         card.get("recency_mrr"),
            "streak_momentum":     card.get("streak_momentum"),
            "ndcg_score":          card.get("ndcg_score"),
            "consensus_accuracy":  card.get("consensus_accuracy"),
            "stability_index":     card.get("stability_index"),
            "recovery_speed":      card.get("recovery_speed"),
            "composite_score":     card.get("composite", 0.0),
            "credibility_weight":  credibility_weights.get(model_name, 0.0),
            "lookback_draws":      lookback_draws,
            "total_evaluated":     card.get("total_evaluated", 0),
            "streak_type":         card.get("streak_type"),
        }
        try:
            db.supabase.table("model_credibility").upsert(
                row,
                on_conflict="score_date,region,model_name"
            ).execute()
        except Exception:
            pass  # Fire-and-forget cache


# ─── Scoring Log ─────────────────────────────────────────────────────────────

def _build_scoring_log(
    scorecard: Dict[str, Dict],
    weights: Dict[str, float],
    confidences: Dict[str, float],
) -> str:
    """Build human-readable credibility scorecard for console + Telegram."""
    MODEL_DISPLAY = {
        "frequency": "Freq", "gap_overdue": "Gap", "markov": "Markov²",
        "xgboost_core": "XGB", "lstm": "BiLSTM", "bayesian": "Bayes",
        "cyclic": "Cyclic",
        "stats_freq_gap": "StatsFG", "chisquare_gof": "ChiGOF",
        "chisquare_independence": "ChiInd",
    }

    STREAK_ICONS = {
        "hot_3+": "🔥", "hot_2": "🔥", "warm": "🟢",
        "neutral": "🟡", "cold_2": "❄️", "cold_3+": "❄️",
        "cold_start": "⚫",
    }

    lines = ["  📊 Model Credibility Scorecard:"]

    # Sort by composite score descending
    sorted_models = sorted(
        scorecard.items(),
        key=lambda x: x[1].get("composite", 0),
        reverse=True,
    )

    for model_name, card in sorted_models:
        display = MODEL_DISPLAY.get(model_name, model_name)
        composite = card.get("composite", 0)
        streak = card.get("streak_type", "neutral")
        icon = STREAK_ICONS.get(streak, "🟡")
        w = weights.get(model_name, 0)
        conf = confidences.get(model_name, 1.0)

        if card.get("cold_start"):
            lines.append(f"     {icon} {display:8s}: N/A  (cold-start)        w={w:.2f} conf={conf:.2f}")
        else:
            lines.append(f"     {icon} {display:8s}: {composite:.2f} ({streak:8s})  w={w:.2f} conf={conf:.2f}")

    return "\n".join(lines)


# ─── Empty Result ────────────────────────────────────────────────────────────

def _empty_result(config_weights: Dict[str, float], reason: str = "no history") -> Dict:
    """Return empty result when no history is available."""
    return {
        "credibility_weights": config_weights.copy(),
        "confidence_map": {m: 1.0 for m in config_weights},
        "scorecard": {},
        "scoring_log": f"  ⚠️  Credibility: using fixed weights ({reason})",
        "using_dynamic_weights": False,
    }
