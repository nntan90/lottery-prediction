---
title: 'XSMN LLM_Gen Single-Provider Adapter'
type: 'feature'
created: '2026-08-04'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'c7ba37bcf912112f70d8e82554086a5ce1cb5738'
context:
  - 'docs/project-context.md'
  - '_bmad-output/implementation-artifacts/spec-limit-xsmn-model-contribution-top2.md'
  - '_bmad-output/implementation-artifacts/spec-xsmn-relationship-shadow-model.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** XSMN chưa có `LLM_Gen` để tổng hợp tín hiệu thống kê hiện tại và chưa thể chọn an toàn một trong hai model: OpenAI `gpt-5.6-sol` hoặc Anthropic `claude-opus-4-8`.

**Approach:** Thêm shadow predictor dùng packet bằng chứng deterministic và provider adapter chung. Chỉ adapter được cấu hình được gọi; code kiểm định Top-3, lưu audit và báo cáo riêng, không tham gia production.

## Boundaries & Constraints

**Always:** Dùng `LLM_GEN_MODE=off|shadow`; khi bật, `LLM_GEN_PROVIDER=openai|anthropic` ánh xạ cố định tới model tương ứng. Chỉ đọc key của provider đã chọn, kể cả khi có cả hai key. Packet không mutate source và chỉ lấy Top-2 hợp lệ từ mỗi `model@province`; family vote dedupe giữa tỉnh, coverage giữ riêng. Mọi thống kê dùng dữ liệu trước cutoff: rank/weight, frequency/co-occurrence đúng scope, recency và đặc trưng chữ số. Validator chỉ nhận candidate trong pool, duy nhất và chọn ba suffix khác nhau. Score là `ranking_score_uncalibrated`. Lưu `XSMN/all`, `llm_gen`, `llm_gen_v1` cùng provider/model, prompt/schema version, input hash, usage, latency; verify `hit_count >= 2`. Cùng input thì reuse success, không gọi lại API.

**Ask First:** Cho LLM_Gen tham gia production/weight/tài chính; gọi cả hai hoặc cross-provider fallback; đổi Top-2/diversity/KPI; thêm SDK/dependency hay schema; ghi đè canonical success bằng config khác cùng ngày.

**Never:** Hardcode/leak key, header, credential hoặc chain-of-thought; gọi provider còn lại; cho LLM phát minh số; gọi score là xác suất; dùng future data; ghi legacy làm mất audit; để lỗi shadow làm hỏng production/Telegram chính.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Tắt | `LLM_GEN_MODE=off` | Không build/call/persist, không báo missing | Production bình thường |
| OpenAI | provider `openai`, key hợp lệ | Chỉ gọi `gpt-5.6-sol`; persist/render `LLM_Gen [GPT-5.6 Sol]` | Tối đa một retry cùng provider cho timeout/429/5xx |
| Anthropic | provider `anthropic`, key hợp lệ | Chỉ gọi `claude-opus-4-8`; persist/render `LLM_Gen [Claude Opus 4.8]` | Cùng policy retry |
| Config/API lỗi | Sai provider/key, refusal hoặc JSON sai | Không Top-3; không gọi provider kia | Redact và cô lập lỗi |
| Candidate lỗi | Ngoài pool, trùng hoặc thiếu ba suffix | Lọc; không đủ thì abstain | Không pad/invent |
| Rerun/conflict | Có canonical success | Cùng hash reuse; khác config không overwrite | Không gọi API |
| Thiếu audit schema | Migration shadow tracking chưa được apply | Không gọi provider | Báo cấu hình/database chưa sẵn sàng |

</frozen-after-approval>

## Code Map

- `src/xsmn_llm_gen/` -- config, evidence, REST adapters, validation và service.
- `src/scripts/predict_ensemble.py` -- post-production execution, persistence và Telegram row.
- `src/database/prediction_repo.py` -- canonical `llm_gen`, audit/redaction/idempotency.
- `src/scripts/verify_v3.py`, `src/scripts/weekly_report.py`, `src/bot/verification_messages.py` -- verify, weekly coverage và labels.
- `.env.example`, `.github/workflows/02-predict-ensemble.yml` -- mode/provider và optional secrets.
- `tests/` -- unit/cross-layer regressions.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_llm_gen/{config,evidence,providers,service}.py` -- provider-neutral pipeline bằng `requests`, strict JSON và validator deterministic.
- [x] `src/scripts/predict_ensemble.py`, `src/database/prediction_repo.py` -- preflight schema, run/reuse/save fault-isolated, three-suffix guard và redaction.
- [x] `src/scripts/verify_v3.py`, `src/scripts/weekly_report.py`, `src/bot/verification_messages.py` -- optional-shadow lifecycle, `>=2/3`, label và coverage `/7`.
- [x] `.env.example`, `.github/workflows/02-predict-ensemble.yml` -- expose selector/secrets, default off, không thêm dependency.
- [x] `tests/test_xsmn_llm_gen.py` và regressions -- khóa adapters, matrix, audit, isolation, Telegram/weekly.

**Acceptance Criteria:**
- Given cả hai key, when chọn provider, then chỉ endpoint/model đó nhận request, không fallback.
- Given source Top-5/10, when build packet, then chỉ Top-2/source tham gia, family không double-vote và input không đổi.
- Given response lỗi candidate, when validate, then Top-3 thuộc pool, unique, khác suffix hoặc abstain.
- Given API/save lỗi, when pipeline chạy, then production và Telegram chính vẫn hoàn tất.
- Given đủ kết quả hai tỉnh, when verify/report, then chỉ `>=2/3` là thắng và có thống kê ngày/7.

## Spec Change Log

## Design Notes

Provider chỉ xếp hạng bằng chứng đã tính; code giữ contract. Response: `ranked_candidates[{pair, rank, ranking_score_uncalibrated, evidence_codes, risk_flags}]`. Không lưu suy luận nội bộ. Chỉ transient error được retry một lần trên cùng provider.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmn_llm_gen.py tests/test_prediction_repo.py tests/test_ensemble_telegram_messages.py tests/test_verify_v3.py tests/test_weekly_report.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/xsmn_llm_gen src/scripts/predict_ensemble.py`
- Parse các workflow predict/verify/weekly bằng PyYAML; `git diff --check`.

## Suggested Review Order

**Entry point và fault isolation**

- Bắt đầu tại boundary cấu hình, preflight, reuse và persistence của shadow.
  [`predict_ensemble.py:349`](../../src/scripts/predict_ensemble.py#L349)

- Service dựng identity trước API và quyết định reuse hoặc canonical conflict.
  [`service.py:259`](../../src/xsmn_llm_gen/service.py#L259)

- Daily pipeline chỉ gọi LLM sau khi production prediction đã được lưu.
  [`predict_ensemble.py:1521`](../../src/scripts/predict_ensemble.py#L1521)

**Evidence và provider trust boundary**

- Packet khóa Top-2/source, dedupe family/source và thống kê trước cutoff.
  [`evidence.py:98`](../../src/xsmn_llm_gen/evidence.py#L98)

- Runtime schema validation chặn response sai kiểu trước business selection.
  [`providers.py:118`](../../src/xsmn_llm_gen/providers.py#L118)

- Validator chỉ chọn candidate trong pool với ba suffix khác nhau.
  [`service.py:30`](../../src/xsmn_llm_gen/service.py#L30)

**Audit và canonical idempotency**

- Schema preflight ngăn API cost khi metadata audit chưa lưu được.
  [`prediction_repo.py:346`](../../src/database/prediction_repo.py#L346)

- First-success guard bảo vệ cả retry thường và unique-insert race.
  [`prediction_repo.py:452`](../../src/database/prediction_repo.py#L452)

**Telegram, verify và weekly lifecycle**

- Telegram ghi provider và nói rõ điểm xếp hạng chưa calibration.
  [`predict_ensemble.py:304`](../../src/scripts/predict_ensemble.py#L304)

- Verify chỉ yêu cầu LLM_Gen missing-row khi shadow đang bật.
  [`verify_v3.py:524`](../../src/scripts/verify_v3.py#L524)

- Weekly report hiển thị hit-days, run coverage và verified coverage.
  [`weekly_report.py:711`](../../src/scripts/weekly_report.py#L711)

**Operations và regression tests**

- Workflow serialize prediction runs và inject đúng mode/provider/secrets.
  [`02-predict-ensemble.yml:10`](../../.github/workflows/02-predict-ensemble.yml#L10)

- Core tests khóa provider selection, evidence, validation và failure isolation.
  [`test_xsmn_llm_gen.py:185`](../../tests/test_xsmn_llm_gen.py#L185)

- Persistence regression khóa concurrent canonical race không overwrite.
  [`test_prediction_repo.py:724`](../../tests/test_prediction_repo.py#L724)
