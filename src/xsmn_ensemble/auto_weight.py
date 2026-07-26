"""
auto_weight.py — Auto-Weight Tuning Engine (XSMN v3.3)

Tự động điều chỉnh weights cho 7 sub-models dựa trên backtest performance:
  1. Query model_predictions N ngày gần nhất
  2. Với mỗi model: tính hit_rate@3, hit_rate@5, MRR
  3. Normalize performance → weights
  4. Smoothing: new = α × perf_weight + (1-α) × current_weight
     (tránh dao động mạnh)

Auto-apply: cập nhật weights trong ensemble mỗi lần chạy predict
Hoặc suggest-only: gửi Telegram báo cáo weights đề xuất

Usage:
  Gọi compute_optimal_weights() trước compute_xsmb_ensemble()
  → truyền weights vào ensemble function
"""

import numpy as np
from datetime import date, timedelta
from typing import Dict, Optional, List


def compute_optimal_weights(
    db,
    lookback_days: int = 30,
    region: str = "XSMN",
    current_weights: Optional[dict[str, float]] = None,
    smoothing: float = 0.7,
    min_weight: float = 0.05,
    max_weight: float = 0.35,
) -> dict[str, float]:
    """
    Tính optimal weights cho mỗi sub-model dựa trên backtest.

    Algorithm:
      1. Lấy model_predictions trong lookback_days
      2. Match với prediction_results (actual hits)
      3. Tính hit_rate@5 cho mỗi model
      4. Normalize → perf_weights (sum = 1.0)
      5. Smooth: new = α × perf + (1-α) × current
      6. Clamp: [min_weight, max_weight]

    Args:
        db: LotteryDB instance
        lookback_days: số ngày nhìn lại
        region: 'XSMB'
        current_weights: weights hiện tại (từ config)
        smoothing: smoothing factor (0.7 = 70% performance-driven)
        min_weight: minimum weight per model
        max_weight: maximum weight per model

    Returns:
        dict[str, float] — optimized weights
    """
    from src.xsmn_ensemble.ensemble_engine import DEFAULT_WEIGHTS

    if current_weights is None:
        current_weights = DEFAULT_WEIGHTS.copy()

    end_date = date.today()
    start_date = end_date - timedelta(days=lookback_days)

    # 1. Lấy model_predictions
    model_preds = _query_model_predictions(db, region, start_date, end_date)

    if not model_preds:
        print("     ⚠️  Auto-weight: không có model_predictions để tune")
        return current_weights

    # 2. Lấy actual tails (từ tails_2d) cho verification
    actual_tails = _query_actual_tails(db, region, start_date, end_date)

    if not actual_tails:
        print("     ⚠️  Auto-weight: không có actual results để verify")
        return current_weights

    # 3. Tính hit_rate cho mỗi model
    model_performance = _compute_model_performance(model_preds, actual_tails)
    model_performance = {
        model_name: performance
        for model_name, performance in model_performance.items()
        if model_name in current_weights
    }

    if not model_performance:
        return current_weights

    # 4. Normalize → performance weights
    total_perf = sum(model_performance.values())
    if total_perf < 1e-10:
        return current_weights

    perf_weights = {}
    for model_name, perf in model_performance.items():
        perf_weights[model_name] = perf / total_perf

    # 5. Smooth: blend với current weights
    optimal = {}
    all_models = set(list(current_weights.keys()) + list(perf_weights.keys()))

    for model_name in all_models:
        perf_w = perf_weights.get(model_name, 0.0)
        curr_w = current_weights.get(model_name, 0.10)

        # Smoothing: α × performance + (1-α) × current
        new_w = smoothing * perf_w + (1.0 - smoothing) * curr_w
        optimal[model_name] = new_w

    # 6. Clamp [min, max] rồi normalize
    for model_name in optimal:
        optimal[model_name] = max(min_weight, min(max_weight, optimal[model_name]))

    # Re-normalize
    total = sum(optimal.values())
    if total > 0:
        for model_name in optimal:
            optimal[model_name] = round(optimal[model_name] / total, 4)

    return optimal


def _query_model_predictions(
    db,
    region: str,
    start_date: date,
    end_date: date,
) -> List[Dict]:
    """Query model_predictions table."""
    try:
        q = db.supabase.table("model_predictions") \
            .select("prediction_date,model_name,pair_1,pair_2,pair_3,pair_4,pair_5,status") \
            .eq("region", region) \
            .eq("status", "success") \
            .gte("prediction_date", start_date.isoformat()) \
            .lte("prediction_date", end_date.isoformat()) \
            .order("prediction_date")

        result = q.execute()
        return result.data or []
    except Exception as e:
        print(f"     ⚠️  Auto-weight query failed: {e}")
        return []


def _query_actual_tails(
    db,
    region: str,
    start_date: date,
    end_date: date,
) -> dict[str, set]:
    """
    Query actual tails per date.

    Returns:
        dict[date_str, set of tail ints]
    """
    try:
        q = db.supabase.table("tails_2d") \
            .select("draw_date,tail_2d") \
            .eq("region", region) \
            .gte("draw_date", start_date.isoformat()) \
            .lte("draw_date", end_date.isoformat())

        # XSMN: query records for specific region (provinces exist, but we aggregate across all provinces)

        result = q.execute()
        rows = result.data or []

        date_tails: dict[str, set] = {}
        for r in rows:
            d = r["draw_date"]
            if d not in date_tails:
                date_tails[d] = set()
            date_tails[d].add(int(r["tail_2d"]))

        return date_tails
    except Exception as e:
        print(f"     ⚠️  Auto-weight actual tails query failed: {e}")
        return {}


def _compute_model_performance(
    model_preds: List[Dict],
    actual_tails: dict[str, set],
) -> dict[str, float]:
    """
    Tính MRR@3 (Mean Reciprocal Rank) cho mỗi model.

    MRR@3 = 1/rank nếu rank <= 3, ngược lại = 0.
    Giúp phân biệt chất lượng: hit ở rank 1 (1.0) tốt hơn rank 3 (0.33).

    Returns:
        dict[model_name, hit_rate]
    """
    from collections import defaultdict

    model_hits: dict[str, int] = defaultdict(int)
    model_total: dict[str, int] = defaultdict(int)

    for pred in model_preds:
        pred_date = pred["prediction_date"]
        model_name = pred["model_name"]

        # Chỉ đánh giá nếu có actual result
        if pred_date not in actual_tails:
            continue

        actual = actual_tails[pred_date]
        model_total[model_name] += 1

        # Check MRR@3
        mrr = 0.0
        predicted_pairs = []
        for k in ["pair_1", "pair_2", "pair_3"]:
            if pred.get(k) is not None:
                predicted_pairs.append(int(pred[k]))
                
        for rank, p in enumerate(predicted_pairs, 1):
            if p in actual:
                mrr = 1.0 / rank
                break # Chỉ tính hit cao nhất cho MRR
                
        model_hits[model_name] += mrr

    # Compute hit rates
    performance = {}
    for model_name in model_total:
        total = model_total[model_name]
        hits = model_hits.get(model_name, 0)
        hit_rate = hits / total if total > 0 else 0.0
        performance[model_name] = hit_rate

    if performance:
        print(f"     📊 Auto-weight performance ({len(actual_tails)} days):")
        for m, score in sorted(performance.items(), key=lambda x: x[1], reverse=True):
            total = model_total[m]
            mrr_sum = model_hits.get(m, 0)
            print(f"        {m}: MRR {score:.3f} (sum={mrr_sum:.1f}/{total})")

    return performance


def format_weight_report(
    current_weights: dict[str, float],
    optimal_weights: dict[str, float],
    performance: Optional[dict[str, float]] = None,
) -> str:
    """
    Format weight comparison cho Telegram report.

    Returns:
        str — formatted Telegram message
    """
    from src.xsmn_ensemble.ensemble_engine import MODEL_DISPLAY_NAME

    lines = ["📊 <b>XSMN Auto-Weight Report</b>\n"]

    all_models = sorted(set(list(current_weights.keys()) + list(optimal_weights.keys())))

    lines.append("Model | Current → Optimal | Δ")
    lines.append("─" * 35)

    for m in all_models:
        display = MODEL_DISPLAY_NAME.get(m, m)
        curr = current_weights.get(m, 0.0)
        opt = optimal_weights.get(m, 0.0)
        delta = opt - curr

        arrow = "↑" if delta > 0.01 else "↓" if delta < -0.01 else "="
        perf_str = ""
        if performance and m in performance:
            perf_str = f" ({performance[m]:.0%})"

        lines.append(f"  {display:8s} | {curr:.2f} → {opt:.2f} | {arrow}{abs(delta):.2f}{perf_str}")

    return "\n".join(lines)
