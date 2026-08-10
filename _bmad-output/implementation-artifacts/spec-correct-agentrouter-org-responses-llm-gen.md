---
title: 'Correct AgentRouter Responses contract for XSMN LLM_Gen'
type: 'bugfix'
created: '2026-08-10'
status: 'done'
baseline_commit: '99623e63abccd250de579a6d2537bed71fd16087'
review_loop_iteration: 0
context:
  - 'docs/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** LLM_Gen đang gửi `AGENTROUTER_API_KEY` do `agentrouter.org` cấp tới `co.agentrouter.org`, dùng Chat Completions và model `gpt-5.6-sol`; preflight vì vậy trả 401 trước khi có thể xác nhận model. Cấu hình này không khớp hướng dẫn AgentRouter Codex mà người dùng cung cấp.

**Approach:** Giữ logical provider `openai` và backend selector `agentrouter`, nhưng dùng đúng contract cố định `https://agentrouter.org/v1`: Responses API, model `gpt-5.6`, và secret hiện có `AGENTROUTER_API_KEY`. Tách model theo backend để OpenAI official và Anthropic không đổi hành vi.

## Boundaries & Constraints

**Always:** AgentRouter chỉ được GET `https://agentrouter.org/v1/models` và POST `https://agentrouter.org/v1/responses`; dùng `Authorization: Bearer <AGENTROUTER_API_KEY>`, chặn redirect và URL tùy chỉnh. Audit/smoke/Telegram phải thống nhất `provider=openai`, `api_backend=agentrouter`, `wire_api=responses`, `provider_model=gpt-5.6`. Giữ Top 2 đầu vào mỗi source, Top 3 đầu ra thuộc candidate pool với ba hàng đơn vị khác nhau, strict schema, shadow isolation, retry tối đa một lần cho timeout/429/5xx, redaction và canonical identity. OpenAI official vẫn dùng model/endpoint/request hiện tại; Anthropic giữ nguyên.

**Ask First:** Đổi sang model khác như `gpt-5.5`, fallback provider/backend, bỏ preflight smoke, nới validator/schema, thay canonical success đã tồn tại, hoặc chạy lại daily production có thể gửi Telegram trùng.

**Never:** Gửi AgentRouter key tới `co.agentrouter.org`, `api.openai.com` hoặc host khác; silent fallback; log/persist secret, raw response lỗi hay danh sách model; coi điểm LLM là xác suất đã calibration; sửa contract public của các function cũ.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| AgentRouter hợp lệ | Key hợp lệ; `/models` có `gpt-5.6`; Responses trả JSON đúng schema | Smoke preflight trước, gọi đúng một generation, Top 3 hợp lệ; metadata/Telegram ghi GPT-5.6 + Responses | Không lộ key/body/model list |
| Auth hoặc transport lỗi | 401/403/timeout/429/5xx/redirect | Không fallback; retry chỉ timeout/429/5xx theo policy | Stable `agentrouter_*`; `model_available=null` trừ khi chứng minh model thiếu |
| Model thiếu | `/models` 200 nhưng không có chính xác `gpt-5.6` | Dừng smoke trước generation | `agentrouter_model_unavailable`, `model_available=false` |
| Response không dùng được | refusal, incomplete/truncated, rỗng, JSON/schema sai hoặc thiếu đa dạng | Không lưu success và không tạo Top 3 giả | Fail closed bằng reason code an toàn |
| Backend official | `LLM_GEN_OPENAI_BACKEND=official` | Endpoint/model/wire/payload/parser hiện tại không đổi | Regression test khóa tương thích |
| Canonical cũ | Cùng input nhưng metadata AgentRouter cũ là Chat Completions/model cũ | Không reuse hoặc overwrite success cũ | Trả `canonical_conflict` |

</frozen-after-approval>

## Code Map

- `src/xsmn_llm_gen/config.py` -- resolve model và wire theo OpenAI backend, giữ selected-key-only.
- `src/xsmn_llm_gen/providers.py` -- endpoint allowlist, Responses request/parser, preflight và lỗi ổn định.
- `src/xsmn_llm_gen/service.py` -- metadata/canonical reuse; dự kiến giữ logic, khóa bằng test.
- `src/scripts/smoke_test_llm_gen_openai.py` -- public identity và preflight/generation summary.
- `src/scripts/predict_ensemble.py` -- identity khi config lỗi, Telegram label/reason.
- `.github/workflows/12-test-llm-gen-openai.yml` -- mô tả đúng Responses smoke; giữ manual/main-only.
- `.env.example` -- tài liệu backend không chứa secret/URL tùy chỉnh.
- `tests/test_xsmn_llm_gen.py`, `tests/test_llm_gen_openai_smoke.py`, `tests/test_prediction_repo.py` -- contract, security, persistence và compatibility regressions.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_llm_gen/config.py` -- thêm mapping model theo backend; giữ public config API và official/Anthropic.
- [x] `src/xsmn_llm_gen/providers.py` -- chuyển riêng AgentRouter sang allowlisted Responses + `gpt-5.6`, dùng parser fail-closed có namespace đúng; cập nhật model preflight.
- [x] `src/scripts/smoke_test_llm_gen_openai.py`, `src/scripts/predict_ensemble.py` -- đồng bộ identity, model availability và Telegram wording.
- [x] `.github/workflows/12-test-llm-gen-openai.yml`, `.env.example` -- sửa operational documentation; không đổi secret routing daily.
- [x] `tests/` -- cập nhật fixtures và bao phủ toàn bộ matrix, bao gồm official byte-for-behavior và canonical conflict.

**Acceptance Criteria:**
- Given AgentRouter được chọn, mọi request chứa selected key chỉ tới `agentrouter.org`, model gửi đi là `gpt-5.6`, wire/audit là `responses`, và không còn đường chạy tới `co.agentrouter.org`.
- Given preflight thành công, smoke chỉ báo `ok=true` sau một Responses call và Top 3 qua validator; khi preflight thất bại generation không chạy.
- Given OpenAI official hoặc Anthropic được chọn, behavior, credential selection và public function signatures không đổi.
- Given lỗi provider hoặc output không hợp lệ, daily shadow vẫn fault-tolerant và chỉ persist/render reason an toàn.

## Verification

**Commands:**
- `.venv/bin/python -m pytest -q tests/test_xsmn_llm_gen.py tests/test_llm_gen_openai_smoke.py tests/test_prediction_repo.py` -- focused contract tests pass.
- `.venv/bin/python -m pytest -q` -- full suite pass.
- `.venv/bin/python -m compileall -q src` -- Python compile pass.
- `python -c 'import yaml; yaml.safe_load(open(".github/workflows/12-test-llm-gen-openai.yml"))'` -- workflow parses.
- `git diff --check` -- no whitespace errors.

## Suggested Review Order

**AgentRouter contract**

- Responses adapter khóa request shape và toàn bộ lifecycle fail-closed.
  [`providers.py:340`](../../src/xsmn_llm_gen/providers.py#L340)

- Mapping backend tách GPT-5.6 mà không đổi OpenAI official.
  [`config.py:18`](../../src/xsmn_llm_gen/config.py#L18)

- Endpoint allowlist ngăn selected credential rời khỏi origin cố định.
  [`providers.py:207`](../../src/xsmn_llm_gen/providers.py#L207)

- Preflight xác minh chính xác GPT-5.6 trước generation smoke.
  [`providers.py:417`](../../src/xsmn_llm_gen/providers.py#L417)

**Audit và vận hành**

- Smoke giữ model availability ba trạng thái và summary không lộ secret.
  [`smoke_test_llm_gen_openai.py:191`](../../src/scripts/smoke_test_llm_gen_openai.py#L191)

- Orchestrator đồng bộ canonical identity và Telegram label GPT-5.6.
  [`predict_ensemble.py:309`](../../src/scripts/predict_ensemble.py#L309)

- Workflow manual gọi đúng AgentRouter Responses, không cấp database hay Telegram.
  [`12-test-llm-gen-openai.yml:37`](../../.github/workflows/12-test-llm-gen-openai.yml#L37)

- Env example công bố contract cố định, không mở custom URL.
  [`.env.example:12`](../../.env.example#L12)

**Regression boundaries**

- Contract test khóa endpoint, model, payload, key header và usage.
  [`test_xsmn_llm_gen.py:423`](../../tests/test_xsmn_llm_gen.py#L423)

- Edge tests khóa status, refusal, truncation và fallback output.
  [`test_xsmn_llm_gen.py:546`](../../tests/test_xsmn_llm_gen.py#L546)

- Smoke test khóa thứ tự preflight rồi generation và availability semantics.
  [`test_llm_gen_openai_smoke.py:135`](../../tests/test_llm_gen_openai_smoke.py#L135)

- Persistence test ngăn reuse canonical giữa contract cũ và mới.
  [`test_prediction_repo.py:752`](../../tests/test_prediction_repo.py#L752)
