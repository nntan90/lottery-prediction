"""
model_loto_statistical.py — Model L: XSMB loto statistical analyzer.

Wraps XSMBLotoAnalyzer's multi-criteria "dan so" scoring as a first-class
ensemble sub-model. The detailed loto report still runs separately for
Telegram, while this wrapper contributes its Top-N statistical picks to the
multi-model consensus.
"""

import time
from datetime import date
from typing import Dict, Optional

from src.xsmb_ensemble.xsmb_loto_analyzer import XSMBLotoAnalyzer


def predict_loto_statistical(
    db,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 100,
    top_n: int = 5,
    region: str = "XSMB",
) -> Dict:
    """
    Model L: statistical loto scorer for XSMB.

    Uses the same multi-criteria logic as the loto report:
      - strong touch/head/tail signals
      - ideal gap window (1-7 days)
      - hot frequency over 30 draws
      - falling-number probability when a pair appeared yesterday

    Returns a model_result dict compatible with compute_xsmb_ensemble().
    """
    start_ms = time.time()

    try:
        if region != "XSMB":
            return _error_result(
                "loto_statistical", province, 0,
                f"Unsupported region for loto statistical model: {region}",
                start_ms,
            )

        analyzer = XSMBLotoAnalyzer(db, target_date, lookback=n_draws)
        result = analyzer.suggest_top_3_dan_so(top_n=top_n)
        scored = result.get("top_scored", [])
        n_used = getattr(analyzer, "_n_draws", 0)

        if not scored:
            return _error_result(
                "loto_statistical", province, n_used,
                "Không có ứng viên loto statistical",
                start_ms,
            )

        raw_scores = [max(float(score), 0.0) for _, score in scored]
        full_raw_scores = [
            max(float(score), 0.0)
            for score in result.get("score_vector", [])
        ]
        max_score = max(full_raw_scores or raw_scores)

        top_pairs = []
        for pair, score in scored[:top_n]:
            positive_score = max(float(score), 0.0)
            if max_score > 1e-10:
                norm_score = positive_score / max_score
            else:
                norm_score = 1.0
            norm_score = max(norm_score, 0.05)
            top_pairs.append((int(pair), round(float(norm_score), 4)))

        return {
            "model_name": "loto_statistical",
            "source_family": "loto_report",
            "province": province,
            "top_pairs": top_pairs,
            "score_vector": [
                float(score / max_score) if max_score > 1e-10 else 0.0
                for score in full_raw_scores
            ],
            "score_semantics": "relative_score_uncalibrated",
            "n_draws_used": n_used,
            "model_version": "loto_statistical_v1",
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return _error_result("loto_statistical", province, 0, str(e), start_ms)


def _error_result(
    model_name: str,
    province: Optional[str],
    n_draws: int,
    error_msg: str,
    start_ms: float,
) -> Dict:
    return {
        "model_name": model_name,
        "province": province,
        "top_pairs": [],
        "n_draws_used": n_draws,
        "status": "error",
        "error_message": error_msg,
        "execution_time_ms": int((time.time() - start_ms) * 1000),
    }
