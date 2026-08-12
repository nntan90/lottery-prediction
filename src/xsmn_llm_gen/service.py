"""Application service and deterministic output validator for ``LLM_Gen``."""

from __future__ import annotations

from datetime import date
import math
import time
from typing import Callable, Iterable, Mapping, Optional, Sequence

from .config import LLMGenConfig
from .evidence import build_evidence_packet, compute_input_hash
from .providers import ProviderError, Transport, create_provider_adapter


MODEL_NAME = "llm_gen"
SCORE_SEMANTICS = "ranking_score_uncalibrated"
LookupSuccess = Callable[[str, LLMGenConfig], Optional[Mapping[str, object]]]


def _safe_codes(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        " ".join(str(item).split())[:80]
        for item in value[:8]
        if str(item).strip()
    ]


def validate_ranked_candidates(
    payload: Mapping[str, object],
    candidate_pool: Iterable[int],
    *,
    max_candidates: int = 10,
) -> dict[str, object]:
    """Validate provider ranks and select three distinct unit digits.

    A pair outside the eligible pool fails the complete response closed so a
    provider cannot invent candidates while still earning a successful Top 3.
    Duplicate eligible ranks remain harmless and are collapsed deterministically.
    """
    allowed: set[int] = set()
    for raw_pair in candidate_pool:
        if isinstance(raw_pair, bool):
            continue
        try:
            pair = int(raw_pair)
        except (TypeError, ValueError):
            continue
        if 0 <= pair <= 99:
            allowed.add(pair)
    raw = payload.get("ranked_candidates")
    if not isinstance(raw, list):
        return {
            "status": "error",
            "reason": "invalid_provider_schema",
            "selected_evidence": [],
            "validated_ranking": [],
        }

    if len(raw) > max_candidates:
        return {
            "status": "error",
            "reason": "invalid_provider_schema",
            "selected_evidence": [],
            "validated_ranking": [],
        }

    normalized = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        raw_pair = item.get("pair")
        raw_rank = item.get("rank")
        raw_score = item.get("ranking_score_uncalibrated")
        if (
            isinstance(raw_pair, bool)
            or type(raw_pair) is not int
            or isinstance(raw_rank, bool)
            or type(raw_rank) is not int
            or isinstance(raw_score, bool)
            or type(raw_score) not in {int, float}
        ):
            continue
        pair = raw_pair
        rank = raw_rank
        score = float(raw_score)
        if pair not in allowed:
            return {
                "status": "error",
                "reason": "candidate_outside_pool",
                "selected_evidence": [],
                "validated_ranking": [],
            }
        if (
            rank < 1
            or rank > 100
            or not math.isfinite(score)
            or not 0.0 <= score <= 1.0
        ):
            continue
        normalized.append(
            {
                "pair": pair,
                "provider_rank": rank,
                "ranking_score_uncalibrated": round(score, 12),
                "evidence_codes": _safe_codes(item.get("evidence_codes")),
                "risk_flags": _safe_codes(item.get("risk_flags")),
                "_response_order": index,
            }
        )

    normalized.sort(
        key=lambda item: (
            int(item["provider_rank"]),
            int(item["_response_order"]),
            int(item["pair"]),
        )
    )
    ranking = []
    seen_pairs: set[int] = set()
    for item in normalized:
        pair = int(item["pair"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        ranking.append({key: value for key, value in item.items() if not key.startswith("_")})

    selected = []
    used_units: set[int] = set()
    for item in ranking:
        pair = int(item["pair"])
        if pair % 10 in used_units:
            continue
        selected.append(
            {
                **item,
                "rank": len(selected) + 1,
            }
        )
        used_units.add(pair % 10)
        if len(selected) == 3:
            break

    if len(selected) != 3:
        reason = (
            "insufficient_candidates"
            if len(ranking) < 3
            else "insufficient_candidate_diversity"
        )
        return {
            "status": reason,
            "reason": reason,
            "selected_evidence": [],
            "validated_ranking": ranking,
        }
    return {
        "status": "success",
        "reason": None,
        "selected_evidence": selected,
        "validated_ranking": ranking,
    }


def _base_result(
    config: LLMGenConfig,
    target_date: date | str,
    provinces: Sequence[str],
    input_hash: str,
    packet: Mapping[str, object],
) -> dict[str, object]:
    date_text = target_date.isoformat() if isinstance(target_date, date) else str(target_date)[:10]
    candidate_pool = [
        int(item["pair"])
        for item in packet.get("candidate_pool", [])
        if isinstance(item, Mapping) and item.get("pair") is not None
    ]
    return {
        "model_name": MODEL_NAME,
        "model_version": config.model_version,
        "mode": "shadow",
        "prediction_mode": "shadow",
        "score_semantics": SCORE_SEMANTICS,
        "target_date": date_text,
        "data_cutoff": date_text,
        "provinces": list(provinces),
        "provider_model": config.provider_model,
        "run_metadata": {
            "provider": config.provider,
            "provider_model": config.provider_model,
            "api_backend": config.api_backend,
            "wire_api": config.wire_api,
            "prompt_version": config.prompt_version,
            "schema_version": config.schema_version,
            "model_version": config.model_version,
            "input_hash": input_hash,
            "provinces": list(provinces),
            "data_cutoff": date_text,
            "history_cutoff_rule": "draw_date < target_date",
            "candidate_pool": candidate_pool,
            "active_model_families": list(packet.get("active_model_families", [])),
            "source_count": len(packet.get("sources", [])),
            "skipped_sources": list(packet.get("skipped_sources", [])),
            "usage": {},
            "latency_ms": 0,
            "reused": False,
            "config": config.public_metadata(),
        },
    }


def _reuse_or_conflict(
    existing: Mapping[str, object],
    base: dict[str, object],
    candidate_pool: set[int],
) -> dict[str, object]:
    metadata = existing.get("run_metadata")
    incoming_metadata = base["run_metadata"]
    if not isinstance(metadata, Mapping) or not isinstance(incoming_metadata, Mapping):
        return {**base, "status": "canonical_conflict", "reason": "canonical_conflict", "selected_evidence": []}
    identity_fields = (
        "provider",
        "provider_model",
        "api_backend",
        "wire_api",
        "prompt_version",
        "schema_version",
        "input_hash",
    )
    if any(metadata.get(field) != incoming_metadata.get(field) for field in identity_fields):
        return {**base, "status": "canonical_conflict", "reason": "canonical_conflict", "selected_evidence": []}
    if (
        existing.get("model_version") != base.get("model_version")
        or metadata.get("config") != incoming_metadata.get("config")
    ):
        return {**base, "status": "canonical_conflict", "reason": "canonical_conflict", "selected_evidence": []}

    ranked_candidates = []
    for rank in range(1, 4):
        pair = existing.get(f"pair_{rank}")
        score = existing.get(f"score_{rank}")
        ranked_candidates.append(
            {
                "pair": pair,
                "rank": rank,
                "ranking_score_uncalibrated": score,
                "evidence_codes": ["REUSED_CANONICAL"],
                "risk_flags": [],
            }
        )
    validated = validate_ranked_candidates(
        {"ranked_candidates": ranked_candidates},
        candidate_pool,
        max_candidates=3,
    )
    if validated["status"] != "success":
        return {**base, "status": "canonical_conflict", "reason": "invalid_canonical_success", "selected_evidence": []}
    reused_metadata = dict(metadata)
    reused_metadata["reused"] = True
    return {
        **base,
        "status": "success",
        "reason": None,
        "top_3": [f"{int(item['pair']):02d}" for item in validated["selected_evidence"]],
        "selected_evidence": validated["selected_evidence"],
        "run_metadata": reused_metadata,
    }


def run_llm_gen(
    config: LLMGenConfig,
    model_results: Iterable[Mapping[str, object]],
    provinces: Sequence[str],
    target_date: date | str,
    *,
    effective_weights: Optional[Mapping[str, float]] = None,
    production_top_pairs: Optional[Sequence[object]] = None,
    recent_tails_by_province: Optional[Mapping[str, Sequence[int]]] = None,
    recent_province_tails: Optional[Mapping[str, set[int]]] = None,
    combo_history_tail_sets: Optional[Iterable[Iterable[int]]] = None,
    lookup_success: Optional[LookupSuccess] = None,
    transport: Optional[Transport] = None,
) -> Optional[dict[str, object]]:
    """Run exactly one configured provider, or reuse the canonical success."""
    if config.mode == "off":
        return None

    packet = build_evidence_packet(
        model_results,
        provinces,
        target_date,
        effective_weights=effective_weights,
        production_top_pairs=production_top_pairs,
        recent_tails_by_province=recent_tails_by_province,
        recent_province_tails=recent_province_tails,
        combo_history_tail_sets=combo_history_tail_sets,
    )
    input_hash = compute_input_hash(packet)
    base = _base_result(config, target_date, provinces, input_hash, packet)
    candidate_pool = {
        int(item["pair"])
        for item in packet.get("candidate_pool", [])
        if isinstance(item, Mapping) and item.get("pair") is not None
    }

    if lookup_success is not None:
        existing = lookup_success(input_hash, config)
        if existing and str(existing.get("status") or "") in {"success", "uncalibrated"}:
            return _reuse_or_conflict(existing, base, candidate_pool)

    if len(candidate_pool) < 3:
        return {
            **base,
            "status": "insufficient_candidates",
            "reason": "insufficient_candidates",
            "selected_evidence": [],
        }
    if len({pair % 10 for pair in candidate_pool}) < 3:
        return {
            **base,
            "status": "insufficient_candidate_diversity",
            "reason": "insufficient_candidate_diversity",
            "selected_evidence": [],
        }

    started = time.perf_counter()
    try:
        adapter = create_provider_adapter(config, transport=transport)
        provider_result = adapter.rank(packet)
        validation = validate_ranked_candidates(
            provider_result.payload,
            candidate_pool,
            max_candidates=config.max_ranked_candidates,
        )
    except ProviderError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        metadata = dict(base["run_metadata"])
        metadata["latency_ms"] = latency_ms
        return {
            **base,
            "status": "error",
            "reason": exc.reason,
            "selected_evidence": [],
            "run_metadata": metadata,
        }
    except Exception:
        latency_ms = int((time.perf_counter() - started) * 1000)
        metadata = dict(base["run_metadata"])
        metadata["latency_ms"] = latency_ms
        return {
            **base,
            "status": "error",
            "reason": "provider_execution_failed",
            "selected_evidence": [],
            "run_metadata": metadata,
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    metadata = dict(base["run_metadata"])
    metadata.update(
        {
            "usage": provider_result.usage,
            "latency_ms": latency_ms,
            "validated_candidate_count": len(validation["validated_ranking"]),
        }
    )
    if validation["status"] != "success":
        return {
            **base,
            "status": validation["status"],
            "reason": validation["reason"],
            "selected_evidence": [],
            "run_metadata": metadata,
        }
    selected = validation["selected_evidence"]
    return {
        **base,
        "status": "success",
        "reason": None,
        "top_3": [f"{int(item['pair']):02d}" for item in selected],
        "selected_evidence": selected,
        "run_metadata": metadata,
    }
