---
title: 'Migrate XSMN LLM_Gen to the current AgentRouter Chat contract'
type: 'bugfix'
created: '2026-08-12'
status: 'done'
baseline_commit: 'f39e2f27845762d7af6f33f0fd31dff94d1b8fb4'
review_loop_iteration: 0
context:
  - 'docs/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** XSMN `LLM_Gen` is pinned to the obsolete AgentRouter host/Responses contract (`agentrouter.org`, `gpt-5.6`, `responses`). Production rows on 11–12/08 fail with `invalid_provider_json`, and the manual smoke preflight cannot parse the old `/models` response, so no LLM Top 3 is produced.

**Approach:** Migrate only the AgentRouter backend to the current documented OpenAI-compatible contract: fixed host `co.agentrouter.org`, exact model `gpt-5.5`, and Chat Completions. Keep OpenAI official, Anthropic, shadow isolation, deterministic Top 3 selection, persistence identity, and all existing production predictions unchanged.

## Boundaries & Constraints

**Always:** Send the selected raw `AGENTROUTER_API_KEY` only as `Authorization: Bearer …` to the fixed HTTPS endpoints `/v1/models` and `/v1/chat/completions`, with redirects disabled. Discover the exact configured model before the smoke generation call. Parse exactly one `choices[0].message.content`, then apply strict local JSON/schema/candidate-pool/diverse-unit validation. Preserve Top 2 per source, uncalibrated score semantics, one retry only for timeout/429/5xx, safe public errors, canonical audit fields (`provider`, `provider_model`, `api_backend`, `wire_api`), and shadow fault isolation.

**Ask First:** Using any model other than `gpt-5.5`; adding an endpoint/model override; falling back to another model, backend, or provider; relaxing validation; replacing a same-day canonical success; promoting LLM_Gen beyond shadow; changing database schema or dependencies.

**Never:** Send the AgentRouter key to the old host, follow redirects, auto-select the first discovered model, retry authentication/client/schema failures, expose the key/model list/raw provider body in logs or persistence, claim ranking scores are probabilities, or alter official OpenAI/Anthropic behavior.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Valid AgentRouter run | `/models` contains exact `gpt-5.5`; Chat returns one stopped choice with valid JSON | Validate and persist shadow Top 3 with identity `openai/agentrouter/gpt-5.5/chat_completions` | N/A |
| Model absent | Valid model list lacks exact `gpt-5.5` | Stop before generation; `model_available=false` | `agentrouter_model_unavailable`; no fallback |
| Preflight indeterminate | 401/403, transport failure, or malformed model response | Do not generate; `model_available=null` | Stable credential-safe reason |
| Invalid Chat response | Zero/multiple choices, refusal/filter, truncation, blank/fenced/malformed JSON, schema or candidate violation | No prediction success | Stable fail-closed AgentRouter reason |
| Transient provider failure | Timeout, 429, or 5xx | Retry once on the same endpoint | Return stable failure after retry |
| Existing canonical row | Prior error or success uses old AgentRouter identity | Error may be replaced by valid success; success remains immutable | Return `canonical_conflict` for existing success |

</frozen-after-approval>

## Code Map

- `src/xsmn_llm_gen/config.py` — backend-specific fixed model and wire identity.
- `src/xsmn_llm_gen/providers.py` — endpoint allowlist, preflight, Chat request/parser, retry and usage normalization.
- `src/scripts/smoke_test_llm_gen_openai.py` — preflight-first diagnostic and safe tri-state summary.
- `src/scripts/predict_ensemble.py` — public identity and Telegram model/error labels.
- `.github/workflows/12-test-llm-gen-openai.yml` — manual smoke wording and isolated secret routing.
- `.github/workflows/02-predict-ensemble.yml`, `.env.example` — regression-check daily routing and document the new fixed contract.
- `tests/test_xsmn_llm_gen.py`, `tests/test_llm_gen_openai_smoke.py`, `tests/test_prediction_repo.py`, `tests/test_ensemble_telegram_messages.py` — contract, parser, identity, smoke, and message regressions.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_llm_gen/config.py` — update only AgentRouter maps to `gpt-5.5`/`chat_completions`; retain the official OpenAI and Anthropic contracts.
- [x] `src/xsmn_llm_gen/providers.py` — replace only the AgentRouter adapter with fixed `co.agentrouter.org` Chat Completions and exact-model preflight; validate JSON locally because the current guide does not guarantee structured-output parameters.
- [x] `src/scripts/smoke_test_llm_gen_openai.py`, `src/scripts/predict_ensemble.py`, `src/database/prediction_repo.py` — preserve canonical persistence safeguards and align safe smoke/audit/Telegram identity with the new contract.
- [x] `.github/workflows/12-test-llm-gen-openai.yml`, `.github/workflows/02-predict-ensemble.yml`, `.env.example` — align wording/documentation and verify isolated selected-secret routing remains unchanged.
- [x] `tests/test_xsmn_llm_gen.py`, `tests/test_llm_gen_openai_smoke.py`, `tests/test_prediction_repo.py`, `tests/test_ensemble_telegram_messages.py` — cover positive, malformed, retry, security, identity, compatibility, and user-message behavior.

**Acceptance Criteria:**
- Given AgentRouter shadow mode, when configuration loads, then only `AGENTROUTER_API_KEY` is read and the identity is exactly `openai/agentrouter/gpt-5.5/chat_completions`.
- Given a successful preflight and valid Chat response, when LLM_Gen runs, then exactly three unique in-pool pairs with distinct unit digits are produced and stored as uncalibrated shadow output.
- Given any unsupported model or invalid provider response, when the run fails, then production ensemble execution continues and no false LLM success is persisted or announced.
- Given official OpenAI or Anthropic configuration, when their adapters run, then endpoints, models, wire payloads, and parsers remain behaviorally unchanged.

## Spec Change Log

## Design Notes

The model remains code-fixed rather than environment-selectable: the current portal documents `gpt-5.5`, while model availability is resource-pool-specific. `/models` is therefore a gate, not a model-selection mechanism. The Chat request uses documented fields (`model`, `messages`, `max_tokens`); the prompt requests raw JSON and existing strict local validation remains the trust boundary. Historical Responses-only error labels may remain renderable, but the new adapter must not emit them.

## Verification

**Commands:**
- `.venv/bin/python -m pytest -q tests/test_xsmn_llm_gen.py tests/test_llm_gen_openai_smoke.py tests/test_prediction_repo.py tests/test_ensemble_telegram_messages.py` — focused contract suite passes.
- `.venv/bin/python -m pytest -q` — full suite passes.
- `.venv/bin/python -m compileall -q src` — Python sources compile.
- Parse every `.github/workflows/*.yml` with the installed YAML runtime — workflow syntax remains valid.
- `git diff --check` — no whitespace errors.

## Suggested Review Order

**Wire and trust boundary**

- Fixed Chat endpoint and one-choice parser replace the obsolete Responses contract.
  [`providers.py:368`](../../src/xsmn_llm_gen/providers.py#L368)

- Candidate-pool violations now reject the entire provider response.
  [`service.py:30`](../../src/xsmn_llm_gen/service.py#L30)

- Exact-model discovery stops smoke generation when availability is unproven.
  [`providers.py:430`](../../src/xsmn_llm_gen/providers.py#L430)

**Identity and production isolation**

- Backend-specific mappings preserve official OpenAI while fixing AgentRouter identity.
  [`config.py:18`](../../src/xsmn_llm_gen/config.py#L18)

- Preflight-first smoke retains safe tri-state model availability.
  [`smoke_test_llm_gen_openai.py:191`](../../src/scripts/smoke_test_llm_gen_openai.py#L191)

- Telegram labels expose GPT-5.5 without changing production selection.
  [`predict_ensemble.py:356`](../../src/scripts/predict_ensemble.py#L356)

**Automation and regressions**

- Manual workflow names the exact model and wire being tested.
  [`12-test-llm-gen-openai.yml:37`](../../.github/workflows/12-test-llm-gen-openai.yml#L37)

- Contract tests pin request shape, endpoint, usage normalization, and parser behavior.
  [`test_xsmn_llm_gen.py:441`](../../tests/test_xsmn_llm_gen.py#L441)

- Regression proves invented candidates can never become a persisted Top 3.
  [`test_xsmn_llm_gen.py:1224`](../../tests/test_xsmn_llm_gen.py#L1224)
