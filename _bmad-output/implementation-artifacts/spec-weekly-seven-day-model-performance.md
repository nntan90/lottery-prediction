---
title: 'Báo cáo tuần theo số ngày trúng của từng model'
type: 'refactor'
created: '2026-07-30'
status: 'done'
review_loop_iteration: 0
baseline_commit: '327ea3588eb5a4c3e3b2515e7a23da9ce4a97ac6'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-refactor-verification-and-result-message.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-local-ddt-telegram-shadow-lifecycle.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Weekly report hiện đếm các row dự đoán theo vùng/tỉnh nên mẫu số `29` trộn ensemble, provincial và legacy scopes; người đọc không biết trong đúng bảy ngày XSMB, XSMN đồng thuận, DDT và CMR đã đạt KPI chính bao nhiêu ngày.

**Approach:** Bổ sung một ledger hiệu quả theo ngày cho bốn scope canonical, dùng điều kiện trúng `>=2/3`, mẫu số hiển thị cố định theo bảy ngày của kỳ báo cáo và coverage riêng để không biến ngày thiếu dự đoán/chưa verify thành một kết quả đã đo. Telegram ưu tiên ledger mới; XML lưu cả ledger mới lẫn summary cũ để giữ tương thích.

## Boundaries & Constraints

**Always:** XSMB và XSMN chỉ lấy row ensemble canonical trong `prediction_results`; XSMN chỉ lấy `province='all'`; DDT/CMR chỉ lấy `ddt_shadow`/`cmr_shadow` ở `XSMN/all` và trạng thái có bộ ba hợp lệ; một model tối đa một verdict mỗi ngày; thắng khi `combo_hit=true` hoặc `hit_count>=2`, legacy fallback phải đếm unique `matched_pairs` trong Top 3; `hit` legacy chỉ được dùng cho ensemble production, không được dùng như combo verdict của shadow.

**Ask First:** Đổi KPI `>=2/3`, đưa shadow vào tài chính/retrain/ensemble weights, thay schema/database constraints, hoặc xóa các field/XML node của report cũ.

**Never:** Cộng row tỉnh XSMN vào XSMN đồng thuận; gọi any-hit của DDT/CMR là ngày trúng; coi thiếu prediction/unverified là miss; thay signature positional đang được test/caller sử dụng; làm query `model_predictions` lỗi khiến toàn weekly job thất bại.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Đủ bảy ngày | Mỗi scope có 7 row verified | Hiển thị `x/7 ngày trúng`, coverage `verify 7/7` | N/A |
| Shadow legacy | `hit=true`, `hit_count/combo_hit=null`, một matched pair | DDT/CMR vẫn là miss theo `1/3`; ngày được tính verified | Không dùng legacy `hit` làm combo verdict |
| Thiếu/chưa verify | Scope chỉ có 3 prediction, 2 verified | Vẫn hiển thị `x/7`, kèm `chạy 3/7 · verify 2/7` | Không tăng miss count |
| Query shadow lỗi | Bảng/cột chưa khả dụng | Report cũ vẫn tạo; DDT/CMR coverage bằng 0 và có warning CI | Không fail Telegram/XML |
| Row trùng/không hợp lệ | Trùng scope-date hoặc Top 3 thiếu/duplicate | Chỉ một ngày, không false hit | Chọn row deterministic; invalid không tính prediction |

</frozen-after-approval>

## Code Map

- `src/scripts/weekly_report.py` -- collectors, daily KPI aggregation, XML và Telegram weekly output.
- `src/scripts/verify_v3.py` -- nguồn semantics canonical: ensemble/shadow đạt khi ít nhất hai trong ba pair.
- `src/database/prediction_repo.py` -- tên model/status shadow ổn định và legacy persistence contract.
- `.github/workflows/10-weekly-report.yml` -- production entrypoint phải giữ nguyên path/arguments/env.
- `tests/test_weekly_report.py` -- regression cho legacy, missing coverage, XML và Telegram.

## Tasks & Acceptance

**Execution:**
- [x] `src/scripts/weekly_report.py` -- thêm collector fault-tolerant cho CMR/DDT và helper phân loại bốn scope, deduplicate theo date, tính `hit_days/prediction_days/verified_days/period_days`.
- [x] `src/scripts/weekly_report.py` -- mở rộng `_analyze` bằng optional shadow input để giữ caller cũ; thêm XML `SevenDayPerformance` và thay section Telegram bằng bốn dòng hiệu quả theo ngày.
- [x] `tests/test_weekly_report.py` -- khóa XSMB/XSMN canonical filters, shadow legacy any-hit không thành combo hit, fixed `/7`, coverage thiếu và giới hạn Telegram.
- [x] `.github/workflows/10-weekly-report.yml` -- impact check, xác nhận không đổi workflow contract hay secrets.

**Acceptance Criteria:**
- Given dữ liệu 20–26/07/2026 có XSMB ensemble 0 ngày và XSMN/all đạt hai ngày, when tạo weekly report, then Telegram ghi XSMB `0/7` và XSMN đồng thuận `2/7`, không dùng các row tỉnh để tăng tử/mẫu.
- Given DDT/CMR có một matched pair nhưng `hit=true` legacy, when aggregate, then model có coverage verified nhưng `hit_days=0`.
- Given model chỉ chạy một phần tuần, when render, then headline vẫn là `x/7 ngày trúng` và coverage cho biết chính xác số ngày chạy/verify.
- Given caller cũ gọi `_analyze` không truyền shadow rows, when build XML/Telegram, then report vẫn hoàn tất và các section tài chính/crawler/retrain/models không đổi contract.

## Spec Change Log

## Design Notes

Golden section:
`🎯 HIỆU QUẢ 7 NGÀY — đạt khi ≥2/3`
`🔴 XSMB: 0/7 ngày trúng · verify 7/7`
`🟡 XSMN đồng thuận: 2/7 ngày trúng · verify 7/7`
`⚪ DDT: 0/7 ngày trúng · chạy 1/7 · verify 1/7`
`⚪ CMR: 0/7 ngày trúng · chạy 2/7 · verify 2/7`

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_weekly_report.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/scripts/weekly_report.py`
- `git diff --check`

**Manual checks:**
- Chạy report fixture tuần 20–26/07 và xác nhận Telegram dưới 4096 ký tự, XML parse được, bốn scope có mẫu số bảy.

## Review Resolution

- Icon dùng `verified_days`: scope chưa verify và shadow 0-hit giữ trạng thái trung tính, không tạo false miss.
- XSMB canonical chỉ nhận `province=NULL`; metadata `ensemble_method` tùy ý không thể biến single row thành ensemble.
- Regression bổ sung verdict `combo_hit`/`hit_count`, collector filters, XML values và rendered coverage.

## Suggested Review Order

**Canonical daily ledger**

- Aggregator deduplicate scope-date và tách prediction, verification, hit coverage.
  [`weekly_report.py:308`](../../src/scripts/weekly_report.py#L308)

- Scope classifier khóa đúng XSMB, XSMN/all và hai shadow model.
  [`weekly_report.py:264`](../../src/scripts/weekly_report.py#L264)

- Shadow collector cô lập lỗi schema/query khỏi toàn weekly job.
  [`weekly_report.py:86`](../../src/scripts/weekly_report.py#L86)

**Report surfaces**

- Telegram ưu tiên bốn KPI theo ngày cùng coverage rõ ràng.
  [`weekly_report.py:680`](../../src/scripts/weekly_report.py#L680)

- XML thêm ledger mới nhưng bảo toàn toàn bộ node cũ.
  [`weekly_report.py:513`](../../src/scripts/weekly_report.py#L513)

- Entrypoint truyền shadow rows bằng optional extension, không đổi workflow.
  [`weekly_report.py:775`](../../src/scripts/weekly_report.py#L775)

**Regression evidence**

- Fixture 20–26/07 khóa XSMB 0/7 và XSMN đồng thuận 2/7.
  [`test_weekly_report.py:216`](../../tests/test_weekly_report.py#L216)

- Shadow tests phân biệt any-hit legacy với combo verdict canonical.
  [`test_weekly_report.py:345`](../../tests/test_weekly_report.py#L345)

- Collector và XML assertions khóa filters cùng output contract.
  [`test_weekly_report.py:519`](../../tests/test_weekly_report.py#L519)
  [`test_weekly_report.py:593`](../../tests/test_weekly_report.py#L593)
