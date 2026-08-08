"""
predict_ensemble.py — v5.1 (4-Model + Loto XSMB, 6-Model XSMN)
Orchestration script cho Multi-Model Ensemble pipeline (XSMB & XSMN).
Chạy bởi GitHub Actions workflow: 02-predict-ensemble.yml

XSMB (v5.1 — 4 active models + Loto):
  1. Frequency (multi-window)     → Top 5
  2. Markov (second-order)        → Top 5
  3. Chi-square GOF               → Top 5
  4. CDM                          → Top 5
  5. Loto Statistical             → Top 5
  → Precision Score Fusion        → Top 3

XSMN (v3.5 — 6 models, backward-compatible contracts):
  1-6. Frequency/Gap/Markov/XGB/LSTM/CDM → CombSUM + combo ranking → Top 3

Flow mỗi ngày:
  1. XSMB: chạy 4 model + Loto → v5 precision ensemble
  2. XSMN: resolve provinces → chạy 6 models per province → merged ensemble
  3. Ghi prediction_results + model_predictions
  4. Gửi Telegram notification

Usage:
  python src/scripts/predict_ensemble.py
  python src/scripts/predict_ensemble.py --date 2026-05-07
"""

import argparse
import asyncio
import os
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.supabase_client import LotteryDB
from src.utils.storage import LotteryStorage
from src.bot.ensemble_messages import ShadowRow, format_compact_ensemble_message
from src.bot.telegram_bot import LotteryNotifier
from src.crawler.xsmn_crawler import XSMNCrawler

from src.xsmn_ensemble.resolve_provinces import get_target_provinces, get_dow_label
from src.xsmn_coupled import CMRConfig, generate_shadow_prediction
from src.xsmn_relationship import (
    RelationshipConfig,
    generate_relationship_shadow,
)
from src.xsmn_llm_gen import (
    LLMGenConfigError,
    build_evidence_packet,
    compute_input_hash,
    load_llm_gen_config,
    run_llm_gen,
)
from src.xsmn_llm_gen.config import OPENAI_BACKEND_WIRE_APIS, PROVIDER_MODELS

# XSMN imports (v3.2 — backward compatible)
from src.xsmn_ensemble.model_frequency import predict_frequency as xsmn_predict_frequency
from src.xsmn_ensemble.model_gap import predict_gap as xsmn_predict_gap
from src.xsmn_ensemble.model_markov import predict_markov as xsmn_predict_markov
from src.xsmn_ensemble.model_xgboost import predict_xgboost as xsmn_predict_xgboost
from src.xsmn_ensemble.model_lstm import predict_lstm as xsmn_predict_lstm
from src.xsmn_ensemble.model_cdm import predict_cdm as xsmn_predict_cdm
from src.xsmn_ensemble.ensemble_engine import (
    compute_xsmn_merged_combo_selector_ensemble,
    format_ensemble_result as xsmn_format_ensemble_result,
    format_model_prediction_log as xsmn_format_model_prediction_log,
)

# XSMB imports (v4.0 — dedicated 7-model pipeline)
from src.xsmb_ensemble.model_frequency import predict_frequency as xsmb_predict_frequency
from src.xsmb_ensemble.model_gap import predict_gap as xsmb_predict_gap
from src.xsmb_ensemble.model_markov import predict_markov as xsmb_predict_markov
from src.xsmb_ensemble.model_xgboost import predict_xgboost as xsmb_predict_xgboost
from src.xsmb_ensemble.model_lstm import predict_lstm as xsmb_predict_lstm
from src.xsmb_ensemble.model_bayesian import predict_bayesian as xsmb_predict_bayesian
from src.xsmb_ensemble.model_cyclic import predict_cyclic as xsmb_predict_cyclic
from src.xsmb_ensemble.model_stats_freq_gap import predict_stats_freq_gap as xsmb_predict_stats_freq_gap
from src.xsmb_ensemble.model_chisquare_gof import predict_chisquare_gof as xsmb_predict_chisquare_gof
from src.xsmb_ensemble.model_chisquare_independence import (
    predict_chisquare_independence as xsmb_predict_chisquare_independence,
)
from src.xsmb_ensemble.model_cdm import predict_cdm as xsmb_predict_cdm
from src.xsmb_ensemble.model_loto_statistical import (
    predict_loto_statistical as xsmb_predict_loto_statistical,
)
from src.xsmb_ensemble.ensemble_engine import (
    compute_xsmb_ensemble,
    format_ensemble_result as xsmb_format_ensemble_result,
    format_model_prediction_log as xsmb_format_model_prediction_log,
)
from src.xsmb_ensemble.auto_weight import compute_optimal_weights  # legacy fallback
from src.scoring.credibility_scorer import compute_credibility_scores
from src.xsmb_combo.shadow import maybe_run_xsmb_combo_shadow

from src.database.prediction_repo import (
    get_shadow_prediction,
    normalize_shadow_prediction,
    normalize_xsmb_combo_shadow,
    sanitize_prediction_reason,
    save_model_prediction,
    save_prediction,
    save_shadow_prediction,
    shadow_tracking_schema_ready,
)


XSMB_ACTIVE_MODEL_NAMES = [
    "frequency",
    "markov",
    "chisquare_gof",
    "cdm",
    "loto_statistical",
]
TOTAL_MODELS_XSMB = len(XSMB_ACTIVE_MODEL_NAMES)
TOTAL_MODELS_PER_PROVINCE = 6   # v3.3: 6 models per XSMN province (added CDM)
MODEL_OUTPUT_TOP_N = 5
MODEL_ENSEMBLE_TOP_N = 10
XSMB_MODEL_OUTPUT_TOP_N = 5
RULE_MODEL_LOOKBACK_DRAWS = 180
XGB_FEATURE_LOOKBACK_DRAWS = 240
LSTM_LOOKBACK_DRAWS = 180

MODEL_SHORT_NAMES = {
    "frequency": "Freq",
    "gap_overdue": "Gap",
    "markov": "Markov",
    "xgboost_core": "XGB",
    "lstm": "LSTM",
    "bayesian": "Bayes",
    "cyclic": "Cyclic",
    "stats_freq_gap": "StatsFG",
    "chisquare_gof": "ChiGOF",
    "chisquare_independence": "ChiInd",
    "cdm": "CDM",
    "loto_statistical": "Loto",
}

XSMB_MODEL_SHORT_NAMES = {
    **MODEL_SHORT_NAMES,
    "markov": "Markov²",
    "lstm": "BiLSTM",
    "loto_statistical": "Loto",
}

EXPECTED_MODEL_NAMES = {
    "XSMB": XSMB_ACTIVE_MODEL_NAMES,
    "XSMN": [
        "frequency",
        "gap_overdue",
        "markov",
        "xgboost_core",
        "lstm",
        "cdm",
    ],
}
def _generate_ddt_shadow_safely(
    db: LotteryDB,
    provinces: list[str],
    target_date: date,
) -> dict:
    """Backward-compatible read-only adapter; production never executes DDT."""
    try:
        result = get_shadow_prediction(db, "ddt_shadow", target_date)
        if result:
            metadata = result.get("run_metadata")
            stored_scope = (
                metadata.get("provinces")
                if isinstance(metadata, dict)
                else None
            )
            if stored_scope and list(stored_scope) != list(provinces):
                return {
                    "status": "error",
                    "reason": "persisted_ddt_scope_mismatch",
                }
            return result
        return {
            "status": "pending_local",
            "reason": "waiting_for_local_run",
        }
    except Exception as exc:
        reason = (" ".join(str(exc).split()) or "persisted_ddt_read_failed")[:160]
        print(f"     ⚠️ DDT persisted row unavailable; production preserved: {reason}")
        return {"status": "error", "reason": reason}


def _ddt_shadow_row(result: Optional[dict]) -> ShadowRow:
    """Convert the audit result to one compact, backward-compatible row."""
    if result and result.get("status") in {"success", "uncalibrated"}:
        try:
            if result.get("pair_1") is not None:
                top_pairs = tuple(
                    (int(result[f"pair_{index}"]), float(result[f"score_{index}"]))
                    for index in range(1, 4)
                )
                return ShadowRow(
                    label="DDT shadow",
                    top_pairs=top_pairs,
                    status=(
                        "calibrated"
                        if result.get("score_semantics")
                        == "merged_pair_hit_probability_calibrated"
                        else "uncalibrated"
                    ),
                )
            score_key = (
                "probability"
                if result.get("score_semantics")
                == "merged_pair_hit_probability_calibrated"
                else "estimated_likelihood_uncalibrated"
            )
            top_pairs = tuple(
                (int(item["pair"]), float(item[score_key]))
                for item in result.get("selected_evidence", [])
            )
            if not top_pairs:
                raise ValueError("DDT selected_evidence is empty")
            return ShadowRow(
                label="DDT shadow",
                top_pairs=top_pairs,
                status="calibrated" if score_key == "probability" else "uncalibrated",
            )
        except (KeyError, TypeError, ValueError):
            return ShadowRow(label="DDT shadow", status="Tạm không khả dụng")
    if result and result.get("status") == "insufficient_evidence":
        reason = str(result.get("error_message") or result.get("reason") or "").strip()
        status = "Chưa đủ dữ liệu"
        if reason:
            status += f": {reason[:120]}"
        return ShadowRow(label="DDT shadow", status=status)
    if result and result.get("status") == "pending_local":
        return ShadowRow(label="DDT shadow", status="Chờ chạy local")
    if result and result.get("status") == "error":
        reason = str(result.get("error_message") or result.get("reason") or "").strip()
        status = "Lỗi local"
        if reason:
            status += f": {reason[:120]}"
        return ShadowRow(label="DDT shadow", status=status)
    return ShadowRow(label="DDT shadow", status="Tạm không khả dụng")


def _relationship_shadow_row(result: Optional[dict]) -> ShadowRow:
    """Render relationship as one combo score, never as a probability."""
    if result and result.get("status") == "success":
        try:
            raw_numbers = result.get("top_3") or [
                result.get(f"pair_{index}") for index in range(1, 4)
            ]
            numbers = tuple(int(pair) for pair in raw_numbers)
            if (
                len(numbers) != 3
                or len(set(numbers)) != 3
                or len({pair % 10 for pair in numbers}) != 3
            ):
                raise ValueError("invalid relationship Top 3")
            metadata = result.get("run_metadata")
            selected_combo = (
                metadata.get("selected_combo")
                if isinstance(metadata, dict)
                else None
            )
            aggregate_score = result.get("relationship_score")
            if aggregate_score is None and isinstance(selected_combo, dict):
                aggregate_score = selected_combo.get("relationship_score")
            return ShadowRow(
                label="Relationship shadow",
                numbers=numbers,
                aggregate_score=float(aggregate_score),
                aggregate_label="điểm bộ chưa calibration",
                status="không thay production",
            )
        except (KeyError, TypeError, ValueError):
            return ShadowRow(
                label="Relationship shadow",
                status="Tạm không khả dụng",
            )
    status = str(result.get("status") or "") if result else ""
    labels = {
        "insufficient_active_models": "Chưa đủ model hoạt động",
        "insufficient_recent_history": "Chưa đủ 2 kỳ gần nhất",
        "insufficient_matched_draws": "Chưa đủ lịch sử matched",
        "no_eligible_anchor": "Không có anchor hợp lệ",
        "insufficient_candidate_diversity": "Chưa đủ đa dạng hàng đơn vị",
        "insufficient": "Chưa đủ dữ liệu",
        "error": "Tạm không khả dụng",
    }
    text = labels.get(status, "Tạm không khả dụng")
    reason = (
        sanitize_prediction_reason(
            result.get("error_message") or result.get("reason")
        )
        if result
        else ""
    ) or ""
    if status == "error" and reason:
        text += f": {reason[:120]}"
    return ShadowRow(label="Relationship shadow", status=text)


def _llm_gen_public_env_identity() -> dict[str, Optional[str]]:
    """Return allowlisted LLM identity fields without reading any credential."""
    provider = (os.getenv("LLM_GEN_PROVIDER", "") or "").strip().lower()
    api_backend: Optional[str]
    wire_api: Optional[str]
    if provider == "openai":
        candidate = os.getenv("LLM_GEN_OPENAI_BACKEND", "official") or "official"
        api_backend = candidate if candidate in OPENAI_BACKEND_WIRE_APIS else None
        wire_api = OPENAI_BACKEND_WIRE_APIS.get(candidate)
    elif provider == "anthropic":
        api_backend = "anthropic"
        wire_api = "messages"
    else:
        api_backend = None
        wire_api = None
    return {
        "provider": provider or None,
        "provider_model": PROVIDER_MODELS.get(provider),
        "api_backend": api_backend,
        "wire_api": wire_api,
    }


def _llm_gen_shadow_row(result: Optional[dict]) -> Optional[ShadowRow]:
    """Render one provider-labelled LLM_Gen row without changing production."""
    if result is None:
        return None
    metadata = result.get("run_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    provider_model = str(
        metadata.get("provider_model") or result.get("provider_model") or ""
    )
    api_backend = str(metadata.get("api_backend") or "")
    provider_labels = {
        "gpt-5.6-sol": "GPT-5.6 Sol",
        "claude-opus-4-8": "Claude Opus 4.8",
    }
    label = "LLM_Gen"
    if provider_model:
        model_label = provider_labels.get(provider_model, provider_model)
        if api_backend == "agentrouter":
            label += f" [AgentRouter · {model_label}]"
        else:
            label += f" [{model_label}]"

    if str(result.get("status") or "") == "success":
        top_pairs = []
        for item in result.get("selected_evidence") or []:
            try:
                pair = int(item["pair"])
                score = float(item["ranking_score_uncalibrated"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= pair <= 99 and pair not in {value for value, _ in top_pairs}:
                top_pairs.append((pair, score))
        if len(top_pairs) == 3 and len({pair % 10 for pair, _ in top_pairs}) == 3:
            status = "điểm xếp hạng chưa calibration"
            if metadata.get("reused"):
                status += " · reuse canonical"
            return ShadowRow(label=label, top_pairs=tuple(top_pairs), status=status)

    reason = str(result.get("reason") or result.get("status") or "error")
    status_map = {
        "canonical_conflict": "Đã có kết quả canonical khác",
        "insufficient_candidate_diversity": "Chưa đủ đa dạng hàng đơn vị",
        "insufficient_candidates": "Chưa đủ candidate hợp lệ",
        "missing_api_key": "Thiếu API key của provider đã chọn",
        "invalid_openai_backend": "Backend OpenAI không hợp lệ",
        "schema_not_ready": "Database audit chưa sẵn sàng",
        "agentrouter_http_401": "AgentRouter từ chối API key (401)",
        "agentrouter_http_403": "AgentRouter không cấp quyền model (403)",
        "agentrouter_http_429": "AgentRouter đang giới hạn tần suất (429)",
        "agentrouter_model_unavailable": "AgentRouter chưa cấp GPT-5.6 Sol",
        "agentrouter_timeout": "AgentRouter hết thời gian chờ",
        "agentrouter_request_failed": "Không kết nối được AgentRouter",
        "agentrouter_invalid_choice_count": "AgentRouter trả số choice không hợp lệ",
        "agentrouter_invalid_choice": "AgentRouter trả choice không hợp lệ",
        "agentrouter_empty_content": "AgentRouter trả nội dung rỗng",
        "agentrouter_refusal": "AgentRouter từ chối tạo kết quả",
        "agentrouter_truncated": "AgentRouter cắt ngắn kết quả",
        "agentrouter_invalid_finish_reason": "AgentRouter chưa hoàn tất kết quả",
        "agentrouter_invalid_models_response": "Không đọc được danh sách model AgentRouter",
        "invalid_provider_json": "Provider trả JSON không hợp lệ",
        "invalid_provider_schema": "Provider trả schema không hợp lệ",
        "endpoint_not_allowed": "Endpoint provider không nằm trong allowlist",
    }
    status = status_map.get(reason)
    if status is None and reason.startswith("agentrouter_http_"):
        http_status = reason.removeprefix("agentrouter_http_")
        status = f"AgentRouter trả lỗi HTTP {http_status}"
    status = status or "Tạm không khả dụng"
    return ShadowRow(label=label, status=status)


def _run_llm_gen_shadow_safely(
    db: LotteryDB,
    model_results: list[dict],
    provinces: list[str],
    target_date: date,
    *,
    ensemble_output: dict,
    recent_tails_by_province: dict[str, list[int]],
    recent_province_tails: dict[str, set[int]],
    combo_history_tail_sets: list[set[int]],
) -> Optional[dict]:
    """Run/persist the optional LLM shadow without risking production.

    Migration 12 is checked before config reads the selected API key, and an
    existing canonical success is handed to the service before any provider
    adapter is created.
    """
    raw_mode = (os.getenv("LLM_GEN_MODE", "off") or "off").strip().lower()
    if raw_mode == "off":
        return None
    public_identity = _llm_gen_public_env_identity()

    try:
        if not shadow_tracking_schema_ready(db):
            return {
                "status": "schema_not_ready",
                "reason": "schema_not_ready",
                "model_name": "llm_gen",
                "model_version": "llm_gen_v1",
                "score_semantics": "ranking_score_uncalibrated",
                "data_cutoff": target_date.isoformat(),
                "run_metadata": {
                    **public_identity,
                    "data_cutoff": target_date.isoformat(),
                    "provinces": list(provinces),
                },
            }
    except Exception as exc:
        safe_detail = sanitize_prediction_reason(exc) or "schema_preflight_failed"
        print(f"     ⚠️ LLM_Gen schema preflight failed; provider skipped: {safe_detail}")
        return {
            "status": "schema_not_ready",
            "reason": "schema_not_ready",
            "model_name": "llm_gen",
            "model_version": "llm_gen_v1",
            "score_semantics": "ranking_score_uncalibrated",
            "data_cutoff": target_date.isoformat(),
            "run_metadata": {
                **public_identity,
                "data_cutoff": target_date.isoformat(),
                "provinces": list(provinces),
            },
        }

    try:
        config = load_llm_gen_config()
    except LLMGenConfigError as exc:
        try:
            packet = build_evidence_packet(
                model_results,
                provinces,
                target_date,
                effective_weights=ensemble_output.get("effective_weights", {}),
                production_top_pairs=ensemble_output.get("top_pairs", []),
                recent_tails_by_province=recent_tails_by_province,
                recent_province_tails=recent_province_tails,
                combo_history_tail_sets=combo_history_tail_sets,
            )
            input_hash = compute_input_hash(packet)
        except Exception:
            input_hash = None
        result = {
            "status": "error",
            "reason": exc.reason,
            "model_name": "llm_gen",
            "model_version": "llm_gen_v1",
            "score_semantics": "ranking_score_uncalibrated",
            "data_cutoff": target_date.isoformat(),
            "selected_evidence": [],
            "run_metadata": {
                **public_identity,
                "prompt_version": "llm_gen_prompt_v1",
                "schema_version": "llm_gen_response_v1",
                "model_version": "llm_gen_v1",
                "input_hash": input_hash,
                "data_cutoff": target_date.isoformat(),
                "provinces": list(provinces),
                "usage": {},
                "latency_ms": 0,
                "config": {
                    "mode": raw_mode,
                    **public_identity,
                },
            },
        }
        try:
            record = normalize_shadow_prediction(
                result,
                model_name="llm_gen",
                target_date=target_date,
                provinces=provinces,
                execution_source="production_post_save",
            )
            save_shadow_prediction(db, record)
        except Exception as persist_exc:
            detail = sanitize_prediction_reason(persist_exc) or "llm_gen_config_error_persistence_failed"
            print(f"     ⚠️ LLM_Gen config error not persisted: {detail}")
        return result

    try:
        existing = get_shadow_prediction(db, "llm_gen", target_date)

        result = run_llm_gen(
            config,
            model_results,
            provinces,
            target_date,
            effective_weights=ensemble_output.get("effective_weights", {}),
            production_top_pairs=ensemble_output.get("top_pairs", []),
            recent_tails_by_province=recent_tails_by_province,
            recent_province_tails=recent_province_tails,
            combo_history_tail_sets=combo_history_tail_sets,
            lookup_success=(
                (lambda _input_hash, _config: existing)
                if existing and existing.get("status") in {"success", "uncalibrated"}
                else None
            ),
        )
        if result is None:
            return None
        if result.get("status") == "canonical_conflict":
            return result
        metadata = result.get("run_metadata")
        if isinstance(metadata, dict) and metadata.get("reused"):
            return result

        runtime_ms = (
            int(metadata.get("latency_ms", 0))
            if isinstance(metadata, dict)
            else None
        )
        record = normalize_shadow_prediction(
            result,
            model_name="llm_gen",
            target_date=target_date,
            provinces=provinces,
            execution_source="production_post_save",
            runtime_ms=runtime_ms,
            config_metadata=config.public_metadata(),
        )
        saved = save_shadow_prediction(db, record)
        if saved:
            return result

        raced = get_shadow_prediction(db, "llm_gen", target_date)
        if raced and raced.get("status") in {"success", "uncalibrated"}:
            return run_llm_gen(
                config,
                model_results,
                provinces,
                target_date,
                effective_weights=ensemble_output.get("effective_weights", {}),
                production_top_pairs=ensemble_output.get("top_pairs", []),
                recent_tails_by_province=recent_tails_by_province,
                recent_province_tails=recent_province_tails,
                combo_history_tail_sets=combo_history_tail_sets,
                lookup_success=lambda _input_hash, _config: raced,
            )
        return {
            **result,
            "status": "error",
            "reason": "llm_gen_ledger_unavailable",
            "selected_evidence": [],
        }
    except Exception as exc:
        safe_detail = sanitize_prediction_reason(exc) or "llm_gen_execution_failed"
        print(f"     ⚠️ LLM_Gen failed without affecting ensemble: {safe_detail}")
        return {
            "status": "error",
            "reason": "llm_gen_execution_failed",
            "model_name": "llm_gen",
            "model_version": "llm_gen_v1",
            "score_semantics": "ranking_score_uncalibrated",
            "data_cutoff": target_date.isoformat(),
            "selected_evidence": [],
            "run_metadata": {
                "provider": config.provider,
                "provider_model": config.provider_model,
                "api_backend": config.api_backend,
                "wire_api": config.wire_api,
                "prompt_version": config.prompt_version,
                "schema_version": config.schema_version,
                "data_cutoff": target_date.isoformat(),
                "provinces": list(provinces),
            },
        }


def _relationship_scope_matches(result: Optional[dict], provinces: list) -> bool:
    """Accept a stored relationship row only for the exact scheduled scope."""
    if not result:
        return False
    metadata = result.get("run_metadata")
    stored = metadata.get("provinces") if isinstance(metadata, dict) else None
    return isinstance(stored, list) and stored == list(provinces)


def _xsmb_combo_shadow_row(result) -> Optional[ShadowRow]:
    """Convert combo v6 output without presenting its score as probability."""
    if result is None:
        return None
    status = (
        result.status.value
        if hasattr(result.status, "value")
        else str(result.status)
    )
    if status == "success" and len(result.top_pairs) == 3:
        return ShadowRow(
            label="Combo v6 shadow",
            numbers=tuple(int(pair) for pair in result.top_pairs),
            aggregate_score=float(result.objective_score),
            aggregate_label="điểm tổ hợp chưa calibration",
            status="không thay production",
        )
    reason = "; ".join(result.diagnostics[:2])
    if status in {"insufficient_candidates", "insufficient_history"}:
        text = "Chưa đủ dữ liệu"
    else:
        text = "Tạm không khả dụng"
    if reason:
        text += f": {reason[:120]}"
    return ShadowRow(label="Combo v6 shadow", status=text)


def _flatten_tail_rows_by_draw(rows: list[dict]) -> list[int]:
    """Flatten unique tails per draw while preserving repeat counts across draws."""
    per_draw: dict[str, set[int]] = {}
    for row in rows:
        draw_date = row.get("draw_date")
        if not draw_date:
            raise ValueError("tail history row is missing draw_date")
        per_draw.setdefault(str(draw_date), set()).add(int(row["tail_2d"]))

    flattened: list[int] = []
    for draw_date in sorted(per_draw):
        flattened.extend(sorted(per_draw[draw_date]))
    return flattened


def _execute_paged_rows(query, page_size: int = 1000) -> list[dict]:
    """Execute a Supabase query without truncating history at the API row cap."""
    rows: list[dict] = []
    offset = 0
    while True:
        page = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def get_recent_tails(db: LotteryDB, region: str, provinces: list, target_date: date, limit_per_province: int = 3) -> list:
    """Lấy lịch sử 2 số cuối trong N kỳ quay gần nhất CÙNG THỨ (cùng ngày trong tuần)."""
    tails = []
    # If no provinces (XSMB), we use [None] to iterate once
    provs_to_check = provinces if provinces else [None]
    target_weekday = target_date.weekday()

    for prov in provs_to_check:
        # Lấy đủ số kỳ để tìm ra N kỳ cùng thứ (thường x7 lần limit)
        fetch_limit = limit_per_province * 7 + 10
        q1 = db.supabase.table("lottery_draws") \
            .select("draw_date") \
            .eq("region", region) \
            .lt("draw_date", str(target_date)) \
            .order("draw_date", desc=True) \
            .limit(fetch_limit)
        q1 = q1.eq("province", prov) if prov else q1.is_("province", "null")
        draws = q1.execute()

        if not draws.data:
            continue

        # Lọc cùng thứ
        same_weekday_dates = []
        for d in draws.data:
            d_date = date.fromisoformat(d["draw_date"])
            if d_date.weekday() == target_weekday:
                same_weekday_dates.append(d["draw_date"])
                if len(same_weekday_dates) == limit_per_province:
                    break

        if not same_weekday_dates:
            continue

        # Lấy tails của các kỳ này
        q2 = db.supabase.table("tails_2d") \
            .select("draw_date,tail_2d") \
            .eq("region", region) \
            .in_("draw_date", same_weekday_dates)
        q2 = q2.eq("province", prov) if prov else q2.is_("province", "null")
        t_data = q2.execute()

        if t_data.data:
            tails.extend(_flatten_tail_rows_by_draw(t_data.data))

    return tails


def get_last_7_days_tails(db: LotteryDB, region: str, provinces: list, target_date: date) -> list:
    """Lấy lịch sử 2 số cuối trong 7 ngày gần nhất (tất cả các ngày, không phân biệt thứ)."""
    tails = []
    provs_to_check = provinces if provinces else [None]
    
    # Tính ngày bắt đầu (target_date - 7 days)
    start_date = target_date - timedelta(days=7)

    for prov in provs_to_check:
        q1 = db.supabase.table("lottery_draws") \
            .select("draw_date") \
            .eq("region", region) \
            .lt("draw_date", str(target_date)) \
            .gte("draw_date", str(start_date)) \
            .order("draw_date", desc=True)
            
        q1 = q1.eq("province", prov) if prov else q1.is_("province", "null")
        draws = q1.execute()

        if not draws.data:
            continue

        draw_dates = [d["draw_date"] for d in draws.data]

        q2 = db.supabase.table("tails_2d") \
            .select("draw_date,tail_2d") \
            .eq("region", region) \
            .in_("draw_date", draw_dates)
        q2 = q2.eq("province", prov) if prov else q2.is_("province", "null")
        t_data = q2.execute()

        if t_data.data:
            tails.extend(_flatten_tail_rows_by_draw(t_data.data))

    return tails


def get_recent_province_tails(
    db: LotteryDB,
    region: str,
    provinces: list[str],
    target_date: date,
    max_days_back: int = 3,
) -> dict[str, set[int]]:
    """
    Lấy tail_set của kỳ quay gần nhất theo từng tỉnh trong vòng max_days_back.

    Dùng cho XSMN để xử lý các đài xổ nhiều hơn 1 lần/tuần như TP.HCM:
    thứ Hai cần biết các số đã ra ở TP.HCM thứ Bảy gần nhất để giảm rank nhẹ.
    """
    result: dict[str, set[int]] = {}
    start_date = target_date - timedelta(days=max_days_back)

    for province in provinces:
        q = db.supabase.table("lottery_draws") \
            .select("draw_date") \
            .eq("region", region) \
            .eq("province", province) \
            .lt("draw_date", target_date.isoformat()) \
            .gte("draw_date", start_date.isoformat()) \
            .order("draw_date", desc=True) \
            .limit(1)

        rows = q.execute().data or []
        if not rows:
            continue

        last_draw_date = rows[0]["draw_date"]
        t_rows = db.supabase.table("tails_2d") \
            .select("tail_2d") \
            .eq("region", region) \
            .eq("province", province) \
            .eq("draw_date", last_draw_date) \
            .execute().data or []

        if t_rows:
            result[province] = {int(row["tail_2d"]) for row in t_rows}
            print(
                f"  📅 {province}: kỳ gần nhất {last_draw_date}, "
                f"{len(result[province])} số để giảm repeat"
            )

    return result


def get_recent_merged_tail_sets_for_province_pair(
    db: LotteryDB,
    region: str,
    provinces: list[str],
    target_date: date,
    limit: int = 10,
) -> list[set[int]]:
    """
    Lấy tail_set merged của các kỳ trước có cùng cặp tỉnh XSMN.

    Ví dụ target Thứ Tư: Đồng Nai + Cần Thơ. Hàm lấy 10 Thứ Tư trước,
    merge tails của hai tỉnh này cho từng ngày, rồi trả về list[set[int]].
    """
    if not provinces:
        return []

    anchor_province = provinces[0]
    fetch_limit = limit * 3
    draw_rows = db.supabase.table("lottery_draws") \
        .select("draw_date") \
        .eq("region", region) \
        .eq("province", anchor_province) \
        .lt("draw_date", target_date.isoformat()) \
        .order("draw_date", desc=True) \
        .limit(fetch_limit) \
        .execute().data or []

    same_weekday_dates = []
    for row in draw_rows:
        draw_date = date.fromisoformat(row["draw_date"])
        if draw_date.weekday() != target_date.weekday():
            continue
        same_weekday_dates.append(row["draw_date"])
        if len(same_weekday_dates) == limit:
            break

    if not same_weekday_dates:
        return []

    tail_query = db.supabase.table("tails_2d") \
        .select("draw_date,province,tail_2d") \
        .eq("region", region) \
        .in_("province", provinces) \
        .in_("draw_date", same_weekday_dates)
    tail_rows = _execute_paged_rows(tail_query)

    tails_by_date: dict[str, set[int]] = {draw_date: set() for draw_date in same_weekday_dates}
    provinces_by_date: dict[str, set[str]] = {draw_date: set() for draw_date in same_weekday_dates}
    row_counts: dict[tuple[str, str], int] = {}
    for row in tail_rows:
        tails_by_date.setdefault(row["draw_date"], set()).add(int(row["tail_2d"]))
        if row.get("province"):
            province = str(row["province"])
            provinces_by_date.setdefault(row["draw_date"], set()).add(province)
            key = (row["draw_date"], province)
            row_counts[key] = row_counts.get(key, 0) + 1

    return [
        tails_by_date[draw_date]
        for draw_date in same_weekday_dates
        if tails_by_date.get(draw_date)
        and provinces_by_date.get(draw_date) == set(provinces)
        and all(row_counts.get((draw_date, province), 0) >= 18 for province in provinces)
    ]


async def run_xsmb_models(
    db: LotteryDB,
    storage: LotteryStorage,
    target_date: date,
    tmpdir: str,
) -> list:
    """
    Chạy các model XSMB v5.1. Trả về list model_results.
    Fault-tolerant: model lỗi → ensemble vẫn chạy với model còn lại.
    """
    print(f"\n  {'='*50}")
    print(f"  📍 XSMB v5.1 — 4 Models + Loto Pipeline")
    print(f"  {'='*50}")

    model_results = []

    # ── Model A: Frequency (Multi-window) ──
    print(f"  🔹 Model A (Frequency/Multi-window)...")
    result = xsmb_predict_frequency(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "A")

    # ── Model B: Gap/Overdue (Weekday-specific) ──
    # print(f"  🔹 Model B (Gap/Weekday-specific) - DISABLED...")
    # result = xsmb_predict_gap(
    #     db, province=None, target_date=target_date,
    #     n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    # )
    # model_results.append(result)
    # _log_model_result(result, "B")

    # ── Model C: Markov (Second-order) ──
    print(f"  🔹 Model C (Markov²)...")
    result = xsmb_predict_markov(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "C")

    # ── Model D: XGBoost (25 features) ──
    # print(f"  🔹 Model D (XGBoost v4) - DISABLED...")
    # result = xsmb_predict_xgboost(
    #     db, storage, province=None, target_date=target_date,
    #     region="XSMB", n_draws=XGB_FEATURE_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, tmpdir=tmpdir,
    # )
    # model_results.append(result)
    # _log_model_result(result, "D")

    # ── Model E: BiLSTM + Attention ──
    # print(f"  🔹 Model E (BiLSTM+Attention) - DISABLED...")
    # result = xsmb_predict_lstm(
    #     db, storage=storage, province=None, target_date=target_date,
    #     region="XSMB", n_draws=LSTM_LOOKBACK_DRAWS, seq_len=60, top_n=XSMB_MODEL_OUTPUT_TOP_N, tmpdir=tmpdir,
    # )
    # model_results.append(result)
    # _log_model_result(result, "E")

    # ── Model F: Bayesian ──
    # print(f"  🔹 Model F (Bayesian) - DISABLED...")
    # result = xsmb_predict_bayesian(
    #     db, province=None, target_date=target_date,
    #     n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    # )
    # model_results.append(result)
    # if result["status"] == "success":
    #     conf = result.get("confidence", 0)
    #     pairs_str = ", ".join(f"{p:02d}" for p, _ in result["top_pairs"])
    #     print(f"     ✅ Top {XSMB_MODEL_OUTPUT_TOP_N}: [{pairs_str}] (conf={conf:.2f}, {result['execution_time_ms']}ms)")
    # else:
    #     print(f"     ❌ Error: {result['error_message']}")

    # ── Model G: Cyclic (FFT) ──
    # print(f"  🔹 Model G (Cyclic/FFT) - DISABLED...")
    # result = xsmb_predict_cyclic(
    #     db, province=None, target_date=target_date,
    #     n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    # )
    # model_results.append(result)
    # _log_model_result(result, "G")

    # ── Model H: Descriptive Frequency/Gap Stats ──
    # print(f"  🔹 Model H (Stats Freq/Gap) - DISABLED...")
    # result = xsmb_predict_stats_freq_gap(
    #     db, province=None, target_date=target_date,
    #     n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    # )
    # model_results.append(result)
    # _log_model_result(result, "H")

    # ── Model I: Chi-square Goodness-of-fit ──
    print(f"  🔹 Model I (Chi-square GOF)...")
    result = xsmb_predict_chisquare_gof(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "I")

    # ── Model J: Chi-square Independence/Homogeneity ──
    # print(f"  🔹 Model J (Chi-square Independence) - DISABLED...")
    # result = xsmb_predict_chisquare_independence(
    #     db, province=None, target_date=target_date,
    #     n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    # )
    # model_results.append(result)
    # _log_model_result(result, "J")

    # ── Model K: CDM (Dirichlet-Multinomial) ──
    print(f"  🔹 Model K (CDM/Dirichlet-Multinomial)...")
    result = xsmb_predict_cdm(
        db, province=None, target_date=target_date,
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "K")

    # ── Model L: Loto Statistical Analyzer ──
    print(f"  🔹 Model L (Loto Statistical)...")
    result = xsmb_predict_loto_statistical(
        db, province=None, target_date=target_date,
        n_draws=100, top_n=XSMB_MODEL_OUTPUT_TOP_N, region="XSMB",
    )
    model_results.append(result)
    _log_model_result(result, "L")

    # ── Summary ──
    success_count = sum(1 for r in model_results if r["status"] == "success")
    print(f"\n  📊 XSMB Models Active: {success_count}/{TOTAL_MODELS_XSMB}")

    lstm_results = [r for r in model_results if r.get("model_name") == "lstm"]
    if lstm_results and lstm_results[0].get("status") != "success":
        err = lstm_results[0].get("error_message", "unknown")
        print(f"  🚨 WARNING: LSTM FAILED — {err}")
        print(f"  🚨 Ensemble sẽ chạy với {success_count} models (LSTM weight={0.15} bị mất)")

    # Save model_predictions logs
    for mr in model_results:
        log = xsmb_format_model_prediction_log("XSMB", None, mr, target_date)
        try:
            save_model_prediction(db, log)
        except Exception as e:
            print(f"     ⚠️  Log save failed ({mr['model_name']}): {e}")

    return model_results


async def run_xsmn_models_for_target(
    db: LotteryDB,
    storage: LotteryStorage,
    province: str | None,
    target_date: date,
    tmpdir: str,
) -> list:
    """
    Chạy 6 models XSMN cho một tỉnh, giữ nguyên result contract cũ.
    """
    print(f"\n  {'='*50}")
    print(f"  📍 XSMN | Province: {province or 'ALL'}")
    print(f"  {'='*50}")

    model_results = []

    # ── Model 1: Frequency/Hot-Cool ──
    print(f"  🔹 Model 1 (Frequency/Hot-Cool)...")
    result_1 = xsmn_predict_frequency(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=MODEL_ENSEMBLE_TOP_N,
    )
    model_results.append(result_1)
    _log_model_result(result_1, "1")

    # ── Model 2: Gap/Overdue ──
    print(f"  🔹 Model 2 (Gap/Overdue)...")
    result_2 = xsmn_predict_gap(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=MODEL_ENSEMBLE_TOP_N,
    )
    model_results.append(result_2)
    _log_model_result(result_2, "2")

    # ── Model 3: Markov ──
    print(f"  🔹 Model 3 (Markov)...")
    result_3 = xsmn_predict_markov(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=MODEL_ENSEMBLE_TOP_N,
    )
    model_results.append(result_3)
    _log_model_result(result_3, "3")

    # ── Model 4: XGBoost ──
    print(f"  🔹 Model 4 (XGBoost)...")
    result_4 = xsmn_predict_xgboost(
        db, storage, province, target_date, region="XSMN",
        n_draws=XGB_FEATURE_LOOKBACK_DRAWS, top_n=MODEL_ENSEMBLE_TOP_N, tmpdir=tmpdir,
    )
    model_results.append(result_4)
    _log_model_result(result_4, "4")

    # ── Model 5: LSTM/GRU ──
    print(f"  🔹 Model 5 (LSTM/GRU)...")
    result_5 = xsmn_predict_lstm(
        db, storage=storage, province=province, target_date=target_date,
        region="XSMN", n_draws=LSTM_LOOKBACK_DRAWS, seq_len=30, top_n=MODEL_ENSEMBLE_TOP_N, tmpdir=tmpdir,
    )
    model_results.append(result_5)
    _log_model_result(result_5, "5")

    # ── Model 6: CDM (Dirichlet-Multinomial) ──
    print(f"  🔹 Model 6 (CDM/Dirichlet-Multinomial)...")
    result_6 = xsmn_predict_cdm(
        db, province, target_date, region="XSMN",
        n_draws=RULE_MODEL_LOOKBACK_DRAWS, top_n=MODEL_ENSEMBLE_TOP_N,
    )
    model_results.append(result_6)
    _log_model_result(result_6, "6")

    # ── Summary ──
    success_count = sum(1 for r in model_results if r["status"] == "success")
    print(f"\n  📊 XSMN Models Active: {success_count}/{TOTAL_MODELS_PER_PROVINCE}")

    # Save model_predictions logs
    for mr in model_results:
        log = xsmn_format_model_prediction_log("XSMN", province, mr, target_date)
        try:
            save_model_prediction(db, log)
        except Exception as e:
            print(f"     ⚠️  Log save failed ({mr['model_name']}): {e}")

    return model_results


def _log_model_result(result: dict, label: str):
    """Helper log kết quả model."""
    if result["status"] == "success":
        pairs_str = ", ".join(f"{p:02d}" for p, _ in result["top_pairs"])
        version_str = f" [{result.get('model_version', '')}]" if result.get('model_version') else ""
        time_str = f"{result['execution_time_ms']}ms"
        n_str = f"n={result['n_draws_used']} kỳ, " if result.get('n_draws_used') else ""
        print(f"     ✅ Top {len(result['top_pairs'])}: [{pairs_str}]{version_str} ({n_str}{time_str})")
    else:
        print(f"     ❌ Error: {result['error_message']}")


def get_missing_models(
    db: LotteryDB,
    region: str,
    province: str | None,
    target_date: date,
    expected_count: int | None = None,
) -> list[str]:
    """
    Return expected sub-models that did not produce a successful log row.

    This is used only for Telegram audit text. It must never fail the
    prediction job after predictions were already generated and saved.
    """
    region_key = region.upper()
    expected_models = EXPECTED_MODEL_NAMES.get(region_key, [])
    if expected_count is not None and expected_count > 0:
        expected_models = expected_models[:expected_count]

    if not expected_models:
        return []

    try:
        q = db.supabase.table("model_predictions") \
            .select("model_name,status") \
            .eq("prediction_date", target_date.isoformat()) \
            .eq("region", region_key)
        q = q.is_("province", "null") if province is None else q.eq("province", province)
        rows = q.execute().data or []
    except Exception as e:
        print(f"   ⚠️  Không kiểm tra được missing models ({region_key}/{province or 'all'}): {e}")
        return []

    successful = {
        row.get("model_name")
        for row in rows
        if row.get("status") == "success"
    }
    return [model for model in expected_models if model not in successful]


async def run_xsmb_ensemble(
    target_date: date,
    db: LotteryDB,
    storage: LotteryStorage,
    notifier: LotteryNotifier,
    tmpdir: str,
    dry_run: bool = False,
):
    """
    XSMB v5.1 — Precision Ensemble Pipeline.
    """
    print(f"\n{'='*60}")
    print(f"🎯 XSMB MULTI-MODEL ENSEMBLE v5.1 (4 Models + Loto)")
    print(f"📅 Target date: {target_date} ({get_dow_label(target_date)})")
    print(f"{'='*60}")

    # Run XSMB models
    all_model_results = await run_xsmb_models(db, storage, target_date, tmpdir)

    # Credibility Scoring (pre-prediction — replaces auto_weight)
    auto_weights = None
    model_confidences = {}
    credibility_log = ""
    try:
        credibility = compute_credibility_scores(db, "XSMB", target_date)
        auto_weights = credibility["credibility_weights"]
        # confidence_map is diagnostic. Credibility influence is already in
        # auto_weights and must not be multiplied into the ensemble twice.
        credibility_log = credibility.get("scoring_log", "")
        print(credibility_log)
    except Exception as e:
        print(f"  ⚠️  Credibility scoring failed, falling back to auto_weight: {e}")
        try:
            auto_weights = compute_optimal_weights(db, lookback_days=30, region="XSMB")
            if auto_weights:
                print(f"  🔧 Auto-weights fallback applied: {', '.join(f'{k}={v:.2f}' for k,v in auto_weights.items())}")
        except Exception as e2:
            print(f"  ⚠️  Auto-weight also failed (using defaults): {e2}")

    # Extract Bayesian confidence (merge with credibility confidences)
    for r in all_model_results:
        if r.get("model_name") == "bayesian" and r.get("status") == "success":
            # Only override if credibility didn't provide it
            if "bayesian" not in model_confidences:
                model_confidences["bayesian"] = r.get("confidence", 1.0)

    # History tails (5 kỳ cùng thứ)
    recent_tails = get_recent_tails(db, "XSMB", [], target_date, limit_per_province=5)
    print(f"  📅 Lấy lịch sử 5 kỳ quay cùng thứ: {len(recent_tails)} số")

    # Extended tails (10 kỳ cho Toxic Gap)
    extended_tails = get_recent_tails(db, "XSMB", [], target_date, limit_per_province=10)
    print(f"  📅 Lấy lịch sử mở rộng 10 kỳ (Toxic Gap): {len(extended_tails)} số")

    # Last 7 calendar days tails for Recency Filter
    last_7_days_tails = get_last_7_days_tails(db, "XSMB", [], target_date)
    print(f"  📅 Lấy lịch sử 7 ngày gần nhất: {len(last_7_days_tails)} số")

    print(f"\n  {'='*50}")
    print(f"  🌍 XSMB ENSEMBLE v5.1")
    print(f"  {'='*50}")

    ensemble_output = compute_xsmb_ensemble(
        all_model_results, recent_tails,
        weights=auto_weights,
        top_n_output=3,
        extended_tails=extended_tails,
        model_confidences=model_confidences,
        last_7_days_tails=last_7_days_tails,
    )

    if not ensemble_output["top_pairs"]:
        raise RuntimeError("XSMB ensemble produced no candidates; all sub-models failed")

    top3_str = ", ".join(f"{p:02d}({s:.2f})" for p, s in ensemble_output["top_pairs"])
    consensus_str = ", ".join(f"{p:02d}" for p in ensemble_output.get("consensus_pairs", []))

    print(f"     ✅ Top 3 statistical signals: [{top3_str}]")
    print(f"     📊 Sources Active: {ensemble_output.get('models_active', 0)}/{TOTAL_MODELS_XSMB}")
    if consensus_str:
        print(f"     🤝 Consensus: [{consensus_str}]")

    # Additive combo-objective selector. Default mode is "off"; "shadow"
    # computes and logs an alternative without changing the saved legacy Top 3.
    combo_shadow_result = maybe_run_xsmb_combo_shadow(
        db,
        all_model_results,
        target_date,
        weights=ensemble_output.get("active_weights"),
    )
    combo_shadow_row = _xsmb_combo_shadow_row(combo_shadow_result)
    if combo_shadow_result is not None:
        try:
            combo_record = normalize_xsmb_combo_shadow(
                combo_shadow_result,
                target_date=target_date,
                execution_source=(
                    "github_actions"
                    if os.getenv("GITHUB_ACTIONS", "").lower() == "true"
                    else "local_manual"
                ),
            )
            saved = save_shadow_prediction(db, combo_record)
            if not saved:
                combo_shadow_row = None
                print(
                    "  ⚠️  XSMB combo shadow not persisted; "
                    "omitting it from Telegram"
                )
        except Exception as exc:
            combo_shadow_row = None
            print(
                "  ⚠️  XSMB combo shadow persistence failed; "
                f"champion preserved: {exc}"
            )

    # Save prediction
    prediction = xsmb_format_ensemble_result("XSMB", None, ensemble_output, target_date)
    prediction.pop('scoring_log', None)
    prediction.pop('candidate_log', None)
    save_prediction(db, prediction)

    # Telegram notification
    if prediction:
        active = ensemble_output.get('models_active', 0)
        active_weights = ensemble_output.get("active_weights", {})
        ordered_weights = {
            name: active_weights[name]
            for name in XSMB_ACTIVE_MODEL_NAMES
            if name in active_weights
        }
        successful_models = {
            result.get("model_name")
            for result in all_model_results
            if result.get("status") == "success"
        }
        missing_models = [
            XSMB_MODEL_SHORT_NAMES.get(name, name)
            for name in XSMB_ACTIVE_MODEL_NAMES
            if name not in successful_models
        ]
        selected_numbers = {number for number, _ in ensemble_output["top_pairs"][:3]}
        remaining_candidates = [
            int(candidate["pair"])
            for candidate in ensemble_output.get("top_candidates", [])
            if int(candidate["pair"]) not in selected_numbers
        ]
        message_kwargs = dict(
            region="XSMB",
            target_date=target_date,
            dow_label=get_dow_label(target_date),
            top_pairs=ensemble_output["top_pairs"],
            consensus_pairs=ensemble_output.get("consensus_pairs", []),
            remaining_candidates=remaining_candidates,
            models_active=active,
            models_total=TOTAL_MODELS_XSMB,
            version="Ensemble v5.1",
            active_weights=ordered_weights,
            model_labels=XSMB_MODEL_SHORT_NAMES,
            missing_by_scope={"XSMB": missing_models},
        )
        if combo_shadow_row is not None:
            message_kwargs["additional_shadows"] = (combo_shadow_row,)
        msg = format_compact_ensemble_message(**message_kwargs)

        if not await _send_chunked(notifier, msg, "predict_ensemble_xsmb"):
            raise RuntimeError("Telegram notification failed for XSMB")
        print(f"\n📱 Telegram notification sent for XSMB!")
        
        # --- NEW: Phân tích Lô Tô ---
        print("\n  🔍 Đang phân tích thống kê Lô Tô...")
        try:
            from src.xsmb_ensemble.xsmb_loto_analyzer import XSMBLotoAnalyzer
            from src.xsmb_ensemble.xsmb_loto_report import format_loto_report_telegram
            
            analyzer = XSMBLotoAnalyzer(db, target_date, lookback=100)
            loto_report = analyzer.generate_full_report()
            
            loto_messages = format_loto_report_telegram(loto_report, target_date)
            for loto_msg in loto_messages:
                if not await _send_chunked(notifier, loto_msg, "predict_ensemble_xsmb"):
                    print("  ⚠️ Gửi báo cáo Lô Tô thất bại!")
            print(f"  ✅ Đã gửi {len(loto_messages)} tin nhắn báo cáo Lô Tô!")
        except Exception as e:
            print(f"  ⚠️ Lỗi khi phân tích Lô Tô: {e}")

    print(f"\n✅ XSMB Ensemble v5.1 Prediction complete!")


async def run_xsmn_ensemble(
    target_date: date,
    provinces: list,
    db: LotteryDB,
    storage: LotteryStorage,
    notifier: LotteryNotifier,
    tmpdir: str,
    dry_run: bool = False,
):
    """
    XSMN v3.5 — 6-model merged combo ensemble.
    """
    print(f"\n{'='*60}")
    print(f"🎯 XSMN MULTI-MODEL ENSEMBLE (v3.5 — 6 Models)")
    print(f"📅 Target date: {target_date} ({get_dow_label(target_date)})")
    print(f"🏢 Target provinces ({len(provinces)}): {provinces}")
    print(f"{'='*60}")

    all_model_results = []
    provs_to_run = provinces if provinces else [None]

    for province in provs_to_run:
        results = await run_xsmn_models_for_target(db, storage, province, target_date, tmpdir)
        all_model_results.extend(results)

    # History (3 kỳ cùng thứ) — province-first để tránh trộn nhịp giữa các đài.
    recent_tails_by_province = {
        province: get_recent_tails(db, "XSMN", [province], target_date, limit_per_province=3)
        for province in provs_to_run
        if province is not None
    }
    recent_tails = [
        tail
        for tails in recent_tails_by_province.values()
        for tail in tails
    ]
    print(f"  📅 Lấy lịch sử 3 kỳ quay cùng thứ: {len(recent_tails)} số")

    # Credibility Scoring for XSMN (pre-prediction)
    xsmn_weights = None
    xsmn_credibility_log = ""
    try:
        xsmn_credibility = compute_credibility_scores(db, "XSMN", target_date)
        xsmn_weights = xsmn_credibility["credibility_weights"]
        xsmn_credibility_log = xsmn_credibility.get("scoring_log", "")
        print(xsmn_credibility_log)
    except Exception as e:
        print(f"  ⚠️  XSMN Credibility scoring failed (using defaults): {e}")

    print(f"\n  {'='*50}")
    print(f"  🌍 GLOBAL ENSEMBLE (XSMN)")
    print(f"  {'='*50}")

    recent_province_tails = get_recent_province_tails(
        db, "XSMN", provinces, target_date, max_days_back=3
    )
    combo_history_tail_sets = get_recent_merged_tail_sets_for_province_pair(
        db, "XSMN", provinces, target_date, limit=52
    )
    print(
        f"  📅 Lấy lịch sử combo merged cùng cặp tỉnh: "
        f"{len(combo_history_tail_sets)} kỳ"
    )

    ensemble_output = compute_xsmn_merged_combo_selector_ensemble(
        all_model_results,
        provinces=provs_to_run,
        recent_tails_by_province=recent_tails_by_province,
        top_n_output=3,
        representatives_per_province=2,
        weights=xsmn_weights,
        recent_province_tails=recent_province_tails,
        combo_history_tail_sets=combo_history_tail_sets,
    )
    ensemble_output["data_cutoff"] = target_date.isoformat()
    ensemble_output["model_versions"] = {
        f"{result.get('model_name')}@{result.get('province')}": result.get("model_version")
        for result in all_model_results
        if result.get("status") == "success"
    }

    if not ensemble_output["top_pairs"]:
        raise RuntimeError("XSMN ensemble produced no candidates")

    top3_str = ", ".join(f"{p:02d}({s:.2f})" for p, s in ensemble_output["top_pairs"])
    consensus_str = ", ".join(f"{p:02d}" for p in ensemble_output.get("consensus_pairs", []))

    print(f"     ✅ Top 3: [{top3_str}]")
    print(f"     📊 Contributing: {len(ensemble_output['contributing_models'])}")
    if consensus_str:
        print(f"     🤝 Consensus: [{consensus_str}]")

    # Save
    prediction = xsmn_format_ensemble_result("XSMN", "all", ensemble_output, target_date)
    prediction.pop('scoring_log', None)
    prediction.pop('candidate_log', None)

    if not dry_run:
        save_prediction(db, prediction)

    cmr_started_at = time.perf_counter()
    cmr_config = CMRConfig()
    try:
        cmr_result = generate_shadow_prediction(
            db,
            provs_to_run,
            target_date,
            cmr_config,
        )
        if cmr_result.get("status") == "success":
            print(f"     🧪 CMR shadow Top 3: {cmr_result['top_3']}")
        else:
            print(
                "     🧪 CMR shadow: "
                f"{cmr_result.get('reason', 'insufficient evidence')}"
            )
    except Exception as exc:
        reason = (" ".join(str(exc).split()) or "cmr_execution_failed")[:240]
        cmr_result = {"status": "error", "reason": reason}
        print(f"     ⚠️ CMR shadow failed without affecting ensemble: {reason}")

    cmr_runtime_ms = int((time.perf_counter() - cmr_started_at) * 1000)
    if not dry_run:
        try:
            cmr_record = normalize_shadow_prediction(
                cmr_result,
                model_name="cmr_shadow",
                target_date=target_date,
                provinces=provs_to_run,
                execution_source="production_post_save",
                runtime_ms=cmr_runtime_ms,
                config_metadata=asdict(cmr_config),
            )
            save_shadow_prediction(db, cmr_record)
        except Exception as exc:
            reason = (" ".join(str(exc).split()) or "cmr_persistence_failed")[:160]
            print(
                "     ⚠️ CMR shadow persistence failed; "
                f"production preserved: {reason}"
            )

    relationship_started_at = time.perf_counter()
    relationship_config = RelationshipConfig()
    try:
        relationship_result = generate_relationship_shadow(
            db,
            all_model_results,
            provs_to_run,
            target_date,
            relationship_config,
            family_weights=xsmn_weights,
        )
        if relationship_result.get("status") == "success":
            print(
                "     🧪 Relationship shadow Top 3: "
                f"{relationship_result['top_3']} "
                f"(score={relationship_result['relationship_score']:.4f})"
            )
        else:
            print(
                "     🧪 Relationship shadow: "
                f"{relationship_result.get('status', 'error')} — "
                f"{relationship_result.get('reason', 'insufficient evidence')}"
            )
    except Exception as exc:
        safe_detail = (
            sanitize_prediction_reason(exc) or "relationship_execution_failed"
        )
        relationship_result = {
            "status": "error",
            "reason": "relationship_execution_failed",
            "model_version": "relationship_v1",
            "score_semantics": "ranking_score_uncalibrated",
            "data_cutoff": target_date.isoformat(),
        }
        print(
            "     ⚠️ Relationship shadow failed without affecting ensemble: "
            f"{safe_detail}"
        )

    relationship_runtime_ms = int(
        (time.perf_counter() - relationship_started_at) * 1000
    )
    relationship_display_result = relationship_result
    if not dry_run:
        try:
            relationship_record = normalize_shadow_prediction(
                relationship_result,
                model_name="relationship",
                target_date=target_date,
                provinces=provs_to_run,
                execution_source="production_post_save",
                runtime_ms=relationship_runtime_ms,
                config_metadata=asdict(relationship_config),
            )
            relationship_saved = save_shadow_prediction(db, relationship_record)
            if relationship_saved:
                relationship_display_result = relationship_record
            else:
                stored_relationship = get_shadow_prediction(
                    db,
                    "relationship",
                    target_date,
                )
                if _relationship_scope_matches(
                    stored_relationship,
                    provs_to_run,
                ):
                    relationship_display_result = stored_relationship
                    print(
                        "     ℹ️ Relationship giữ nguyên bản ghi canonical "
                        "đã lưu trước đó"
                    )
                else:
                    relationship_display_result = {
                        "status": "error",
                        "reason": "relationship_ledger_unavailable",
                    }
        except Exception as exc:
            safe_detail = (
                sanitize_prediction_reason(exc) or "relationship_persistence_failed"
            )
            relationship_display_result = {
                "status": "error",
                "reason": "relationship_persistence_failed",
            }
            print(
                "     ⚠️ Relationship persistence failed; "
                f"production preserved: {safe_detail}"
            )
    relationship_row = _relationship_shadow_row(relationship_display_result)

    # A dry run must not create billable external side effects.
    llm_gen_result = None if dry_run else _run_llm_gen_shadow_safely(
        db,
        all_model_results,
        provs_to_run,
        target_date,
        ensemble_output=ensemble_output,
        recent_tails_by_province=recent_tails_by_province,
        recent_province_tails=recent_province_tails,
        combo_history_tail_sets=combo_history_tail_sets,
    )
    llm_gen_row = _llm_gen_shadow_row(llm_gen_result)
    if llm_gen_row is not None:
        if llm_gen_row.top_pairs:
            print(
                "     🧠 LLM_Gen shadow Top 3: "
                f"{[f'{pair:02d}' for pair, _ in llm_gen_row.top_pairs]}"
            )
        else:
            print(
                "     🧠 LLM_Gen shadow: "
                f"{llm_gen_row.status or 'Tạm không khả dụng'}"
            )

    # DDT is owned by the local Telegram worker. Production only reads its row.
    ddt_result = _generate_ddt_shadow_safely(db, provs_to_run, target_date)
    ddt_row = _ddt_shadow_row(ddt_result)
    if ddt_row.top_pairs:
        print(
            "     🧪 DDT shadow Top 3: "
            f"{[f'{pair:02d}' for pair, _ in ddt_row.top_pairs]}"
        )
    else:
        print(f"     🧪 DDT shadow: {ddt_row.status or 'Tạm không khả dụng'}")

    # Telegram
    if prediction:
        total_expected = len(provs_to_run) * TOTAL_MODELS_PER_PROVINCE
        active_count = len(ensemble_output['contributing_models'])
        missing_by_province = {}
        for prov in provs_to_run:
            missing = get_missing_models(db, "XSMN", prov, target_date, TOTAL_MODELS_PER_PROVINCE)
            if missing:
                missing_by_province[prov] = [
                    MODEL_SHORT_NAMES.get(name, name) for name in missing
                ]

        active_weights = ensemble_output.get("active_weights", {})
        ordered_weights = {
            name: active_weights[name]
            for name in EXPECTED_MODEL_NAMES["XSMN"]
            if name in active_weights
        }
        selected_numbers = {number for number, _ in ensemble_output["top_pairs"][:3]}
        remaining_candidates = [
            int(candidate["pair"])
            for candidate in ensemble_output.get("top_candidates", [])
            if int(candidate["pair"]) not in selected_numbers
        ]
        cmr_top_pairs = []
        cmr_status = None
        if cmr_result and cmr_result.get("status") == "success":
            cmr_top_pairs = [
                (
                    int(item["number"]),
                    float(item["estimated_hit_likelihood_uncalibrated"]),
                )
                for item in cmr_result.get("selected_evidence", [])
            ]
        elif cmr_result and cmr_result.get("status") == "insufficient_evidence":
            cmr_status = "Chưa đủ dữ liệu"
        elif cmr_result:
            cmr_status = "Tạm không khả dụng"
        additional_shadow_rows = [relationship_row]
        if llm_gen_row is not None:
            additional_shadow_rows.append(llm_gen_row)
        additional_shadow_rows.append(ddt_row)

        msg = format_compact_ensemble_message(
            region="XSMN",
            target_date=target_date,
            dow_label=get_dow_label(target_date),
            provinces=provs_to_run,
            province_labels=XSMNCrawler.PROVINCE_MAP,
            top_pairs=ensemble_output["top_pairs"],
            consensus_pairs=ensemble_output.get("consensus_pairs", []),
            remaining_candidates=remaining_candidates,
            models_active=active_count,
            models_total=total_expected,
            version="Ensemble v3.5",
            active_weights=ordered_weights,
            model_labels=MODEL_SHORT_NAMES,
            missing_by_scope=missing_by_province,
            shadow_top_pairs=cmr_top_pairs,
            shadow_status=cmr_status,
            additional_shadows=tuple(additional_shadow_rows),
        )

        if not dry_run:
            if not await _send_chunked(notifier, msg, "predict_ensemble_xsmn"):
                raise RuntimeError("Telegram notification failed for XSMN")
            print(f"\n📱 Telegram notification sent for XSMN!")
        else:
            print(f"\n[DRY-RUN] Would send telegram message:\n{msg}")

    print(f"\n✅ XSMN Ensemble Prediction complete!")


async def _send_chunked(notifier, msg: str, config_key: str) -> bool:
    """Send Telegram message, chunking if > 4000 chars."""
    max_len = 4000
    if len(msg) <= max_len:
        return await notifier.send_message(msg, config_key=config_key)

    current_chunk = ""
    for block in msg.split('\n\n'):
        split_by_line = False
        pending_blocks = [block]
        if len(block) > max_len:
            split_by_line = True
            pending_blocks = block.splitlines()

        for chunk in pending_blocks:
            separator = "\n" if split_by_line else "\n\n"
            extra_len = len(separator) if current_chunk else 0
            if len(current_chunk) + len(chunk) + extra_len > max_len:
                if current_chunk:
                    if not await notifier.send_message(current_chunk, config_key=config_key):
                        return False
                current_chunk = chunk
            else:
                current_chunk += (separator + chunk) if current_chunk else chunk

    if current_chunk:
        if not await notifier.send_message(current_chunk, config_key=config_key):
            return False

    return True


async def main():
    parser = argparse.ArgumentParser(description="Multi-Model Ensemble Prediction (XSMB v5.1 + XSMN v3.3)")
    parser.add_argument("--date", type=str, help="Ngày xếp hạng tín hiệu (YYYY-MM-DD). Mặc định = hôm nay")
    parser.add_argument("--dry-run", action="store_true", help="Chạy thử không lưu DB và không gửi tin nhắn Telegram")
    args = parser.parse_args()

    # Target date
    if args.date:
        target_date = date.fromisoformat(args.date)
    else:
        vn_now = datetime.utcnow() + timedelta(hours=7)
        target_date = vn_now.date()

    db = LotteryDB()
    storage = LotteryStorage()
    notifier = LotteryNotifier(db, default_config_key="predict_ensemble")

    with tempfile.TemporaryDirectory() as tmpdir:
        # XSMB — v5.1
        print(f"\n{'='*60}")
        print(f"🎯 BẮT ĐẦU CHẠY XSMB ENSEMBLE v5.1 (4 Models + Loto)")
        await run_xsmb_ensemble(target_date, db, storage, notifier, tmpdir, dry_run=args.dry_run)

        # XSMN — v3.5 (6 models, backward-compatible storage contract)
        xsmn_provinces = get_target_provinces(target_date)
        if xsmn_provinces:
            await run_xsmn_ensemble(target_date, xsmn_provinces, db, storage, notifier, tmpdir, dry_run=args.dry_run)
        else:
            print(f"⚠️  Không có province nào cho XSMN ngày {target_date}")

    print(f"\n{'='*60}")
    print(f"✅ ALL ENSEMBLE PREDICTIONS COMPLETE!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
