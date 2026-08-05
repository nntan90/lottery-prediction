---
title: 'LLM_Gen OpenAI Connection Smoke Test'
type: 'chore'
created: '2026-08-05'
status: 'done'
route: 'one-shot'
---

# LLM_Gen OpenAI Connection Smoke Test

## Intent

**Problem:** Cần xác nhận GitHub Secret gọi được OpenAI Responses API qua đúng adapter `LLM_Gen`, nhưng pipeline dự đoán hiện bị chặn bởi migration Supabase chưa áp dụng.

**Approach:** Thêm workflow chỉ chạy thủ công trên `main`, dùng packet tổng hợp nhỏ để gọi adapter production và chỉ log kết quả đã kiểm định, latency và token usage; không đọc/ghi Supabase hoặc gửi Telegram.

## Suggested Review Order

**Biên thực thi thủ công**

- Main-only dispatch giữ secret ngoài code từ ref không tin cậy.
  [`12-test-llm-gen-openai.yml:15`](../../.github/workflows/12-test-llm-gen-openai.yml#L15)

- Secret chỉ được cấp cho đúng bước gọi Responses API.
  [`12-test-llm-gen-openai.yml:37`](../../.github/workflows/12-test-llm-gen-openai.yml#L37)

**Contract provider và log an toàn**

- Packet deterministic gọi nguyên service và adapter production.
  [`smoke_test_llm_gen_openai.py:146`](../../src/scripts/smoke_test_llm_gen_openai.py#L146)

- Summary khóa Top-3, reason, usage và không phản chiếu credential.
  [`smoke_test_llm_gen_openai.py:109`](../../src/scripts/smoke_test_llm_gen_openai.py#L109)

**Regression guards**

- Failure paths chứng minh lỗi không làm lộ key.
  [`test_llm_gen_openai_smoke.py:81`](../../tests/test_llm_gen_openai_smoke.py#L81)

- YAML assertions khóa trigger, quyền và trusted ref.
  [`test_llm_gen_openai_smoke.py:192`](../../tests/test_llm_gen_openai_smoke.py#L192)
