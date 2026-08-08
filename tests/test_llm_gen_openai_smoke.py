"""Tests for the manual OpenAI connectivity diagnostic."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest
import yaml

from src.scripts import smoke_test_llm_gen_openai
from src.scripts.smoke_test_llm_gen_openai import execute_smoke_test
from src.xsmn_llm_gen import ProviderError


def _success_result() -> dict[str, object]:
    return {
        "status": "success",
        "reason": None,
        "top_3": ["11", "25", "38"],
        "score_semantics": "ranking_score_uncalibrated",
        "run_metadata": {
            "latency_ms": 321,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    }


def test_execute_smoke_test_uses_openai_without_leaking_key() -> None:
    captured = {}

    def runner(config, model_results, provinces, target_date, **kwargs):
        captured.update(
            {
                "provider": config.provider,
                "model": config.provider_model,
                "source_count": len(model_results),
                "provinces": provinces,
                "target_date": target_date,
                "kwargs": kwargs,
            }
        )
        return _success_result()

    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "OPENAI_API_KEY": "do-not-print-this-key",
        },
        runner=runner,
        target_date=date(2026, 8, 5),
    )

    assert exit_code == 0
    assert summary["ok"] is True
    assert summary["provider"] == "openai"
    assert summary["provider_model"] == "gpt-5.6-sol"
    assert summary["top_3"] == ["11", "25", "38"]
    assert summary["latency_ms"] == 321
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["source_count"] == 5
    assert captured["provinces"] == ["smoke-province-a", "smoke-province-b"]
    assert "do-not-print-this-key" not in json.dumps(summary)


def test_execute_smoke_test_fails_closed_without_key() -> None:
    exit_code, summary = execute_smoke_test(
        {"LLM_GEN_MODE": "shadow", "LLM_GEN_PROVIDER": "openai"},
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider must not run")
        ),
    )

    assert exit_code == 2
    assert summary["ok"] is False
    assert summary["reason"] == "missing_api_key"


def test_execute_smoke_test_rejects_malformed_success() -> None:
    malformed = _success_result()
    malformed["top_3"] = ["11", "21", "31"]

    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "OPENAI_API_KEY": "secret",
        },
        runner=lambda *_args, **_kwargs: malformed,
    )

    assert exit_code == 1
    assert summary["ok"] is False
    assert summary["reason"] == "invalid_smoke_top_3"
    assert summary["top_3"] == []


def test_execute_smoke_test_sanitizes_runner_failures() -> None:
    secret = "credential-must-not-appear"

    def failing_runner(*_args, **_kwargs):
        raise RuntimeError(f"Authorization: Bearer {secret}")

    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "OPENAI_API_KEY": secret,
        },
        runner=failing_runner,
    )

    assert exit_code == 1
    assert summary["reason"] == "smoke_runner_failed"
    assert secret not in json.dumps(summary)


def test_execute_smoke_test_rejects_non_openai_provider() -> None:
    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "secret",
        },
        runner=lambda *_args, **_kwargs: _success_result(),
    )

    assert exit_code == 2
    assert summary["reason"] == "smoke_test_requires_openai"


def test_agentrouter_smoke_preflights_model_before_generation() -> None:
    events = []

    def preflight(config):
        events.append(("preflight", config.api_backend, config.provider_model))

    def runner(config, *_args, **_kwargs):
        events.append(("generation", config.wire_api, config.provider_model))
        return _success_result()

    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            "AGENTROUTER_API_KEY": "router-key-must-not-appear",
        },
        model_preflight=preflight,
        runner=runner,
        target_date=date(2026, 8, 8),
    )

    assert exit_code == 0
    assert events == [
        ("preflight", "agentrouter", "gpt-5.6-sol"),
        ("generation", "chat_completions", "gpt-5.6-sol"),
    ]
    assert summary["api_backend"] == "agentrouter"
    assert summary["wire_api"] == "chat_completions"
    assert summary["model_available"] is True
    assert "router-key-must-not-appear" not in json.dumps(summary)


def test_agentrouter_smoke_stops_before_generation_when_model_unavailable() -> None:
    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            "AGENTROUTER_API_KEY": "router-secret",
        },
        model_preflight=lambda _config: (_ for _ in ()).throw(
            ProviderError("agentrouter_model_unavailable")
        ),
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not run")
        ),
    )

    assert exit_code == 1
    assert summary["ok"] is False
    assert summary["reason"] == "agentrouter_model_unavailable"
    assert summary["model_available"] is False


def test_agentrouter_smoke_requires_the_separate_selected_key() -> None:
    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "LLM_GEN_OPENAI_BACKEND": "agentrouter",
            "OPENAI_API_KEY": "official-key-must-not-be-used",
        },
        runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("generation must not run")
        ),
    )

    assert exit_code == 2
    assert summary["reason"] == "missing_api_key"
    assert summary["api_backend"] == "agentrouter"
    assert summary["wire_api"] == "chat_completions"


def test_summary_allowlists_reason_and_usage_fields() -> None:
    result = _success_result()
    result["status"] = "error"
    result["reason"] = "Authorization: Bearer must-not-appear"
    result["run_metadata"] = {
        "latency_ms": object(),
        "usage": {
            "input_tokens": 100,
            "output_tokens": "20",
            "secret_field": "must-not-appear",
        },
    }

    exit_code, summary = execute_smoke_test(
        {
            "LLM_GEN_MODE": "shadow",
            "LLM_GEN_PROVIDER": "openai",
            "OPENAI_API_KEY": "secret",
        },
        runner=lambda *_args, **_kwargs: result,
    )

    assert exit_code == 1
    assert summary["reason"] == "unsafe_or_unknown_error"
    assert summary["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert summary["latency_ms"] == 0
    assert "must-not-appear" not in json.dumps(summary)


def test_main_prints_only_machine_readable_safe_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    safe = {
        "ok": False,
        "status": "error",
        "reason": "openai_http_401",
        "provider": "openai",
        "provider_model": "gpt-5.6-sol",
        "top_3": [],
        "score_semantics": None,
        "latency_ms": 123,
        "usage": {},
    }
    monkeypatch.setattr(
        smoke_test_llm_gen_openai,
        "execute_smoke_test",
        lambda: (1, safe),
    )

    assert smoke_test_llm_gen_openai.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    prefix, payload = captured.out.strip().split("=", 1)
    assert prefix == "LLM_GEN_OPENAI_SMOKE"
    assert json.loads(payload) == safe


def test_workflow_is_manual_and_has_no_database_or_telegram_secrets() -> None:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/12-test-llm-gen-openai.yml"
    )
    workflow = workflow_path.read_text()
    parsed = yaml.load(workflow, Loader=yaml.BaseLoader)

    assert set(parsed["on"]) == {"workflow_dispatch"}
    assert parsed["permissions"] == {"contents": "read"}
    job = parsed["jobs"]["smoke-test"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && "
        "github.ref == 'refs/heads/main'"
    )
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] == "false"
    assert parsed["concurrency"]["cancel-in-progress"] == "true"
    assert "secrets.AGENTROUTER_API_KEY" in workflow
    assert "LLM_GEN_OPENAI_BACKEND: agentrouter" in workflow
    assert "secrets.OPENAI_API_KEY" not in workflow
    assert "actions/checkout@v4" not in workflow
    assert "actions/setup-python@v5" not in workflow
    assert "SUPABASE" not in workflow
    assert "TELEGRAM" not in workflow
