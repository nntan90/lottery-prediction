---
title: 'XSMN Relationship Consensus Shadow Model'
type: 'feature'
created: '2026-08-02'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'e49fa0ebab5bceaa2e29570e8b7aa0701901e4ad'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/research/technical-xsmn-cooccurrence-consensus-model-research-2026-08-02.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Ensemble XSMN chưa khai thác trực tiếp quan hệ đồng xuất hiện giữa các candidate Top-5 của nhiều model trên đúng cặp tỉnh. Cần đo riêng tín hiệu này bằng KPI trúng ít nhất `2/3`.

**Approach:** Thêm shadow predictor `relationship`: chọn anchor theo đồng thuận model-family, loại anchor đã về trong cả hai kỳ merge gần nhất, rồi xếp bộ ba bằng co-occurrence đã shrink, đủ ba cạnh và bằng chứng lịch sử trực tiếp `>=2/3`.

## Boundaries & Constraints

**Always:** Dùng Top-5 của mỗi result `model@province` thành công mà không sửa input; một family chọn cùng số ở hai tỉnh chỉ có một phiếu, province coverage lưu riêng; mẫu số là số family hoạt động. History là các kỳ matched của đúng tập tỉnh, chỉ lấy `draw_date < target_date`; anchor chỉ bị loại khi xuất hiện ở cả hai kỳ matched gần nhất. Top-3 phải khác số và khác hàng đơn vị, deterministic. Lưu `XSMN/all`, `model_name=relationship`, `relationship_v1`, mode `shadow`, score `ranking_score_uncalibrated`; verifier dùng union đúng scope tỉnh và `hit_count>=2`.

**Ask First:** Đổi schema, KPI, guardrail; đưa Relationship vào production verdict, weights, tài chính hoặc retrain.

**Never:** Hardcode năm family; leakage; gọi score là xác suất calibrated; pad Top-3; để lỗi shadow/persistence/report làm hỏng production hoặc Telegram chính.

## I/O & Edge-Case Matrix

| Scenario | State | Expected | Error handling |
|---|---|---|---|
| Đủ bằng chứng | ≥4 family, ≥52 matched kỳ | `success`, Top-3 và audit vote/anchor/pair/triangle/combo | N/A |
| Anchor vừa về liên tiếp | Candidate đầu có trong cả hai kỳ mới nhất | Reject có lý do, xét candidate kế | `no_eligible_anchor` nếu hết |
| Thiếu input/history | Thiếu family, hai kỳ gần nhất hoặc history | Không sinh bộ giả | Status thiếu dữ liệu cụ thể |
| Thiếu đa dạng | Không có bộ khác ba unit digit | Giữ guardrail | `insufficient_candidate_diversity` |

</frozen-after-approval>

## Code Map

- `src/xsmn_relationship/` -- contracts, pure predictor, history adapter, service và backtest.
- `src/scripts/predict_ensemble.py` -- chạy/persist shadow sau production và render Telegram.
- `src/database/prediction_repo.py` -- canonical relationship persistence bằng schema hiện tại.
- `src/scripts/verify_v3.py`, `src/scripts/weekly_report.py`, `src/bot/verification_messages.py` -- verify và báo cáo lifecycle.
- `tests/` -- unit và cross-layer regressions.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_relationship/{domain,predictor,repository,service,backtest}.py` -- family votes, anchor filter, empirical-Bayes pair evidence, triangle/direct-combo selector và leakage-safe ablations.
- [x] `src/scripts/predict_ensemble.py` -- truyền snapshot Top-5, chạy fault-tolerant, persist và thêm `Relationship shadow` mà không đổi production Top-3.
- [x] `src/database/prediction_repo.py` -- normalize Top-3/score và giữ audit metadata.
- [x] `src/scripts/verify_v3.py`, `src/scripts/weekly_report.py`, `src/bot/verification_messages.py` -- registry, merged-scope verify và `Relationship: x/7`.
- [x] `tests/test_xsmn_relationship.py` cùng regression liên quan -- khóa matrix, determinism và isolation.

**Acceptance Criteria:**
- Given một family chọn `11` ở hai tỉnh, when đếm, then vote bằng một và coverage bằng hai.
- Given candidate đầu có trong cả hai kỳ gần nhất, when chọn anchor, then nó bị loại; candidate chỉ có trong một kỳ vẫn hợp lệ.
- Given cùng input/config, when chạy lại, then canonical output và audit ordering giống nhau.
- Given shadow lỗi/thiếu evidence, when pipeline chạy, then production, CMR/DDT và Telegram chính vẫn hoàn tất.
- Given kết quả đúng scope, when verify/report, then Relationship chỉ thắng ở `>=2/3` và hiển thị ngày trúng `/7` cùng coverage.

## Spec Change Log

## Design Notes

Defaults: Top-5/source; min 4 family; anchor vote ratio `0.50`; check 2 kỳ và reject khi hit cả 2; history 104 kỳ, min 52; prior strength 20; pair support min 3; distinct unit digits. Backtest: R-A vote/rank, R-B +pair, R-C +direct `2/3`, guard off/on; báo `>=2/3 / evaluated days`, coverage và abstention.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmn_relationship.py tests/test_prediction_repo.py tests/test_ensemble_telegram_messages.py tests/test_verify_v3.py tests/test_weekly_report.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/xsmn_relationship src/scripts/predict_ensemble.py src/scripts/verify_v3.py src/scripts/weekly_report.py`
- `git diff --check`

## Suggested Review Order

**Logic chọn bộ Relationship**

- Luồng chính ghép consensus, anchor, tam giác và bằng chứng trực tiếp thành Top-3.
  [`predictor.py:408`](../../src/xsmn_relationship/predictor.py#L408)

- Phiếu model-family được dedupe giữa hai tỉnh, coverage được giữ riêng.
  [`predictor.py:64`](../../src/xsmn_relationship/predictor.py#L64)

- Anchor bị loại duy nhất khi xuất hiện trong cả hai kỳ matched gần nhất.
  [`predictor.py:195`](../../src/xsmn_relationship/predictor.py#L195)

- Co-occurrence dùng excess-over-prior shrinkage để tránh phóng đại mẫu nhỏ.
  [`predictor.py:241`](../../src/xsmn_relationship/predictor.py#L241)

**Dữ liệu và lifecycle shadow**

- Service khóa guard khác hàng đơn vị trước khi truy vấn và chấm điểm.
  [`service.py:13`](../../src/xsmn_relationship/service.py#L13)

- History matched được phân trang và chỉ tải tails của date candidates cần thiết.
  [`repository.py:65`](../../src/xsmn_relationship/repository.py#L65)

- Persistence chuẩn hóa status, Top-3, unit digits và audit metadata.
  [`prediction_repo.py:226`](../../src/database/prediction_repo.py#L226)

- Pipeline chạy sau production và Telegram chỉ đọc kết quả canonical đã lưu.
  [`predict_ensemble.py:1175`](../../src/scripts/predict_ensemble.py#L1175)

**Đánh giá và báo cáo**

- Walk-forward so sánh ablation theo ngày paired với baseline production.
  [`backtest.py:128`](../../src/xsmn_relationship/backtest.py#L128)

- Verifier chờ đủ 18 prize rows ở cả hai tỉnh trước verdict.
  [`verify_v3.py:152`](../../src/scripts/verify_v3.py#L152)

- Weekly report thêm Relationship thắng ≥2/3 theo ngày trên cửa sổ bảy ngày.
  [`weekly_report.py:314`](../../src/scripts/weekly_report.py#L314)

**Regression tests**

- Unit tests khóa triangle, direct evidence, determinism và diversity guard.
  [`test_xsmn_relationship.py:210`](../../tests/test_xsmn_relationship.py#L210)

- Integration test khóa canonical persistence và cô lập production/Telegram.
  [`test_xsmn_digit_transition_integration.py:217`](../../tests/test_xsmn_digit_transition_integration.py#L217)

- Verification test khóa KPI Relationship đúng điều kiện ít nhất 2/3.
  [`test_verify_v3.py:486`](../../tests/test_verify_v3.py#L486)
