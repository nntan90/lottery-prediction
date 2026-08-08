---
title: 'XSMN LLM_Gen AgentRouter OpenAI-Compatible Backend'
type: 'bugfix'
created: '2026-08-08'
status: 'done'
review_loop_iteration: 0
baseline_commit: '1f284cf1e9b1e1e1737f8463a3fb90b4845f5ede'
context:
  - 'docs/project-context.md'
  - '_bmad-output/implementation-artifacts/spec-xsmn-llm-gen-provider-adapter.md'
  - '_bmad-output/implementation-artifacts/spec-test-openai-llm-gen-connection.md'
---

# XSMN LLM_Gen AgentRouter OpenAI-Compatible Backend

## Intent

### Problem

Ngày 07/08/2026, `LLM_Gen` đã được gọi nhưng kết thúc với `openai_http_401`. Secret hiện tại được tạo bởi AgentRouter, trong khi adapter đang gửi nó tới `https://api.openai.com/v1/responses`. AgentRouter yêu cầu gateway và wire protocol khác, nên daily pipeline không thể tạo dự đoán; Telegram chỉ hiển thị lý do chung chung.

### Approach

Giữ `LLM_GEN_PROVIDER=openai` và model cố định `gpt-5.6-sol`, đồng thời thêm backend OpenAI được allowlist: `official` dùng Responses API hiện tại, `agentrouter` dùng Chat Completions tại gateway AgentRouter. Backend, wire API và credential tương ứng phải được cấu hình, audit và kiểm tra canonical reuse độc lập. Chạy smoke test an toàn trước khi bật lại daily flow.

## Frozen Behavior Contract

### Always

- `LLM_GEN_OPENAI_BACKEND` chỉ nhận `official|agentrouter`, mặc định `official`; không nhận URL tùy ý.
- `official` gọi đúng `https://api.openai.com/v1/responses` bằng `OPENAI_API_KEY` và giữ nguyên request/parser hiện tại.
- `agentrouter` gọi đúng `https://co.agentrouter.org/v1/chat/completions` bằng `AGENTROUTER_API_KEY`; chỉ parse `choices[0].message.content`, sau đó dùng validator/schema hiện hữu.
- Logical provider vẫn là `openai`; model vẫn là `gpt-5.6-sol`; mỗi run chỉ đọc credential của backend/provider được chọn.
- Public metadata và canonical identity chứa `api_backend` và `wire_api`, nhưng không chứa key, authorization header, URL tùy ý hoặc raw response body.
- Giữ nguyên input evidence Top 2/model, output Top 3, diversity guard, shadow isolation, timeout và retry một lần chỉ cho timeout/429/5xx trên cùng backend.
- Chat response phải fail closed khi thiếu hoặc có nhiều choice không hợp lệ, content rỗng, refusal, JSON/schema sai, hoặc `finish_reason` cho biết bị cắt/chặn.
- Lỗi 401/403/model unavailable có stable reason rõ ràng cho DB và Telegram; không đưa raw exception/body vào tin nhắn.

### Ask First

- Đổi model nếu AgentRouter không cung cấp `gpt-5.6-sol`.
- Thêm gateway/provider khác, auto-discovery model trong production, cross-backend fallback, hoặc thay đổi DB schema.

### Never

- Gửi credential tới endpoint không nằm trong mapping cố định hoặc follow redirect mang authorization ra ngoài allowlist.
- Tự chuyển sang model khác, tự fallback từ AgentRouter sang OpenAI official, hoặc làm yếu schema/diversity validation.
- Log/persist key, header xác thực, danh sách model đầy đủ, hay provider response thô.

## Input / Output Matrix

| Input/state | Expected output |
|---|---|
| backend omitted / `official` | Regression-compatible OpenAI Responses call |
| backend `agentrouter` + valid key/model | One Chat Completions call, validated canonical LLM_Gen result |
| invalid backend or missing selected key | Config error before any network call |
| HTTP 401/403/4xx | Safe stable error row and actionable Telegram status; no retry except 429 |
| Empty/refusal/truncated/malformed response | Fail-closed error; no prediction promoted |
| Same input, different backend/wire API | Canonical conflict/new execution; never reuse incompatible success |

## Code Map and Tasks

- [x] `src/xsmn_llm_gen/config.py`: add backend enum, backend-specific key selection, public `api_backend`/`wire_api` metadata and validation.
- [x] `src/xsmn_llm_gen/providers.py`: preserve official adapter path; add exact AgentRouter Chat Completions request/parser, redirect protection and stable errors.
- [x] `src/xsmn_llm_gen/service.py`, `src/database/prediction_repo.py`: include backend identity in reuse/conflict decisions; redact all three LLM credential values.
- [x] `src/scripts/predict_ensemble.py`: synchronize fallback metadata and map stable AgentRouter errors to concise Telegram reasons.
- [x] `.env.example`, `.github/workflows/02-predict-ensemble.yml`, `.github/workflows/12-test-llm-gen-openai.yml`: expose backend selection and selected credential without leaking secrets. Smoke test checks `/v1/models` safely, then performs one real generation.
- [x] Tests: official regression, Chat request/response, exact endpoint/no redirects, invalid config, 401/429/timeout, model unavailable, malformed output, secret redaction, Telegram mapping, workflow wiring and canonical backend conflict.

## Acceptance Criteria

- [x] With `LLM_GEN_OPENAI_BACKEND=agentrouter`, an AgentRouter key is never sent to `api.openai.com`; official mode remains byte-for-behavior compatible.
- [x] Smoke returns success only when `gpt-5.6-sol` is available and one response passes the existing strict validator.
- [x] If the model is unavailable, rollout stops with `agentrouter_model_unavailable` and asks the user before any model change.
- [x] DB audit distinguishes official versus AgentRouter runs; Telegram exposes a safe operational reason; no credential appears in metadata, logs, DB reason or message.
- [x] Changing backend for the same date/input cannot silently reuse the previous canonical success.

## Verification

- Run focused LLM_Gen/provider/repository/Telegram/workflow tests, then `python3 -m pytest -q`.
- Run `python3 -m compileall -q src`, parse modified workflow YAML, and run `git diff --check`.
- Set GitHub variable `LLM_GEN_OPENAI_BACKEND=agentrouter`, add `AGENTROUTER_API_KEY`, execute the manual smoke workflow, then verify its safe summary plus the Supabase audit row before rerunning daily prediction.

## Suggested Review Order

**Configuration and credential routing**

- Start here: one logical provider selects only an allowlisted backend and credential.
  [`config.py:130`](../../src/xsmn_llm_gen/config.py#L130)

- Workflow injects only the secret selected by canonical mode/provider/backend values.
  [`02-predict-ensemble.yml:42`](../../.github/workflows/02-predict-ensemble.yml#L42)

**Provider security boundary**

- Central transport blocks unknown endpoints, redirects, and unsafe retry behavior.
  [`providers.py:246`](../../src/xsmn_llm_gen/providers.py#L246)

- AgentRouter request and response parsing remain strict and fail closed.
  [`providers.py:366`](../../src/xsmn_llm_gen/providers.py#L366)

- Smoke preflight verifies the fixed model without exposing the model list.
  [`providers.py:428`](../../src/xsmn_llm_gen/providers.py#L428)

**Audit and operator feedback**

- Service metadata records backend and wire identity for canonical reuse decisions.
  [`service.py:158`](../../src/xsmn_llm_gen/service.py#L158)

- Persistence rejects incompatible backend identities and redacts selected credentials.
  [`prediction_repo.py:426`](../../src/database/prediction_repo.py#L426)

- Telegram translates stable AgentRouter errors into actionable Vietnamese status.
  [`predict_ensemble.py:328`](../../src/scripts/predict_ensemble.py#L328)

**Smoke and regression coverage**

- Manual smoke reports only allowlisted fields and stops before unsupported generation.
  [`smoke_test_llm_gen_openai.py:188`](../../src/scripts/smoke_test_llm_gen_openai.py#L188)

- Adapter tests lock endpoint, parser, retry, redirect and malformed-response behavior.
  [`test_xsmn_llm_gen.py:402`](../../tests/test_xsmn_llm_gen.py#L402)

- Canonical tests prevent reuse across official OpenAI and AgentRouter backends.
  [`test_xsmn_llm_gen.py:809`](../../tests/test_xsmn_llm_gen.py#L809)

- Smoke tests lock model preflight and secret-safe summaries.
  [`test_llm_gen_openai_smoke.py:135`](../../tests/test_llm_gen_openai_smoke.py#L135)
