"""Focused tests for the XSMN LLM_Gen single-provider shadow."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json

import pytest
import requests

from src.xsmn_llm_gen import (
    AnthropicAdapter,
    LLMGenConfig,
    LLMGenConfigError,
    OpenAIAdapter,
    ProviderError,
    build_evidence_packet,
    compute_input_hash,
    create_provider_adapter,
    ensure_agentrouter_model_available,
    load_llm_gen_config,
    run_llm_gen,
    validate_ranked_candidates,
)


class TrackingEnv(dict):
    def __init__(self, values):
        super().__init__(values)
        self.reads = []

    def get(self, key, default=None):
        self.reads.append(key)
        return super().get(key, default)


class FakeResponse:
    def __init__(self, status_code: int, body: object):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def _model_results() -> list[dict]:
    return [
        {
            "status": "success",
            "model_name": "frequency",
            "model_version": "freq_v1",
            "province": "a",
            "top_pairs": [(11, 0.9), (25, 0.8), (99, 0.7)],
        },
        {
            "status": "success",
            "model_name": "frequency",
            "model_version": "freq_v1",
            "province": "b",
            "top_pairs": [(11, 0.85), (38, 0.75), (98, 0.6)],
        },
        {
            "status": "success",
            "model_name": "markov",
            "model_version": "markov_v1",
            "province": "a",
            "top_pairs": [(38, 0.95), (42, 0.7), (97, 0.5)],
        },
        {
            "status": "error",
            "model_name": "lstm",
            "province": "b",
            "top_pairs": [(77, 1.0)],
        },
    ]


def _provider_payload() -> dict:
    return {
        "ranked_candidates": [
            {
                "pair": 11,
                "rank": 1,
                "ranking_score_uncalibrated": 0.91,
                "evidence_codes": ["FAMILY_VOTE"],
                "risk_flags": [],
            },
            {
                "pair": 25,
                "rank": 2,
                "ranking_score_uncalibrated": 0.82,
                "evidence_codes": ["RANK_2"],
                "risk_flags": [],
            },
            {
                "pair": 38,
                "rank": 3,
                "ranking_score_uncalibrated": 0.73,
                "evidence_codes": ["COOCCURRENCE"],
                "risk_flags": [],
            },
        ]
    }


def _config(provider: str, *, backend: str = "official") -> LLMGenConfig:
    if provider == "openai":
        model = "gpt-5.6" if backend == "agentrouter" else "gpt-5.6-sol"
    else:
        model = "claude-opus-4-8"
    return LLMGenConfig(
        mode="shadow",
        provider=provider,
        provider_model=model,
        api_backend=backend if provider == "openai" else None,
        api_key=f"{provider}-{backend}-secret",
    )


def test_off_mode_reads_no_provider_or_key() -> None:
    env = TrackingEnv(
        {
            "LLM_GEN_MODE": "off",
            "LLM_GEN_PROVIDER": "openai",
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_API_KEY": "other",
        }
    )

    config = load_llm_gen_config(env)

    assert config.mode == "off"
    assert env.reads == ["LLM_GEN_MODE"]


def test_empty_workflow_mode_defaults_to_off() -> None:
    env = TrackingEnv({"LLM_GEN_MODE": "", "LLM_GEN_PROVIDER": "openai"})

    config = load_llm_gen_config(env)

    assert config.mode == "off"
    assert env.reads == ["LLM_GEN_MODE"]


@pytest.mark.parametrize(
    ("provider", "key_name", "unselected_keys", "model"),
    [
        (
            "openai",
            "OPENAI_API_KEY",
            ("AGENTROUTER_API_KEY", "ANTHROPIC_API_KEY"),
            "gpt-5.6-sol",
        ),
        (
            "anthropic",
            "ANTHROPIC_API_KEY",
            ("OPENAI_API_KEY", "AGENTROUTER_API_KEY"),
            "claude-opus-4-8",
        ),
    ],
)
def test_config_reads_only_selected_provider_key(
    provider: str,
    key_name: str,
    unselected_keys: tuple[str, ...],
    model: str,
) -> None:
    env = TrackingEnv(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": provider,
            "OPENAI_API_KEY": "openai-secret",
            "AGENTROUTER_API_KEY": "agentrouter-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
        }
    )

    config = load_llm_gen_config(env)

    assert config.provider_model == model
    assert key_name in env.reads
    assert all(key not in env.reads for key in unselected_keys)
    assert "secret" not in repr(config)
    assert "api_key" not in config.public_metadata()


def test_agentrouter_config_reads_only_its_key_and_audits_wire_identity() -> None:
    env = TrackingEnv(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            "OPENAI_API_KEY": "official-secret",
            "AGENTROUTER_API_KEY": "router-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
        }
    )

    config = load_llm_gen_config(env)

    assert config.provider == "openai"
    assert config.provider_model == "gpt-5.6"
    assert config.api_backend == "agentrouter"
    assert config.wire_api == "responses"
    assert "AGENTROUTER_API_KEY" in env.reads
    assert "OPENAI_API_KEY" not in env.reads
    assert "ANTHROPIC_API_KEY" not in env.reads
    assert config.public_metadata()["api_backend"] == "agentrouter"
    assert config.public_metadata()["wire_api"] == "responses"
    assert "router-secret" not in repr(config.public_metadata())


@pytest.mark.parametrize(
    ("provider", "backend"),
    (("openai", "official"), ("anthropic", "anthropic")),
)
def test_config_preserves_model_validation_before_missing_key(
    provider: str,
    backend: str,
) -> None:
    with pytest.raises(LLMGenConfigError, match="provider_model_mismatch"):
        LLMGenConfig(
            mode="shadow",
            provider=provider,
            provider_model="wrong-model",
            api_backend=backend,
            api_key=None,
        )


@pytest.mark.parametrize(
    "backend",
    ("https://untrusted.example/v1", "AgentRouter", " agentrouter "),
)
def test_invalid_openai_backend_fails_before_reading_any_key(
    backend: str,
) -> None:
    env = TrackingEnv(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": backend,
            "OPENAI_API_KEY": "official-secret",
            "AGENTROUTER_API_KEY": "router-secret",
        }
    )

    with pytest.raises(LLMGenConfigError, match="invalid_openai_backend"):
        load_llm_gen_config(env)

    assert "OPENAI_API_KEY" not in env.reads
    assert "AGENTROUTER_API_KEY" not in env.reads


@pytest.mark.parametrize(
    "values,reason",
    [
        ({"LLM_GEN_MODE": "invalid"}, "invalid_mode"),
        (
            {
                "LLM_GEN_MODE": " Shadow ",
                "LLM_GEN_PROVIDER": "openai",
                "OPENAI_API_KEY": "secret",
            },
            "invalid_mode",
        ),
        ({"LLM_GEN_MODE": "shadow", "LLM_GEN_PROVIDER": "other"}, "invalid_provider"),
        (
            {
                "LLM_GEN_MODE": "shadow",
                "LLM_GEN_PROVIDER": "OpenAI",
                "OPENAI_API_KEY": "secret",
            },
            "invalid_provider",
        ),
        ({"LLM_GEN_MODE": "shadow", "LLM_GEN_PROVIDER": "openai"}, "missing_api_key"),
        (
            {
                "LLM_GEN_MODE": "shadow",
                "LLM_GEN_PROVIDER": "openai",
                "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            },
            "missing_api_key",
        ),
    ],
)
def test_invalid_config_fails_closed(values: dict, reason: str) -> None:
    with pytest.raises(LLMGenConfigError, match=reason):
        load_llm_gen_config(values)


def test_evidence_uses_raw_top_two_dedupes_family_vote_and_does_not_mutate() -> None:
    results = _model_results()
    original = deepcopy(results)

    packet = build_evidence_packet(
        results,
        ["a", "b"],
        date(2026, 8, 4),
        effective_weights={"frequency": 0.6, "markov": 0.4},
        production_top_pairs=[(11, 1.0), (25, 0.9), (38, 0.8)],
        recent_tails_by_province={"a": [11, 11, 42], "b": [25]},
        recent_province_tails={"a": {11}, "b": {38}},
        combo_history_tail_sets=[{11, 25}, {11, 38}, {42}],
    )

    assert results == original
    assert all(len(source["eligible_top_2"]) <= 2 for source in packet["sources"])
    assert 99 not in {item["pair"] for item in packet["candidate_pool"]}
    assert 98 not in {item["pair"] for item in packet["candidate_pool"]}
    candidate_11 = next(item for item in packet["candidate_pool"] if item["pair"] == 11)
    assert candidate_11["family_vote_count"] == 1
    assert candidate_11["province_count"] == 2
    assert candidate_11["recent_same_weekday_tail_occurrences"] == 2
    assert candidate_11["digit_features"]["unit_digit"] == 1
    assert packet["cooccurrence_top"][0]["joint_hit_count"] == 1
    assert compute_input_hash(packet) == compute_input_hash(deepcopy(packet))


def test_evidence_rejects_fractional_pairs_dedupes_sources_and_preserves_zero_weight() -> None:
    results = _model_results()
    results[0]["top_pairs"] = [(11.9, 0.99), (25, 0.8), (38, 0.7)]
    results.append(deepcopy(results[1]))

    packet = build_evidence_packet(
        results,
        ["a", "b"],
        date(2026, 8, 4),
        effective_weights={"frequency": 0.0, "markov": 1.0},
    )

    assert len([source for source in packet["sources"] if source["source_id"] == "frequency@b"]) == 1
    assert "frequency@b:duplicate" in packet["skipped_sources"]
    assert packet["effective_weights"]["frequency"] == 0.0
    assert 11 not in {
        item["pair"]
        for item in packet["candidate_pool"]
        if item["source_ids"] == ["frequency@a"]
    }


def test_validator_filters_hallucinations_duplicates_and_enforces_suffixes() -> None:
    payload = {
        "ranked_candidates": [
            {"pair": 99, "rank": 1, "ranking_score_uncalibrated": 1.0},
            {"pair": 11, "rank": 2, "ranking_score_uncalibrated": 0.9},
            {"pair": 11, "rank": 3, "ranking_score_uncalibrated": 0.8},
            {"pair": 21, "rank": 4, "ranking_score_uncalibrated": 0.7},
            {"pair": 25, "rank": 5, "ranking_score_uncalibrated": 0.6},
            {"pair": 38, "rank": 6, "ranking_score_uncalibrated": 0.5},
        ]
    }

    result = validate_ranked_candidates(payload, {11, 21, 25, 38})

    assert result["status"] == "success"
    assert [item["pair"] for item in result["selected_evidence"]] == [11, 25, 38]
    assert len({item["pair"] % 10 for item in result["selected_evidence"]}) == 3


def test_validator_abstains_instead_of_relaxing_diversity() -> None:
    payload = {
        "ranked_candidates": [
            {"pair": pair, "rank": rank, "ranking_score_uncalibrated": 0.8}
            for rank, pair in enumerate((11, 21, 31), start=1)
        ]
    }

    result = validate_ranked_candidates(payload, {11, 21, 31})

    assert result["status"] == "insufficient_candidate_diversity"
    assert result["selected_evidence"] == []


def test_validator_rejects_fractional_boolean_and_oversized_response_values() -> None:
    malformed = {
        "ranked_candidates": [
            {"pair": 11.9, "rank": 1, "ranking_score_uncalibrated": 0.9},
            {"pair": 25, "rank": True, "ranking_score_uncalibrated": 0.8},
            {"pair": 38, "rank": 101, "ranking_score_uncalibrated": 0.7},
        ]
    }

    result = validate_ranked_candidates(malformed, {11, 25, 38})

    assert result["status"] == "insufficient_candidates"
    oversized = {"ranked_candidates": [_provider_payload()["ranked_candidates"][0]] * 11}
    assert validate_ranked_candidates(oversized, {11})["reason"] == "invalid_provider_schema"


def test_openai_adapter_calls_only_responses_endpoint_and_fixed_model() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        body = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": json.dumps(_provider_payload())}
                    ]
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        return FakeResponse(200, body)

    result = create_provider_adapter(_config("openai"), transport=transport).rank({"candidate_pool": []})

    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/responses")
    assert calls[0][1]["json"]["model"] == "gpt-5.6-sol"
    assert calls[0][1]["json"]["max_output_tokens"] == 2000
    assert calls[0][1]["json"]["store"] is False
    assert calls[0][1]["allow_redirects"] is False
    assert "Authorization" in calls[0][1]["headers"]
    assert "x-api-key" not in calls[0][1]["headers"]
    assert result.payload == _provider_payload()
    assert result.usage["input_tokens"] == 100


def test_agentrouter_adapter_uses_exact_responses_wire_and_usage() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(_provider_payload()),
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 101,
                    "output_tokens": 21,
                    "total_tokens": 122,
                },
            },
        )

    result = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=transport,
    ).rank({"candidate_pool": []})

    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://agentrouter.org/v1/responses"
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == (
        "Bearer openai-agentrouter-secret"
    )
    payload = kwargs["json"]
    assert payload["model"] == "gpt-5.6"
    assert payload["input"][0]["role"] == "system"
    assert payload["input"][1]["role"] == "user"
    assert payload["max_output_tokens"] == 2000
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert "messages" not in payload
    assert "response_format" not in payload
    assert result.payload == _provider_payload()
    assert result.usage == {
        "input_tokens": 101,
        "output_tokens": 21,
        "total_tokens": 122,
    }


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "agentrouter_incomplete"),
        (_provider_payload(), "agentrouter_incomplete"),
        ({"status": "completed", "output": []}, "agentrouter_empty_content"),
        (
            {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            "agentrouter_truncated",
        ),
        (
            {"status": "incomplete", "incomplete_details": {"reason": "other"}},
            "agentrouter_incomplete",
        ),
        ({"status": "failed"}, "agentrouter_response_failed"),
        ({"status": "queued"}, "agentrouter_incomplete"),
        (
            {
                "status": "completed",
                "output_text": json.dumps(_provider_payload()),
                "output": [
                    {
                        "content": [
                            {"type": "refusal", "refusal": "blocked"},
                        ]
                    }
                ],
            },
            "agentrouter_refusal",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {"type": "refusal", "refusal": "blocked"},
                        ]
                    }
                ],
            },
            "agentrouter_refusal",
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "content": [{"type": "output_text", "text": ""}],
                    }
                ],
            },
            "agentrouter_empty_content",
        ),
        (
            {
                "status": "completed",
                "output_text": "not-json",
            },
            "invalid_provider_json",
        ),
    ],
)
def test_agentrouter_responses_output_fails_closed(body: dict, reason: str) -> None:
    adapter = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=lambda *_a, **_k: FakeResponse(200, body),
    )

    with pytest.raises(ProviderError, match=reason):
        adapter.rank({})


def test_agentrouter_blank_output_text_falls_back_to_nested_output() -> None:
    body = {
        "status": "completed",
        "output_text": "  ",
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(_provider_payload()),
                    }
                ]
            }
        ],
    }
    result = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=lambda *_a, **_k: FakeResponse(200, body),
    ).rank({})

    assert result.payload == _provider_payload()


@pytest.mark.parametrize("status", [301, 401, 403])
def test_agentrouter_never_follows_redirect_or_retries_permanent_errors(
    status: int,
) -> None:
    calls = []

    def transport(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(status, {})

    adapter = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=transport,
    )
    expected = f"agentrouter_http_{status}"
    with pytest.raises(ProviderError, match=expected):
        adapter.rank({})

    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False


def test_adapter_rejects_unmapped_endpoint_before_sending_credential() -> None:
    calls = []
    adapter = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(ProviderError, match="endpoint_not_allowed"):
        adapter._post(
            endpoint="https://untrusted.example/v1/responses",
            headers={"Authorization": "Bearer openai-agentrouter-secret"},
            payload={},
        )

    assert calls == []


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (404, {}, "agentrouter_http_404"),
        (
            404,
            {"error": {"type": "model_not_found"}},
            "agentrouter_model_unavailable",
        ),
        (
            400,
            {"error": {"code": "model_not_available"}},
            "agentrouter_model_unavailable",
        ),
    ],
)
def test_agentrouter_model_errors_require_explicit_error_code(
    status: int,
    body: dict,
    reason: str,
) -> None:
    adapter = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=lambda *_a, **_k: FakeResponse(status, body),
    )

    with pytest.raises(ProviderError, match=reason):
        adapter.rank({})


def test_agentrouter_retries_429_once_on_same_endpoint() -> None:
    responses = [
        FakeResponse(429, {}),
        FakeResponse(
            200,
            {
                "status": "completed",
                "output_text": json.dumps(_provider_payload()),
            },
        ),
    ]
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    result = OpenAIAdapter(
        _config("openai", backend="agentrouter"),
        transport=transport,
    ).rank({})

    assert result.payload == _provider_payload()
    assert [call[0] for call in calls] == [
        "https://agentrouter.org/v1/responses",
        "https://agentrouter.org/v1/responses",
    ]


def test_anthropic_adapter_calls_only_messages_endpoint_and_fixed_model() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {
                "content": [{"type": "text", "text": json.dumps(_provider_payload())}],
                "usage": {"input_tokens": 90, "output_tokens": 18},
            },
        )

    result = create_provider_adapter(_config("anthropic"), transport=transport).rank({"candidate_pool": []})

    assert len(calls) == 1
    assert calls[0][0].endswith("/v1/messages")
    assert calls[0][1]["json"]["model"] == "claude-opus-4-8"
    assert calls[0][1]["json"]["max_tokens"] == 2000
    assert "x-api-key" in calls[0][1]["headers"]
    assert "Authorization" not in calls[0][1]["headers"]
    assert result.payload == _provider_payload()


def test_retry_is_once_and_only_for_transient_status() -> None:
    responses = [FakeResponse(429, {}), FakeResponse(200, _provider_payload())]
    calls = []

    def transport(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    OpenAIAdapter(_config("openai"), transport=transport).rank({})
    assert len(calls) == 2

    non_transient_calls = []

    def bad_request(*args, **kwargs):
        non_transient_calls.append((args, kwargs))
        return FakeResponse(400, {})

    with pytest.raises(ProviderError, match="openai_http_400"):
        OpenAIAdapter(_config("openai"), transport=bad_request).rank({})
    assert len(non_transient_calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"ranked_candidates": []},
        {
            "ranked_candidates": [
                {
                    **item,
                    "pair": 11.9 if index == 0 else item["pair"],
                }
                for index, item in enumerate(_provider_payload()["ranked_candidates"])
            ]
        },
        {**_provider_payload(), "unexpected": True},
    ],
)
def test_provider_runtime_rejects_structurally_invalid_json(payload: dict) -> None:
    with pytest.raises(ProviderError, match="invalid_provider_schema"):
        OpenAIAdapter(
            _config("openai"),
            transport=lambda *_a, **_k: FakeResponse(200, payload),
        ).rank({})


def test_timeout_retries_same_provider_once_without_fallback() -> None:
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args[0])
        raise requests.Timeout("secret must not be surfaced")

    with pytest.raises(ProviderError, match="openai_timeout"):
        OpenAIAdapter(_config("openai"), transport=timeout).rank({})

    assert calls == [
        "https://api.openai.com/v1/responses",
        "https://api.openai.com/v1/responses",
    ]


def test_agentrouter_model_preflight_uses_exact_endpoint_without_exposing_list() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            200,
            {"data": [{"id": "other-model"}, {"id": "gpt-5.6"}]},
        )

    result = ensure_agentrouter_model_available(
        _config("openai", backend="agentrouter"),
        transport=transport,
    )

    assert result is None
    assert len(calls) == 1
    assert calls[0][0] == "https://agentrouter.org/v1/models"
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["headers"]["Authorization"] == (
        "Bearer openai-agentrouter-secret"
    )


def test_agentrouter_model_preflight_fails_closed_when_model_is_missing() -> None:
    with pytest.raises(ProviderError, match="agentrouter_model_unavailable"):
        ensure_agentrouter_model_available(
            _config("openai", backend="agentrouter"),
            transport=lambda *_a, **_k: FakeResponse(
                200,
                {"data": [{"id": "another-model"}]},
            ),
        )


def test_agentrouter_model_preflight_retries_only_transient_failure() -> None:
    responses = [
        FakeResponse(500, {}),
        FakeResponse(200, {"data": [{"id": "gpt-5.6"}]}),
    ]
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return responses.pop(0)

    ensure_agentrouter_model_available(
        _config("openai", backend="agentrouter"),
        transport=transport,
    )

    assert [call[0] for call in calls] == [
        "https://agentrouter.org/v1/models",
        "https://agentrouter.org/v1/models",
    ]


def test_service_reuses_same_hash_before_provider_call() -> None:
    config = _config("openai")
    packet = build_evidence_packet(_model_results(), ["a", "b"], date(2026, 8, 4))
    input_hash = compute_input_hash(packet)
    existing = {
        "status": "success",
        "model_version": config.model_version,
        "pair_1": 11,
        "pair_2": 25,
        "pair_3": 38,
        "score_1": 0.91,
        "score_2": 0.82,
        "score_3": 0.73,
        "run_metadata": {
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "api_backend": "official",
            "wire_api": "responses",
            "prompt_version": config.prompt_version,
            "schema_version": config.schema_version,
            "model_version": config.model_version,
            "input_hash": input_hash,
            "provinces": ["a", "b"],
            "config": config.public_metadata(),
        },
    }

    def no_network(*args, **kwargs):
        raise AssertionError("provider must not be called")

    result = run_llm_gen(
        config,
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        lookup_success=lambda *_args: existing,
        transport=no_network,
    )

    assert result["status"] == "success"
    assert result["run_metadata"]["reused"] is True
    assert result["top_3"] == ["11", "25", "38"]


def test_service_never_reuses_same_input_across_openai_backends() -> None:
    official = _config("openai")
    agentrouter = _config("openai", backend="agentrouter")
    packet = build_evidence_packet(_model_results(), ["a", "b"], date(2026, 8, 4))
    existing = {
        "status": "success",
        "model_version": official.model_version,
        "pair_1": 11,
        "pair_2": 25,
        "pair_3": 38,
        "score_1": 0.91,
        "score_2": 0.82,
        "score_3": 0.73,
        "run_metadata": {
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "api_backend": "official",
            "wire_api": "responses",
            "prompt_version": official.prompt_version,
            "schema_version": official.schema_version,
            "model_version": official.model_version,
            "input_hash": compute_input_hash(packet),
            "provinces": ["a", "b"],
            "config": official.public_metadata(),
        },
    }

    result = run_llm_gen(
        agentrouter,
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        lookup_success=lambda *_args: existing,
        transport=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("provider must not run on canonical conflict")
        ),
    )

    assert result["status"] == "canonical_conflict"
    assert result["reason"] == "canonical_conflict"
    assert result["run_metadata"]["api_backend"] == "agentrouter"
    assert result["run_metadata"]["wire_api"] == "responses"


def test_service_rejects_legacy_agentrouter_chat_canonical() -> None:
    config = _config("openai", backend="agentrouter")
    packet = build_evidence_packet(_model_results(), ["a", "b"], date(2026, 8, 4))
    existing = {
        "status": "success",
        "model_version": config.model_version,
        "pair_1": 11,
        "pair_2": 25,
        "pair_3": 38,
        "score_1": 0.91,
        "score_2": 0.82,
        "score_3": 0.73,
        "run_metadata": {
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "api_backend": "agentrouter",
            "wire_api": "chat_completions",
            "prompt_version": config.prompt_version,
            "schema_version": config.schema_version,
            "model_version": config.model_version,
            "input_hash": compute_input_hash(packet),
            "provinces": ["a", "b"],
            "config": {
                **config.public_metadata(),
                "provider_model": "gpt-5.6-sol",
                "wire_api": "chat_completions",
            },
        },
    }

    result = run_llm_gen(
        config,
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        lookup_success=lambda *_args: existing,
        transport=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("provider must not run on canonical conflict")
        ),
    )

    assert result["status"] == "canonical_conflict"
    assert result["run_metadata"]["provider_model"] == "gpt-5.6"
    assert result["run_metadata"]["wire_api"] == "responses"


def test_existing_success_conflicts_before_insufficient_rerun_checks() -> None:
    existing = {
        "status": "success",
        "model_version": "llm_gen_v1",
        "pair_1": 11,
        "pair_2": 25,
        "pair_3": 38,
        "score_1": 0.9,
        "score_2": 0.8,
        "score_3": 0.7,
        "run_metadata": {
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "prompt_version": "llm_gen_prompt_v1",
            "schema_version": "llm_gen_response_v1",
            "model_version": "llm_gen_v1",
            "input_hash": "old-input",
            "config": _config("openai").public_metadata(),
        },
    }

    result = run_llm_gen(
        _config("openai"),
        [],
        ["a", "b"],
        date(2026, 8, 4),
        lookup_success=lambda *_args: existing,
        transport=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("provider must not run")
        ),
    )

    assert result["status"] == "canonical_conflict"


def test_service_conflict_does_not_call_provider_or_overwrite() -> None:
    existing = {
        "status": "success",
        "pair_1": 11,
        "pair_2": 25,
        "pair_3": 38,
        "score_1": 0.9,
        "score_2": 0.8,
        "score_3": 0.7,
        "run_metadata": {
            "provider": "anthropic",
            "provider_model": "claude-opus-4-8",
            "prompt_version": "old",
            "schema_version": "old",
            "input_hash": "different",
        },
    }

    result = run_llm_gen(
        _config("openai"),
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        lookup_success=lambda *_args: existing,
        transport=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not be called")
        ),
    )

    assert result["status"] == "canonical_conflict"
    assert result["selected_evidence"] == []


def test_service_calls_one_selected_provider_and_returns_auditable_top_three() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        return FakeResponse(
            200,
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": json.dumps(_provider_payload())}
                        ]
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    result = run_llm_gen(
        _config("openai"),
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        transport=transport,
    )

    assert result["status"] == "success"
    assert result["top_3"] == ["11", "25", "38"]
    assert result["score_semantics"] == "ranking_score_uncalibrated"
    assert result["run_metadata"]["provider"] == "openai"
    assert result["run_metadata"]["provider_model"] == "gpt-5.6-sol"
    assert result["run_metadata"]["api_backend"] == "official"
    assert result["run_metadata"]["wire_api"] == "responses"
    assert result["run_metadata"]["input_hash"]
    assert result["run_metadata"]["usage"]["input_tokens"] == 100
    assert calls == ["https://api.openai.com/v1/responses"]


def test_orchestration_off_mode_skips_schema_build_and_provider(monkeypatch) -> None:
    from src.scripts import predict_ensemble

    monkeypatch.setenv("LLM_GEN_MODE", "off")
    monkeypatch.setattr(
        predict_ensemble,
        "shadow_tracking_schema_ready",
        lambda _db: (_ for _ in ()).throw(AssertionError("preflight must not run")),
    )

    result = predict_ensemble._run_llm_gen_shadow_safely(
        object(),
        [],
        ["a", "b"],
        date(2026, 8, 4),
        ensemble_output={},
        recent_tails_by_province={},
        recent_province_tails={},
        combo_history_tail_sets=[],
    )

    assert result is None


def test_orchestration_missing_schema_skips_config_key_and_provider(monkeypatch) -> None:
    from src.scripts import predict_ensemble

    monkeypatch.setenv("LLM_GEN_MODE", "shadow")
    monkeypatch.setenv("LLM_GEN_PROVIDER", "openai")
    monkeypatch.setattr(predict_ensemble, "shadow_tracking_schema_ready", lambda _db: False)
    monkeypatch.setattr(
        predict_ensemble,
        "load_llm_gen_config",
        lambda: (_ for _ in ()).throw(AssertionError("key must not be read")),
    )
    monkeypatch.setattr(
        predict_ensemble,
        "run_llm_gen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )

    result = predict_ensemble._run_llm_gen_shadow_safely(
        object(),
        [],
        ["a", "b"],
        date(2026, 8, 4),
        ensemble_output={},
        recent_tails_by_province={},
        recent_province_tails={},
        combo_history_tail_sets=[],
    )

    assert result["status"] == "schema_not_ready"


def test_orchestration_config_error_persists_complete_non_secret_audit(monkeypatch) -> None:
    from src.scripts import predict_ensemble

    saved = []
    monkeypatch.setenv("LLM_GEN_MODE", "shadow")
    monkeypatch.setenv("LLM_GEN_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(predict_ensemble, "shadow_tracking_schema_ready", lambda _db: True)
    monkeypatch.setattr(
        predict_ensemble,
        "save_shadow_prediction",
        lambda _db, record: saved.append(record) or True,
    )

    result = predict_ensemble._run_llm_gen_shadow_safely(
        object(),
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        ensemble_output={"top_pairs": [(11, 1.0)], "effective_weights": {}},
        recent_tails_by_province={},
        recent_province_tails={},
        combo_history_tail_sets=[],
    )

    assert result["reason"] == "missing_api_key"
    metadata = saved[0]["run_metadata"]
    assert metadata["input_hash"]
    assert metadata["usage"] == {}
    assert metadata["latency_ms"] == 0
    assert metadata["api_backend"] == "official"
    assert metadata["wire_api"] == "responses"
    assert "api_key" not in str(metadata).lower()


def test_orchestration_save_failure_is_isolated(monkeypatch) -> None:
    from src.scripts import predict_ensemble

    config = _config("openai")
    payload = {
        "status": "success",
        "model_name": "llm_gen",
        "model_version": "llm_gen_v1",
        "score_semantics": "ranking_score_uncalibrated",
        "data_cutoff": "2026-08-04",
        "selected_evidence": [
            {"pair": 11, "rank": 1, "ranking_score_uncalibrated": 0.9},
            {"pair": 25, "rank": 2, "ranking_score_uncalibrated": 0.8},
            {"pair": 38, "rank": 3, "ranking_score_uncalibrated": 0.7},
        ],
        "run_metadata": {
            "provider": "openai",
            "provider_model": "gpt-5.6-sol",
            "prompt_version": config.prompt_version,
            "schema_version": config.schema_version,
            "input_hash": "hash",
            "latency_ms": 10,
        },
    }
    monkeypatch.setenv("LLM_GEN_MODE", "shadow")
    monkeypatch.setattr(predict_ensemble, "shadow_tracking_schema_ready", lambda _db: True)
    monkeypatch.setattr(predict_ensemble, "load_llm_gen_config", lambda: config)
    monkeypatch.setattr(predict_ensemble, "get_shadow_prediction", lambda *_a, **_k: None)
    monkeypatch.setattr(predict_ensemble, "run_llm_gen", lambda *_a, **_k: payload)
    monkeypatch.setattr(
        predict_ensemble,
        "normalize_shadow_prediction",
        lambda *_a, **_k: {"model_name": "llm_gen", "status": "success"},
    )
    monkeypatch.setattr(
        predict_ensemble,
        "save_shadow_prediction",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    result = predict_ensemble._run_llm_gen_shadow_safely(
        object(),
        _model_results(),
        ["a", "b"],
        date(2026, 8, 4),
        ensemble_output={"top_pairs": [(11, 1.0)], "effective_weights": {}},
        recent_tails_by_province={},
        recent_province_tails={},
        combo_history_tail_sets=[],
    )

    assert result["status"] == "error"
    assert result["reason"] == "llm_gen_execution_failed"


def test_prediction_shadow_row_displays_selected_provider() -> None:
    from src.scripts.predict_ensemble import _llm_gen_shadow_row

    row = _llm_gen_shadow_row(
        {
            "status": "success",
            "selected_evidence": [
                {"pair": 11, "ranking_score_uncalibrated": 0.9},
                {"pair": 25, "ranking_score_uncalibrated": 0.8},
                {"pair": 38, "ranking_score_uncalibrated": 0.7},
            ],
            "run_metadata": {
                "provider": "anthropic",
                "provider_model": "claude-opus-4-8",
            },
        }
    )

    assert row.label == "LLM_Gen [Claude Opus 4.8]"
    assert tuple(row.top_pairs) == ((11, 0.9), (25, 0.8), (38, 0.7))
    assert row.status == "điểm xếp hạng chưa calibration"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("agentrouter_http_401", "AgentRouter từ chối API key (401)"),
        ("agentrouter_http_403", "AgentRouter không cấp quyền model (403)"),
        ("agentrouter_model_unavailable", "AgentRouter chưa cấp GPT-5.6"),
        ("agentrouter_timeout", "AgentRouter hết thời gian chờ"),
        ("agentrouter_incomplete", "AgentRouter chưa hoàn tất Responses"),
    ],
)
def test_prediction_shadow_row_shows_safe_agentrouter_reason(
    reason: str,
    expected: str,
) -> None:
    from src.scripts.predict_ensemble import _llm_gen_shadow_row

    row = _llm_gen_shadow_row(
        {
            "status": "error",
            "reason": reason,
            "run_metadata": {
                "provider": "openai",
                "provider_model": "gpt-5.6",
                "api_backend": "agentrouter",
                "wire_api": "responses",
            },
        }
    )

    assert row.label == "LLM_Gen [AgentRouter · GPT-5.6]"
    assert row.status == expected
    assert "secret" not in row.status.lower()


def test_workflows_propagate_mode_and_serialize_prediction_runs() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    predict = (root / ".github/workflows/02-predict-ensemble.yml").read_text()
    verify = (root / ".github/workflows/03-verify-predictions.yml").read_text()
    weekly = (root / ".github/workflows/10-weekly-report.yml").read_text()

    assert "cancel-in-progress: false" in predict
    assert "LLM_GEN_OPENAI_BACKEND: ${{ vars.LLM_GEN_OPENAI_BACKEND }}" in predict
    assert "secrets.AGENTROUTER_API_KEY" in predict
    assert "vars.LLM_GEN_OPENAI_BACKEND == 'agentrouter'" in predict
    assert "LLM_GEN_MODE: ${{ vars.LLM_GEN_MODE }}" in verify
    assert "LLM_GEN_MODE: ${{ vars.LLM_GEN_MODE }}" in weekly
