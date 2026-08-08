"""Environment-backed configuration for the XSMN ``LLM_Gen`` shadow."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping, Optional


MODE_ENV_VAR = "LLM_GEN_MODE"
PROVIDER_ENV_VAR = "LLM_GEN_PROVIDER"
OPENAI_BACKEND_ENV_VAR = "LLM_GEN_OPENAI_BACKEND"
VALID_MODES = frozenset({"off", "shadow"})
PROVIDER_MODELS = {
    "openai": "gpt-5.6-sol",
    "anthropic": "claude-opus-4-8",
}
OPENAI_BACKEND_WIRE_APIS = {
    "official": "responses",
    "agentrouter": "chat_completions",
}
OPENAI_BACKEND_KEY_ENV = {
    "official": "OPENAI_API_KEY",
    "agentrouter": "AGENTROUTER_API_KEY",
}


class LLMGenConfigError(ValueError):
    """Stable fail-closed configuration error without credential contents."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class LLMGenConfig:
    """Immutable single-provider configuration.

    The API key is deliberately excluded from repr/equality so it cannot leak
    through routine diagnostics or metadata snapshots.
    """

    mode: str = "off"
    provider: Optional[str] = None
    provider_model: Optional[str] = None
    api_backend: Optional[str] = None
    wire_api: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False, compare=False)
    timeout_seconds: float = 45.0
    max_retries: int = 1
    max_output_tokens: int = 2000
    max_ranked_candidates: int = 10
    top_pairs_per_source: int = 2
    prompt_version: str = "llm_gen_prompt_v1"
    schema_version: str = "llm_gen_response_v1"
    model_version: str = "llm_gen_v1"

    def __post_init__(self) -> None:
        mode = str(self.mode).strip().lower()
        if mode not in VALID_MODES:
            raise LLMGenConfigError("invalid_mode")
        object.__setattr__(self, "mode", mode)
        if mode == "off":
            if (
                self.provider is not None
                or self.provider_model is not None
                or self.api_backend is not None
                or self.wire_api is not None
                or self.api_key
            ):
                raise LLMGenConfigError("off_mode_must_not_configure_provider")
            return

        provider = str(self.provider or "").strip().lower()
        if provider not in PROVIDER_MODELS:
            raise LLMGenConfigError("invalid_provider")
        if self.provider_model != PROVIDER_MODELS[provider]:
            raise LLMGenConfigError("provider_model_mismatch")
        if not str(self.api_key or "").strip():
            raise LLMGenConfigError("missing_api_key")

        if provider == "openai":
            api_backend = str(self.api_backend or "official")
            if api_backend not in OPENAI_BACKEND_WIRE_APIS:
                raise LLMGenConfigError("invalid_openai_backend")
            expected_wire_api = OPENAI_BACKEND_WIRE_APIS[api_backend]
        else:
            api_backend = str(self.api_backend or "anthropic")
            if api_backend != "anthropic":
                raise LLMGenConfigError("provider_backend_mismatch")
            expected_wire_api = "messages"
        wire_api = str(self.wire_api or expected_wire_api)
        if wire_api != expected_wire_api:
            raise LLMGenConfigError("wire_api_mismatch")

        if self.timeout_seconds <= 0:
            raise LLMGenConfigError("invalid_timeout")
        if self.max_retries not in {0, 1}:
            raise LLMGenConfigError("invalid_retry_count")
        if not 256 <= self.max_output_tokens <= 4096:
            raise LLMGenConfigError("invalid_output_token_limit")
        if not 3 <= self.max_ranked_candidates <= 10:
            raise LLMGenConfigError("invalid_ranked_candidate_limit")
        if self.top_pairs_per_source != 2:
            raise LLMGenConfigError("top_pairs_per_source_must_equal_two")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "api_backend", api_backend)
        object.__setattr__(self, "wire_api", wire_api)

    def public_metadata(self) -> dict[str, object]:
        """Return a JSON-safe config snapshot that never includes the key."""
        return {
            "mode": self.mode,
            "provider": self.provider,
            "provider_model": self.provider_model,
            "api_backend": self.api_backend,
            "wire_api": self.wire_api,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "max_output_tokens": self.max_output_tokens,
            "max_ranked_candidates": self.max_ranked_candidates,
            "top_pairs_per_source": self.top_pairs_per_source,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "model_version": self.model_version,
        }


def load_llm_gen_config(
    environ: Optional[Mapping[str, str]] = None,
) -> LLMGenConfig:
    """Load mode and exactly one selected provider key.

    In ``off`` mode the provider and both key variables are intentionally never
    read. In ``shadow`` mode only the selected provider's key is accessed.
    """
    env = environ if environ is not None else os.environ
    raw_mode = str(env.get(MODE_ENV_VAR, "off") or "off")
    mode = raw_mode.strip().lower()
    if raw_mode != mode:
        raise LLMGenConfigError("invalid_mode")
    if mode not in VALID_MODES:
        raise LLMGenConfigError("invalid_mode")
    if mode == "off":
        return LLMGenConfig(mode="off")

    raw_provider = str(env.get(PROVIDER_ENV_VAR, "") or "")
    provider = raw_provider.strip().lower()
    if raw_provider != provider:
        raise LLMGenConfigError("invalid_provider")
    if provider not in PROVIDER_MODELS:
        raise LLMGenConfigError("invalid_provider")
    if provider == "openai":
        api_backend = str(env.get(OPENAI_BACKEND_ENV_VAR, "official") or "official")
        if api_backend not in OPENAI_BACKEND_WIRE_APIS:
            raise LLMGenConfigError("invalid_openai_backend")
        wire_api = OPENAI_BACKEND_WIRE_APIS[api_backend]
        key_env_var = OPENAI_BACKEND_KEY_ENV[api_backend]
    else:
        api_backend = "anthropic"
        wire_api = "messages"
        key_env_var = "ANTHROPIC_API_KEY"
    key = str(env.get(key_env_var, "") or "").strip()
    if not key:
        raise LLMGenConfigError("missing_api_key")
    return LLMGenConfig(
        mode="shadow",
        provider=provider,
        provider_model=PROVIDER_MODELS[provider],
        api_backend=api_backend,
        wire_api=wire_api,
        api_key=key,
    )
