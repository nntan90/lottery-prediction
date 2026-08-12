"""Provider-neutral REST adapters for the ``LLM_Gen`` shadow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Mapping, Optional

import requests

from .config import LLMGenConfig


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
AGENTROUTER_CHAT_COMPLETIONS_URL = (
    "https://co.agentrouter.org/v1/chat/completions"
)
AGENTROUTER_MODELS_URL = "https://co.agentrouter.org/v1/models"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

SYSTEM_PROMPT = """You rank only the supplied XSMN candidate pool.
Use the supplied model ranks, family votes, province coverage, measured history,
co-occurrence and digit features. Return JSON matching the schema. Never invent
a pair, never call an uncalibrated ranking score a probability, and do not
provide hidden reasoning or chain-of-thought. Use short evidence codes only.
Rank enough candidates for deterministic code to choose three distinct unit
digits."""

RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "ranked_candidates": {
            "type": "array",
            "minItems": 3,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "pair": {"type": "integer", "minimum": 0, "maximum": 99},
                    "rank": {"type": "integer", "minimum": 1, "maximum": 100},
                    "ranking_score_uncalibrated": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "evidence_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                    },
                    "risk_flags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 8,
                    },
                },
                "required": [
                    "pair",
                    "rank",
                    "ranking_score_uncalibrated",
                    "evidence_codes",
                    "risk_flags",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranked_candidates"],
    "additionalProperties": False,
}


class ProviderError(RuntimeError):
    """Credential-safe provider failure with a stable reason code."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ProviderResult:
    """Parsed structured output and provider usage only; no raw headers/body."""

    payload: dict[str, object]
    usage: dict[str, int]


Transport = Callable[..., Any]
ModelTransport = Callable[..., Any]


def _parse_json_object(text: object) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("empty_provider_response")
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ProviderError("invalid_provider_json") from exc
    if not isinstance(value, dict) or not isinstance(value.get("ranked_candidates"), list):
        raise ProviderError("invalid_provider_schema")
    return value


def _usage(body: Mapping[str, object]) -> dict[str, int]:
    raw = body.get("usage")
    if not isinstance(raw, Mapping):
        return {}
    result = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            result[str(key)] = number
    return result


def _agentrouter_chat_usage(body: Mapping[str, object]) -> dict[str, int]:
    """Normalize Chat token counters to the existing persistence vocabulary."""
    raw = body.get("usage")
    if not isinstance(raw, Mapping):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    result: dict[str, int] = {}
    for canonical, candidates in aliases.items():
        for key in candidates:
            value = raw.get(key)
            if isinstance(value, bool):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if number >= 0:
                result[canonical] = number
                break
    return result


def _agentrouter_http_reason(status: int, response: object) -> str:
    """Map only known AgentRouter error signals without exposing its body."""
    if status in {401, 403}:
        return f"agentrouter_http_{status}"
    try:
        body = response.json()  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        body = None
    error = body.get("error") if isinstance(body, Mapping) else None
    error_code = (
        str(error.get("code") or error.get("type") or "").strip().lower()
        if isinstance(error, Mapping)
        else ""
    )
    if error_code in {"invalid_model", "model_not_available", "model_not_found"}:
        return "agentrouter_model_unavailable"
    return f"agentrouter_http_{status or 'invalid'}"


def _validate_provider_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Enforce the provider response schema again at the trust boundary."""
    if set(payload) != {"ranked_candidates"}:
        raise ProviderError("invalid_provider_schema")
    candidates = payload.get("ranked_candidates")
    if not isinstance(candidates, list) or not 3 <= len(candidates) <= 10:
        raise ProviderError("invalid_provider_schema")
    required = {
        "pair",
        "rank",
        "ranking_score_uncalibrated",
        "evidence_codes",
        "risk_flags",
    }
    for item in candidates:
        if not isinstance(item, Mapping) or set(item) != required:
            raise ProviderError("invalid_provider_schema")
        pair = item.get("pair")
        rank = item.get("rank")
        score = item.get("ranking_score_uncalibrated")
        evidence_codes = item.get("evidence_codes")
        risk_flags = item.get("risk_flags")
        if (
            isinstance(pair, bool)
            or type(pair) is not int
            or not 0 <= pair <= 99
            or isinstance(rank, bool)
            or type(rank) is not int
            or not 1 <= rank <= 100
            or isinstance(score, bool)
            or type(score) not in {int, float}
            or not math.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
        ):
            raise ProviderError("invalid_provider_schema")
        for codes in (evidence_codes, risk_flags):
            if (
                not isinstance(codes, list)
                or len(codes) > 8
                or any(not isinstance(code, str) for code in codes)
            ):
                raise ProviderError("invalid_provider_schema")
    return {"ranked_candidates": [dict(item) for item in candidates]}


class _BaseAdapter:
    provider: str
    endpoint: str

    def __init__(
        self,
        config: LLMGenConfig,
        *,
        transport: Optional[Transport] = None,
    ) -> None:
        if config.mode != "shadow" or config.provider != self.provider:
            raise ProviderError("provider_adapter_mismatch")
        self.config = config
        self._transport = transport or requests.post

    @property
    def error_namespace(self) -> str:
        """Return the stable public namespace for transport failures."""
        if self.provider == "openai" and self.config.api_backend == "agentrouter":
            return "agentrouter"
        return self.provider

    @property
    def allowed_endpoints(self) -> frozenset[str]:
        """Return the fixed destination set for the selected credential."""
        if self.provider == "openai":
            endpoint = {
                "official": OPENAI_RESPONSES_URL,
                "agentrouter": AGENTROUTER_CHAT_COMPLETIONS_URL,
            }.get(self.config.api_backend)
            return frozenset({endpoint}) if endpoint else frozenset()
        if self.provider == "anthropic":
            return frozenset({ANTHROPIC_MESSAGES_URL})
        return frozenset()

    def _post(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        endpoint: Optional[str] = None,
    ) -> Mapping[str, object]:
        url = endpoint or self.endpoint
        if url not in self.allowed_endpoints:
            raise ProviderError("endpoint_not_allowed")
        namespace = self.error_namespace
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._transport(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                    allow_redirects=False,
                )
            except requests.Timeout as exc:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(f"{namespace}_timeout") from exc
            except requests.RequestException as exc:
                raise ProviderError(f"{namespace}_request_failed") from exc

            status = int(getattr(response, "status_code", 0) or 0)
            if status == 429 or 500 <= status <= 599:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(f"{namespace}_http_{status}")
            if not 200 <= status <= 299:
                reason = (
                    _agentrouter_http_reason(status, response)
                    if namespace == "agentrouter"
                    else f"{namespace}_http_{status or 'invalid'}"
                )
                raise ProviderError(reason)
            try:
                body = response.json()
            except (TypeError, ValueError) as exc:
                raise ProviderError("invalid_provider_json") from exc
            if not isinstance(body, Mapping):
                raise ProviderError("invalid_provider_schema")
            return body
        raise ProviderError(f"{namespace}_request_failed")

    def rank(self, packet: Mapping[str, object]) -> ProviderResult:
        raise NotImplementedError


class OpenAIAdapter(_BaseAdapter):
    """Fixed-model adapter for official and AgentRouter OpenAI wires."""

    provider = "openai"
    endpoint = OPENAI_RESPONSES_URL

    def rank(self, packet: Mapping[str, object]) -> ProviderResult:
        if self.config.api_backend == "agentrouter":
            return self._rank_agentrouter(packet)
        return self._rank_official(packet)

    def _rank_official(self, packet: Mapping[str, object]) -> ProviderResult:
        """Preserve the official OpenAI Responses request and parser."""
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.provider_model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        packet,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "llm_gen_response",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                }
            },
            "max_output_tokens": self.config.max_output_tokens,
            "store": False,
        }
        body = self._post(headers=headers, payload=payload)
        if isinstance(body.get("ranked_candidates"), list):
            parsed = dict(body)
        else:
            output_text = body.get("output_text")
            if not isinstance(output_text, str):
                texts = []
                outputs = body.get("output")
                for output in outputs if isinstance(outputs, list) else []:
                    if not isinstance(output, Mapping):
                        continue
                    contents = output.get("content")
                    for content in contents if isinstance(contents, list) else []:
                        if not isinstance(content, Mapping):
                            continue
                        if content.get("type") == "refusal":
                            raise ProviderError("openai_refusal")
                        if content.get("type") in {"output_text", "text"}:
                            if isinstance(content.get("text"), str):
                                texts.append(str(content["text"]))
                output_text = "".join(texts)
            parsed = _parse_json_object(output_text)
        parsed = _validate_provider_payload(parsed)
        return ProviderResult(payload=parsed, usage=_usage(body))

    def _rank_agentrouter(self, packet: Mapping[str, object]) -> ProviderResult:
        """Call the fixed AgentRouter Chat endpoint and parse one stopped choice."""
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.provider_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "evidence_packet": packet,
                            "response_schema": RESPONSE_SCHEMA,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            ],
            "max_tokens": self.config.max_output_tokens,
        }
        body = self._post(
            endpoint=AGENTROUTER_CHAT_COMPLETIONS_URL,
            headers=headers,
            payload=payload,
        )
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ProviderError("agentrouter_invalid_choice_count")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProviderError("agentrouter_invalid_choice")
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError("agentrouter_invalid_choice")

        finish_reason = str(choice.get("finish_reason") or "").strip().lower()
        refusal = message.get("refusal")
        if finish_reason == "content_filter" or (
            refusal is not None and str(refusal).strip()
        ):
            raise ProviderError("agentrouter_refusal")
        if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
            raise ProviderError("agentrouter_truncated")
        if finish_reason != "stop":
            raise ProviderError("agentrouter_invalid_finish_reason")

        output_text = message.get("content")
        if not isinstance(output_text, str) or not output_text.strip():
            raise ProviderError("agentrouter_empty_content")
        parsed = _parse_json_object(output_text)
        parsed = _validate_provider_payload(parsed)
        return ProviderResult(
            payload=parsed,
            usage=_agentrouter_chat_usage(body),
        )


def ensure_agentrouter_model_available(
    config: LLMGenConfig,
    *,
    transport: Optional[ModelTransport] = None,
) -> None:
    """Fail closed unless the selected AgentRouter key exposes the fixed model.

    The model list is inspected in memory only. It is never returned, logged, or
    persisted, and redirects are disabled so the bearer credential cannot leave
    the fixed AgentRouter origin.
    """
    if (
        config.mode != "shadow"
        or config.provider != "openai"
        or config.api_backend != "agentrouter"
        or config.wire_api != "chat_completions"
    ):
        raise ProviderError("agentrouter_preflight_config_mismatch")

    request = transport or requests.get
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Accept": "application/json",
    }
    attempts = config.max_retries + 1
    for attempt in range(attempts):
        try:
            response = request(
                AGENTROUTER_MODELS_URL,
                headers=headers,
                timeout=config.timeout_seconds,
                allow_redirects=False,
            )
        except requests.Timeout as exc:
            if attempt + 1 < attempts:
                continue
            raise ProviderError("agentrouter_timeout") from exc
        except requests.RequestException as exc:
            raise ProviderError("agentrouter_request_failed") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status == 429 or 500 <= status <= 599:
            if attempt + 1 < attempts:
                continue
            raise ProviderError(f"agentrouter_http_{status}")
        if not 200 <= status <= 299:
            raise ProviderError(_agentrouter_http_reason(status, response))
        try:
            body = response.json()
        except (TypeError, ValueError) as exc:
            raise ProviderError("agentrouter_invalid_models_response") from exc
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(data, list):
            raise ProviderError("agentrouter_invalid_models_response")
        available = {
            str(item.get("id") or "")
            for item in data
            if isinstance(item, Mapping) and item.get("id") is not None
        }
        if config.provider_model not in available:
            raise ProviderError("agentrouter_model_unavailable")
        return
    raise ProviderError("agentrouter_request_failed")


class AnthropicAdapter(_BaseAdapter):
    """Anthropic Messages API adapter fixed to ``claude-opus-4-8``."""

    provider = "anthropic"
    endpoint = ANTHROPIC_MESSAGES_URL

    def rank(self, packet: Mapping[str, object]) -> ProviderResult:
        headers = {
            "x-api-key": str(self.config.api_key),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.provider_model,
            "max_tokens": self.config.max_output_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": json.dumps(
                        packet,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": RESPONSE_SCHEMA,
                }
            },
        }
        body = self._post(headers=headers, payload=payload)
        if str(body.get("stop_reason") or "") == "refusal":
            raise ProviderError("anthropic_refusal")
        if isinstance(body.get("ranked_candidates"), list):
            parsed = dict(body)
        else:
            texts = []
            contents = body.get("content")
            for content in contents if isinstance(contents, list) else []:
                if not isinstance(content, Mapping):
                    continue
                if content.get("type") == "refusal":
                    raise ProviderError("anthropic_refusal")
                if content.get("type") == "text" and isinstance(content.get("text"), str):
                    texts.append(str(content["text"]))
            parsed = _parse_json_object("".join(texts))
        parsed = _validate_provider_payload(parsed)
        return ProviderResult(payload=parsed, usage=_usage(body))


def create_provider_adapter(
    config: LLMGenConfig,
    *,
    transport: Optional[Transport] = None,
) -> _BaseAdapter:
    """Instantiate exactly the configured provider; never fall back."""
    if config.provider == "openai":
        return OpenAIAdapter(config, transport=transport)
    if config.provider == "anthropic":
        return AnthropicAdapter(config, transport=transport)
    raise ProviderError("invalid_provider")
