---
title: 'Refactor XSMB theo mục tiêu tổ hợp >=2/3'
type: 'refactor'
created: '2026-07-25'
status: 'done'
review_loop_iteration: 0
baseline_commit: '98191e53b11b68703c71afd495beaf6b46e8f9d2'
context:
  - '{project-root}/docs/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** XSMB hiện fusion Top 5 bằng MRR/Hit@5, consensus và heuristic nóng/lạnh, trong khi điều kiện thắng thật là có ít nhất hai trong ba số xuất hiện. Score từ các model cũng là relative score khác thang đo, nên không được gọi hoặc kết hợp như probability.

**Approach:** Thêm một XSMB combo core độc lập để chuẩn hóa KPI, chuyển output legacy qua adapter, ước lượng xác suất đồng xuất hiện từ các kỳ trước target date và duyệt toàn bộ bộ ba trong candidate pool. Giữ nguyên mọi public function cũ; integration chỉ chạy shadow khi `XSMB_COMBO_SELECTOR_MODE=shadow`, mặc định `off` nên output production không đổi.

## Boundaries & Constraints

**Always:** Lookback theo kỳ quay và chỉ dùng draw trước target date; deduplicate tail theo từng draw; `combo_hit = hit_count >= 2`; `winning_circles = C(hit_count, 2)`; relative score và empirical joint probability phải được đặt tên/phân loại rõ; deterministic với cùng input; type hints và docstring cho logic xác suất.

**Ask First:** Bật selector mới làm production output, thay schema/migration, ghi shadow result vào Supabase, thay workflow/env production, hoặc thêm package ngoài `requirements.txt`.

**Never:** Đổi signature/return contract của `compute_xsmb_ensemble`, các `predict_*`, `calculate_station_profit`, `summarize_predictions`; sửa logic XSMN; gọi min-max/relative score là calibrated probability; dùng actual tail của chính target date khi chọn số; tự động tune weight trên cùng cửa sổ đánh giá.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Combo hợp lệ | >=3 candidates, đủ history trước target | Trả đúng ba số duy nhất, joint combo score, expected winning circles và audit breakdown | N/A |
| Model legacy lỗi | Một số result lỗi, pair/score null hoặc trùng | Bỏ entry lỗi; các model hợp lệ vẫn tham gia | Báo model bị bỏ trong diagnostics |
| Thiếu candidates | Sau sanitize còn dưới ba số | Không tạo dự đoán giả | Trả status `insufficient_candidates` |
| Thiếu history | Không đủ số kỳ tối thiểu | Không gán joint probability không đáng tin | Trả status `insufficient_history` |
| Shadow tắt | Env thiếu hoặc `off` | Không load thêm data, output cũ giữ nguyên | N/A |
| Shadow lỗi | Selector exception | Legacy ensemble vẫn save/send bình thường | Log warning, không raise khỏi pipeline |

</frozen-after-approval>

## Code Map

- `src/xsmb_combo/domain.py` -- immutable contracts cho score vector, combo evaluation và selector result.
- `src/xsmb_combo/metrics.py` -- nguồn logic duy nhất cho hit count, combo KPI, vòng thắng và random baseline.
- `src/xsmb_combo/adapters.py` -- chuyển legacy `model_results` thành vector 00-99 mà không sửa model cũ.
- `src/xsmb_combo/joint_probability.py` -- empirical-Bayes estimates cho P(a&b), P(a&b&c) chỉ từ history.
- `src/xsmb_combo/selector.py` -- fixed-weight fusion, candidate pool và exhaustive triple optimization.
- `src/xsmb_combo/shadow.py` -- load history theo draw và chạy selector ở chế độ shadow.
- `src/scripts/predict_ensemble.py` -- hook shadow default-off, fault tolerant, không thay legacy output.
- `tests/test_xsmb_combo.py` -- unit/regression tests cho matrix và compatibility.

## Tasks & Acceptance

**Execution:**
- [x] Tạo package `src/xsmb_combo/` với typed immutable contracts và exports ổn định.
- [x] Cài canonical combo metrics, hypergeometric random baseline và profit primitives không phụ thuộc DB.
- [x] Sanitize output legacy thành full score vectors; normalize theo rank/relative evidence nhưng không gắn nhãn probability.
- [x] Ước lượng joint probabilities bằng draw-level presence với Beta smoothing và minimum-history gate.
- [x] Duyệt `C(K,3)` triples; hỗ trợ objective `combo_probability` và `expected_circles`, deterministic tie-break.
- [x] Thêm shadow service và hook `off|shadow`; warning-only khi shadow lỗi.
- [x] Thêm tests cho leakage cutoff, duplicates, thiếu dữ liệu, công thức ≥2/3, deterministic selection và legacy-off compatibility.
- [x] Kiểm tra `.github/workflows/02-predict-ensemble.yml`; không đổi path, args hoặc env bắt buộc.

### Review Findings

- [x] [Review][Patch] Adapter phải bỏ qua top-level model result không phải `Mapping` thay vì làm hỏng toàn bộ shadow run [src/xsmb_combo/adapters.py:24]
- [x] [Review][Patch] Bổ sung regression test pipeline-level để khóa legacy Top 3, DB payload và Telegram path khi combo selector mặc định `off` [tests/test_xsmb_combo.py:145]

**Acceptance Criteria:**
- Given flag mặc định, when chạy XSMB pipeline, then legacy Top 3, model version, DB write và Telegram path không đổi.
- Given history kết thúc trước target date, when selector chấm triples, then không query hoặc sử dụng target actual.
- Given một triple có joint evidence cao hơn, when objective là combo probability, then triple đó được chọn dù không chứa ba marginal scores cao nhất.
- Given model result malformed, when adapter xử lý, then không crash và diagnostics nêu đúng model bị bỏ.
- Given cùng inputs và weights, when chạy nhiều lần, then Top 3 và audit scores giống nhau.

## Design Notes

Với candidate pool mặc định `K=10`, selector chỉ duyệt 120 triples. Objective:
`P(>=2) = P(ab) + P(ac) + P(bc) - 2*P(abc)`;
`E[circles] = P(ab) + P(ac) + P(bc)`.
Joint estimates dùng presence theo draw và Beta prior; fusion score chỉ dùng để tạo candidate pool, không được trình bày như xác suất.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmb_combo.py tests/test_ensemble_engine.py tests/test_prediction_repo.py tests/test_backtest_metrics.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/xsmb_combo src/scripts/predict_ensemble.py`
- `git diff --check`

## Suggested Review Order

1. Production integration and default-off compatibility: [`predict_ensemble.py`](../../src/scripts/predict_ensemble.py#L803), [`shadow.py`](../../src/xsmb_combo/shadow.py#L19)
2. Combo selection and diversification constraints: [`selector.py`](../../src/xsmb_combo/selector.py#L57)
3. Joint probability estimation: [`joint_probability.py`](../../src/xsmb_combo/joint_probability.py#L15)
4. Backtest metrics and random baseline: [`metrics.py`](../../src/xsmb_combo/metrics.py#L26)
5. Legacy model adaptation and malformed-result handling: [`adapters.py`](../../src/xsmb_combo/adapters.py#L12)
6. Regression and behavior tests: [`test_xsmb_combo.py`](../../tests/test_xsmb_combo.py#L177)
