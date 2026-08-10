"""Run a manual, data-side-effect-free OpenAI smoke test for ``LLM_Gen``.

The diagnostic uses the production provider adapter and structured-output
contract with a small synthetic evidence packet.  It never reads Supabase,
writes a prediction, or sends Telegram messages.  Output is intentionally
limited to non-secret status, latency, usage, and the validated Top 3.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import os
from typing import Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from src.xsmn_llm_gen import (
    LLMGenConfig,
    LLMGenConfigError,
    ProviderError,
    ensure_agentrouter_model_available,
    load_llm_gen_config,
    run_llm_gen,
)
from src.xsmn_llm_gen.config import (
    OPENAI_BACKEND_MODELS,
    OPENAI_BACKEND_WIRE_APIS,
    PROVIDER_MODELS,
)


Runner = Callable[..., Optional[dict[str, object]]]
ModelPreflight = Callable[[LLMGenConfig], None]
SMOKE_PROVINCES = ("smoke-province-a", "smoke-province-b")
SAFE_USAGE_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


def _safe_reason(value: object) -> Optional[str]:
    """Allow only stable internal reason codes in the public CI summary."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text and len(text) <= 80 and all(char.isalnum() or char == "_" for char in text):
        return text
    return "unsafe_or_unknown_error"


def _safe_config_error_identity(
    environ: Mapping[str, str],
) -> dict[str, Optional[str]]:
    """Expose only recognized non-secret identity after config rejection."""
    provider = str(environ.get("LLM_GEN_PROVIDER", "") or "").strip().lower()
    if provider == "openai":
        candidate = str(
            environ.get("LLM_GEN_OPENAI_BACKEND", "official") or "official"
        )
        return {
            "provider": provider,
            "provider_model": OPENAI_BACKEND_MODELS.get(candidate),
            "api_backend": (
                candidate if candidate in OPENAI_BACKEND_WIRE_APIS else None
            ),
            "wire_api": OPENAI_BACKEND_WIRE_APIS.get(candidate),
        }
    if provider == "anthropic":
        return {
            "provider": provider,
            "provider_model": PROVIDER_MODELS[provider],
            "api_backend": "anthropic",
            "wire_api": "messages",
        }
    return {
        "provider": None,
        "provider_model": None,
        "api_backend": None,
        "wire_api": None,
    }


def _safe_nonnegative_int(value: object) -> int:
    """Normalize provider metadata without allowing serialization surprises."""
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number >= 0 else 0


def _validated_top_3(value: object) -> list[str]:
    """Return exactly three unique pairs with distinct unit digits, or empty."""
    if not isinstance(value, list) or len(value) != 3:
        return []
    pairs: list[int] = []
    for raw_pair in value:
        if isinstance(raw_pair, bool) or not str(raw_pair).strip().isdigit():
            return []
        pair = int(raw_pair)
        if not 0 <= pair <= 99 or pair in pairs:
            return []
        pairs.append(pair)
    if len({pair % 10 for pair in pairs}) != 3:
        return []
    return [f"{pair:02d}" for pair in pairs]


def _smoke_model_results() -> list[dict[str, object]]:
    """Return a deterministic candidate pool with diverse unit digits."""
    return [
        {
            "status": "success",
            "model_name": "frequency",
            "model_version": "smoke_v1",
            "province": SMOKE_PROVINCES[0],
            "top_pairs": [(11, 0.91), (25, 0.84)],
        },
        {
            "status": "success",
            "model_name": "frequency",
            "model_version": "smoke_v1",
            "province": SMOKE_PROVINCES[1],
            "top_pairs": [(11, 0.88), (38, 0.79)],
        },
        {
            "status": "success",
            "model_name": "markov",
            "model_version": "smoke_v1",
            "province": SMOKE_PROVINCES[0],
            "top_pairs": [(38, 0.86), (42, 0.76)],
        },
        {
            "status": "success",
            "model_name": "xgboost",
            "model_version": "smoke_v1",
            "province": SMOKE_PROVINCES[1],
            "top_pairs": [(25, 0.83), (64, 0.72)],
        },
        {
            "status": "success",
            "model_name": "gap",
            "model_version": "smoke_v1",
            "province": SMOKE_PROVINCES[0],
            "top_pairs": [(42, 0.74), (73, 0.68)],
        },
    ]


def _safe_summary(
    result: Optional[Mapping[str, object]],
    config: Optional[LLMGenConfig],
    *,
    model_available: Optional[bool] = None,
) -> dict[str, object]:
    """Build the only payload allowed in CI logs; credentials are excluded."""
    value = result if isinstance(result, Mapping) else {}
    metadata = value.get("run_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    top_3 = _validated_top_3(value.get("top_3"))
    producer_success = value.get("status") == "success"
    status = "success" if producer_success and top_3 else "error"
    reason = value.get("reason")
    if producer_success and not top_3:
        reason = "invalid_smoke_top_3"
    raw_usage = metadata.get("usage")
    usage = {
        field: _safe_nonnegative_int(raw_usage[field])
        for field in SAFE_USAGE_FIELDS
        if isinstance(raw_usage, Mapping) and field in raw_usage
    }
    return {
        "ok": status == "success",
        "status": status,
        "reason": _safe_reason(reason),
        "provider": config.provider if config else None,
        "provider_model": config.provider_model if config else None,
        "api_backend": config.api_backend if config else None,
        "wire_api": config.wire_api if config else None,
        "model_available": model_available,
        "top_3": top_3,
        "score_semantics": (
            "ranking_score_uncalibrated"
            if value.get("score_semantics") == "ranking_score_uncalibrated"
            else None
        ),
        "latency_ms": _safe_nonnegative_int(metadata.get("latency_ms")),
        "usage": usage,
    }


def execute_smoke_test(
    environ: Optional[Mapping[str, str]] = None,
    *,
    runner: Runner = run_llm_gen,
    model_preflight: ModelPreflight = ensure_agentrouter_model_available,
    target_date: Optional[date] = None,
) -> tuple[int, dict[str, object]]:
    """Exercise one backend with its configured retry policy and safe output."""
    env = environ if environ is not None else os.environ
    try:
        config = load_llm_gen_config(env)
    except LLMGenConfigError as exc:
        public_identity = _safe_config_error_identity(env)
        return 2, {
            "ok": False,
            "status": "error",
            "reason": exc.reason,
            **public_identity,
            "model_available": None,
            "top_3": [],
            "score_semantics": None,
            "latency_ms": 0,
            "usage": {},
        }

    if config.provider != "openai":
        return 2, {
            "ok": False,
            "status": "error",
            "reason": "smoke_test_requires_openai",
            "provider": config.provider,
            "provider_model": config.provider_model,
            "api_backend": config.api_backend,
            "wire_api": config.wire_api,
            "model_available": None,
            "top_3": [],
            "score_semantics": None,
            "latency_ms": 0,
            "usage": {},
        }

    model_available: Optional[bool] = None
    if config.api_backend == "agentrouter":
        try:
            model_preflight(config)
            model_available = True
        except ProviderError as exc:
            return 1, _safe_summary(
                {"status": "error", "reason": exc.reason},
                config,
                model_available=(
                    False
                    if exc.reason == "agentrouter_model_unavailable"
                    else None
                ),
            )
        except Exception:
            return 1, _safe_summary(
                {
                    "status": "error",
                    "reason": "agentrouter_model_preflight_failed",
                },
                config,
                model_available=None,
            )

    try:
        result = runner(
            config,
            _smoke_model_results(),
            list(SMOKE_PROVINCES),
            target_date or datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date(),
            effective_weights={
                "frequency": 0.30,
                "markov": 0.25,
                "xgboost": 0.25,
                "gap": 0.20,
            },
            production_top_pairs=[(11, 1.0), (25, 0.9), (38, 0.8)],
            recent_tails_by_province={
                SMOKE_PROVINCES[0]: [11, 42],
                SMOKE_PROVINCES[1]: [25, 38],
            },
            recent_province_tails={
                SMOKE_PROVINCES[0]: {11, 42},
                SMOKE_PROVINCES[1]: {25, 38},
            },
            combo_history_tail_sets=[{11, 25}, {38, 42}, {25, 64}],
        )
    except Exception:
        return 1, {
            "ok": False,
            "status": "error",
            "reason": "smoke_runner_failed",
            "provider": config.provider,
            "provider_model": config.provider_model,
            "api_backend": config.api_backend,
            "wire_api": config.wire_api,
            "model_available": model_available,
            "top_3": [],
            "score_semantics": None,
            "latency_ms": 0,
            "usage": {},
        }
    summary = _safe_summary(
        result,
        config,
        model_available=model_available,
    )
    return (0 if summary["ok"] else 1), summary


def main() -> int:
    """Print one machine-readable, credential-safe diagnostic line."""
    exit_code, summary = execute_smoke_test()
    print(
        "LLM_GEN_OPENAI_SMOKE="
        + json.dumps(summary, ensure_ascii=False, sort_keys=True)
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
