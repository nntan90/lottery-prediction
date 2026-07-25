---
title: 'Refactor dò kết quả và Telegram theo KPI Multi-Model ≥2/3'
type: 'refactor'
created: '2026-07-25'
status: 'done'
review_loop_iteration: 0
baseline_commit: '0fe5333660bdafc9c259da51de0003c9303d80af'
context:
  - '{project-root}/docs/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `verify_v3.py` đã dùng điều kiện `>=2/3` cho kết quả chính nhưng vẫn lặp lại logic của `evaluate_combo()`, có thể đếm trùng pair thành false hit và trộn mọi row vào tỷ lệ tổng. Telegram chỉ liệt kê số khớp nên một sub-model xanh hoặc một số khớp trong bộ ba dễ bị hiểu nhầm là dự đoán Multi-Model chính xác.

**Approach:** Dùng evaluator combo hiện tại làm nguồn chân lý cho bộ ba và profit, phân biệt rõ scope Multi-Model với Single/sub-model, rồi tách formatter Telegram thuần để hiển thị verdict `TRÚNG/TRƯỢT`, số khớp `/3`, điều kiện `≥2/3` và chú thích diagnostic.

## Boundaries & Constraints

**Always:** Multi-Model chỉ `hit=True` khi có ít nhất hai trong đúng ba pair duy nhất; matched pairs phải deduplicate theo thứ tự dự đoán; profit dùng cùng `hit_count` và `C(hit_count, 2)`; tỷ lệ headline chỉ tính row ensemble; sub-model Top 3/5 vẫn lưu any-hit để giữ contract lịch sử; Telegram dùng HTML, deterministic và không vượt quá dữ liệu thực.

**Ask First:** Đổi schema/cột DB, đổi định nghĩa `model_predictions.hit`, sửa credibility/auto-weight/retrain semantics, hoặc đổi điều kiện thắng của Single Model.

**Never:** Gọi một match là Multi-Model trúng; tính pair ngoài danh sách đang hiển thị; để duplicate làm tăng `hit_count`; đổi signature/output shape của `calculate_station_profit()` hoặc `verify_date()`; sửa prediction selector, XSMB/XSMN model hay workflow path.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Multi khớp 0 hoặc 1 | Top 3 hợp lệ, actual có dưới 2 pair | DB `hit=False`; Telegram đỏ, `(0/3)` hoặc `(1/3 · chưa đạt)` | Vẫn hiển thị pair đã khớp |
| Multi khớp 2 hoặc 3 | Top 3 hợp lệ, actual có ít nhất 2 pair | DB `hit=True`; circles lần lượt 1 hoặc 3; Telegram xanh `TRÚNG` | N/A |
| Pair trùng/không hợp lệ | Top 3 không đủ ba pair duy nhất 00–99 | Không được tạo false hit hoặc profit thắng | Log warning; verdict miss an toàn, pipeline tiếp tục |
| Sub-model có một match | Top 3/5 diagnostic | Giữ DB any-hit và liệt kê match | Legend nói rõ không phải verdict Multi |
| Sub-model rỗng | Không có candidate, ví dụ LSTM `[]` | Hiển thị `không có dữ liệu`, không dùng icon trượt | N/A |
| Có row legacy/single | Cùng ngày với ensemble rows | Không làm thay đổi mẫu số/tử số headline Multi | Single giữ semantics hiện tại |

</frozen-after-approval>

## Code Map

- `src/scripts/verify_v3.py` -- orchestration verify, DB updates, profit compatibility wrapper và caller của formatter.
- `src/xsmb_combo/metrics.py` -- canonical `evaluate_combo()` cho hit count, combo verdict, circles và profit.
- `src/bot/verification_messages.py` -- formatter Telegram thuần mới cho báo cáo kết quả.
- `database/migrations/08_model_predictions_tracking.sql` -- contract any-hit hiện tại của từng sub-model, không thay đổi.
- `.github/workflows/01-daily-crawl.yml` và `.github/workflows/03-verify-predictions.yml` -- production callers cần giữ nguyên.
- `tests/test_verify_profit.py` -- regression contract của profit wrapper.
- `tests/test_verify_v3.py` -- coverage mới cho evaluator integration, summary và Telegram.

## Tasks & Acceptance

**Execution:**
- [x] `src/scripts/verify_v3.py` -- thay logic dò/profit trùng lặp bằng adapter quanh `evaluate_combo()`, phân loại ensemble/single và truyền `hit_count`, `combo_hit`, `model_scope` sang report.
- [x] `src/bot/verification_messages.py` -- tách pure formatter; render Multi verdict rõ `x/3`, chỉ render Single khi có dữ liệu, sắp xếp region/province/model ổn định và giải thích icon sub-model.
- [x] `tests/test_verify_profit.py` -- khóa backward compatibility của signature/output và parity với canonical evaluator cho bộ ba hợp lệ.
- [x] `tests/test_verify_v3.py` -- test 0/1/2/3 matches, duplicate/invalid pair, denominator chỉ gồm ensemble, empty model, filtered Top-N match và snapshot XSMB/XSMN.
- [x] `.github/workflows/*.yml` -- xác nhận entrypoint/arguments không đổi; không thêm env hoặc migration.

### Review Findings

- [x] [Review][Patch] Logging không crash khi Top 3 chứa null/non-integer.
- [x] [Review][Patch] Single Model dùng nhãn diagnostic `x/3 khớp`, không dùng màu xanh như verdict.
- [x] [Review][Patch] Top 3 Single bị trùng/không hợp lệ cũng bị forced miss để đồng nhất với profit.
- [x] [Review][Patch] Footer không trình bày `0/0` như một tỷ lệ đo được.
- [x] [Review][Patch] Escape province/model text trước khi chèn vào Telegram HTML.
- [x] [Review][Patch] Giữ nguyên ba slot lỗi trong report để không che duplicate/corruption.
- [x] [Review][Defer] Ghi nhận partial-skip reporting, HTML chunking và delivery-failure propagation trong `deferred-work.md`.

**Acceptance Criteria:**
- Given bộ Multi `[50,83,89]` chỉ khớp `83`, when verify và format, then DB là miss và Telegram ghi `1/3 · chưa đạt`, không gọi là chính xác.
- Given bộ Multi khớp hai hoặc ba pair, when verify, then DB, profit và Telegram dùng cùng một canonical `hit_count` và cùng verdict.
- Given duplicate pair hoặc row single cùng ngày, when tạo báo cáo, then không có false combo hit và headline vẫn chỉ phản ánh các row ensemble.
- Given sub-model Top-N có một match, when format, then match vẫn hiển thị như diagnostic và legend nói rõ kết quả chính chỉ tính Multi `≥2/3`.

## Design Notes

Golden line cho kết quả chính:
`└ 🔴 Bộ 3: 50 | 83 | 89 → 83 (1/3 · chưa đạt)`

Footer:
`📈 Multi-Model đạt ≥2/3: 0/2 (0%)`

`model_predictions.hit` tiếp tục mang nghĩa có ít nhất một candidate khớp. Chỉ `prediction_results` scope ensemble và headline dùng KPI combo; không migration vì các trường hiện tại đã đủ.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_verify_profit.py tests/test_verify_v3.py tests/test_xsmb_combo.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/scripts/verify_v3.py src/bot/verification_messages.py`
- `git diff --check`

## Suggested Review Order

**Canonical verification**

- Entry point classifies scope and persists one canonical verdict.
  [`verify_v3.py:138`](../../src/scripts/verify_v3.py#L138)

- Top-3 adapter enforces unique pairs and fail-safe invalid data.
  [`verify_v3.py:66`](../../src/scripts/verify_v3.py#L66)

- Profit wrapper delegates valid triples to the same evaluator.
  [`verify_v3.py:97`](../../src/scripts/verify_v3.py#L97)

**Telegram semantics**

- Formatter builds deterministic region sections and ensemble-only headline.
  [`verification_messages.py:266`](../../src/bot/verification_messages.py#L266)

- Main verdict distinguishes one match from the required two.
  [`verification_messages.py:164`](../../src/bot/verification_messages.py#L164)

- Single and sub-model rows remain explicitly diagnostic.
  [`verification_messages.py:117`](../../src/bot/verification_messages.py#L117)
  [`verification_messages.py:201`](../../src/bot/verification_messages.py#L201)

- Invalid stored slots remain visible while dynamic labels are HTML-safe.
  [`verification_messages.py:68`](../../src/bot/verification_messages.py#L68)

**Regression evidence**

- KPI, invalid-input and Telegram behavior are covered together.
  [`test_verify_v3.py:29`](../../tests/test_verify_v3.py#L29)

- Fake-DB integration proves Multi miss and sub-model any-hit coexist.
  [`test_verify_v3.py:339`](../../tests/test_verify_v3.py#L339)

- Profit compatibility is checked against the canonical evaluator.
  [`test_verify_profit.py:174`](../../tests/test_verify_profit.py#L174)

- Pre-existing delivery hardening is recorded for focused follow-up.
  [`deferred-work.md:35`](./deferred-work.md#L35)
