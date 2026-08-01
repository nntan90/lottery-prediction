---
title: 'Giới hạn XSMN Top-2/source và đa dạng hàng đơn vị'
type: 'refactor'
created: '2026-08-01'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'e1fbcbce10053746db64fc0925a4a8da8c7103d5'
context:
  - 'docs/project-context.md'
  - '_bmad-output/implementation-artifacts/spec-xsmn-ensemble-production-hardening.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** XSMN production hiện cho phép mỗi `model@province` đóng góp tới Top-10 vào CombSUM/consensus. Các ứng viên hạng thấp có thể tích lũy phiếu giữa các model tương quan; sau khi cắt Top-2, selector vẫn có thể chọn cả ba số cùng hàng đơn vị như `32–42–02`, tạo rủi ro tập trung nếu suffix đó không về.

**Approach:** Giữ nguyên Top-10 output nội bộ và Top-5 persistence/audit, nhưng chỉ cho hai cặp đứng đầu của mỗi `model@province` tham gia aggregation. Combo selector chỉ xét bộ ba có ba hàng đơn vị khác nhau; bộ cuối vẫn verify theo `hit_count >= 2`.

## Boundaries & Constraints

**Always:** Áp dụng giới hạn theo từng source `model@province`; dùng cùng tập Top-2 cho base score, source count, model-family consensus, province attribution và scoring log; Top-3 cuối phải có ba suffix khác nhau; giữ nguyên public signatures, fault tolerance, Telegram Top-3 và database contracts.

**Ask First:** Nới diversity để cho hai/ba số trùng suffix khi pool thiếu đa dạng, thay output cuối khỏi ba, giới hạn XSMB, thay Top-10 inference/Top-5 persistence, hoặc cho shadow CMR/DDT tham gia production.

**Never:** Cắt/sửa `top_pairs` gốc của model; tính consensus từ hạng 3 trở xuống; pad bằng ứng viên hạng thấp để lách Top-2; output ba số trùng suffix; gọi ranking score là probability; áp dụng cho XSMB hay shadow.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| XSMN source đủ Top-10 | Một `model@province` trả 10 cặp | Chỉ hạng 1–2 được chấm và ghi attribution; output gốc vẫn còn 10 cặp | N/A |
| XSMN source có 0–1 cặp | Model lỗi hoặc output ngắn | Dùng tất cả cặp hợp lệ sẵn có; các model khác tiếp tục | Giữ fault tolerance hiện tại |
| Combo tập trung suffix | Ba ứng viên điểm cao nhất đều tận cùng một chữ số | Loại combo đó; chọn bộ ba tốt nhất có ba suffix khác nhau | Ghi diversity constraint trong audit output |
| Pool thiếu diversity | Sau Top-2 không có ba suffix distinct | Không pad từ hạng 3+ và không phá constraint | Trả `insufficient_digit_diversity`, không phát hành Top-3 sai contract |
| XSMB | Model XSMB trả Top-5/10 | Scoring và output không đổi | Regression test bắt mọi thay đổi |

</frozen-after-approval>

## Code Map

- `src/xsmn_ensemble/ensemble_engine.py` -- tạo contribution view Top-2 và ràng buộc suffix diversity trong merged combo selector.
- `config/scoring.yaml` -- khai báo Top-2/source và ba suffix distinct minh bạch, deterministic, không ảnh hưởng XSMB.
- `tests/test_ensemble_engine.py` -- regression cho Top-2, suffix diversity, insufficient diversity và XSMB compatibility.
- `tests/test_scoring_config.py` -- xác nhận config contribution limit của XSMN.
- `.github/workflows/02-predict-ensemble.yml` -- kiểm tra path/command không cần thay đổi.

## Tasks & Acceptance

**Execution:**
- [x] `config/scoring.yaml` -- thêm `max_pairs_per_source: 2` và `require_distinct_unit_digits: true` trong XSMN combo config.
- [x] `src/xsmn_ensemble/ensemble_engine.py` -- giới hạn contribution view tại aggregation, dùng nhất quán trong scoring log và chỉ score combo ba suffix distinct; không mutate model result.
- [x] `tests/test_ensemble_engine.py` -- thêm focused tests cho Top-2, consensus/attribution, ba suffix distinct, insufficient diversity, short output và XSMB compatibility.
- [x] `tests/test_scoring_config.py` -- test config XSMN expose cả hai guardrail.
- [x] `.github/workflows/02-predict-ensemble.yml` -- impact-check command/path vẫn hợp lệ.

**Acceptance Criteria:**
- Given một XSMN model source trả mười cặp, when ensemble chấm điểm, then chỉ hai cặp đầu có thể xuất hiện trong source attribution của source đó.
- Given một cặp chỉ xuất hiện từ hạng 3 trở xuống, when consensus được tính, then cặp đó không nhận base score, source vote hoặc model-family vote.
- Given pool có ít nhất ba suffix, when merged selector chọn Top-3, then ba cặp cuối có ba hàng đơn vị khác nhau dù combo trùng suffix có raw score cao hơn.
- Given pool không có ba suffix distinct, when selector chạy, then output bị từ chối rõ ràng thay vì pad hạng 3+ hoặc phá diversity.
- Given cùng `model_results`, when XSMB aggregation chạy, then behavior và candidate contributions vẫn theo contract hiện tại.
- Given model output chứa Top-10, when XSMN aggregation hoàn tất, then object gốc và persistence formatter vẫn có thể audit Top-5/10 như trước.

## Spec Change Log

- 2026-08-01 / review loop 1: Top-2/source alone produced `32–42–02`, exposing suffix concentration risk. Human intent was expanded to require three distinct unit digits and explicit abstention when the eligible pool cannot satisfy diversity. KEEP: non-mutating Top-10 audit, Top-2 source contribution, XSMB/shadow isolation and the existing >=2/3 verifier contract.

## Design Notes

Giới hạn được áp dụng ở consumer aggregation thay vì từng predictor, giữ full ranking cho chẩn đoán/backfill. `model@province` là grain của source. Diversity là hard portfolio constraint trong combo selector, không phải bonus trừ điểm: bộ `32–42–02` bị loại ngay cả khi có score cao. Nếu pool Top-2 không đủ ba suffix, selector abstain thay vì nới rule ngầm.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_ensemble_engine.py tests/test_scoring_config.py` -- focused scoring/config tests pass.
- `python3 -m pytest -q` -- full suite pass.
- `python3 -m compileall -q src tests` -- source/tests compile.
- Parse `config/scoring.yaml` và `.github/workflows/02-predict-ensemble.yml` bằng PyYAML -- YAML hợp lệ.
- `git diff --check` -- không có whitespace error.
