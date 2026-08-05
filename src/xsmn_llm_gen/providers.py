"""Provider-neutral REST adapters for the ``LLM_Gen`` shadow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Mapping, Optional

import requests

from .config import LLMGenConfig


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
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

    def _post(self, *, headers: dict[str, str], payload: dict[str, object]) -> Mapping[str, object]:
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._transport(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
            except requests.Timeout as exc:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(f"{self.provider}_timeout") from exc
            except requests.RequestException as exc:
                raise ProviderError(f"{self.provider}_request_failed") from exc

            status = int(getattr(response, "status_code", 0) or 0)
            if status == 429 or 500 <= status <= 599:
                if attempt + 1 < attempts:
                    continue
                raise ProviderError(f"{self.provider}_http_{status}")
            if not 200 <= status <= 299:
                raise ProviderError(f"{self.provider}_http_{status or 'invalid'}")
            try:
                body = response.json()
            except (TypeError, ValueError) as exc:
                raise ProviderError("invalid_provider_json") from exc
            if not isinstance(body, Mapping):
                raise ProviderError("invalid_provider_schema")
            return body
        raise ProviderError(f"{self.provider}_request_failed")

    def rank(self, packet: Mapping[str, object]) -> ProviderResult:
        raise NotImplementedError


class OpenAIAdapter(_BaseAdapter):
    """OpenAI Responses API adapter fixed to ``gpt-5.6-sol``."""

    provider = "openai"
    endpoint = OPENAI_RESPONSES_URL

    def rank(self, packet: Mapping[str, object]) -> ProviderResult:
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
