---
title: 'XSMN Coupled Motif Retrieval Shadow Predictor'
type: 'feature'
created: '2026-07-20'
status: 'done'
review_loop_iteration: 0
baseline_commit: '3ea4f57e8639a1223c179ad2bccbb7f2732a9669'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/brainstorming/brainstorm-xsmn-two-province-novel-predictor-2026-07-19/brainstorm-intent.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Ensemble XSMN production hiện dự đoán từng tỉnh rồi merge, nên chưa mô hình hóa trạng thái quan hệ giữa đúng hai tỉnh cùng lịch. Cần một predictor mới có cơ chế độc lập để kiểm nghiệm liệu relational motif có nâng tỷ lệ `hit >= 2/3` hay không.

**Approach:** Xây CMR V1 dưới dạng module và CLI shadow riêng. Predictor lấy kỳ gần nhất trước target của từng tỉnh, dựng fingerprint quan hệ theo từng mã giải, truy hồi historical coupled snapshots, chấm candidate bằng similarity-weighted next-draw evidence với Bayesian shrinkage, rồi chọn đúng ba số với tối đa một direct-overlap candidate.

## Boundaries & Constraints

**Always:** Hai tỉnh được resolve theo lịch XSMN hiện có và phải có đúng hai phần tử; anchor là latest same-province draw trước target, kể cả TP.HCM thứ Hai dùng thứ Bảy gần nhất; từng prize block `DB,1..8` dùng Cartesian all-to-all và có tổng ảnh hưởng bằng nhau; historical label giữ identity `A -> A next`, `B -> B next` rồi chỉ union ở objective; mọi neighbor phải có target date nhỏ hơn ngày đang dự đoán; output phải giải thích overlap, score chưa calibration, support A/B, effective neighbors và nearest cases; kết quả deterministic với cùng input/config.

**Ask First:** Bất kỳ thay đổi nào vào schema, workflow GitHub Actions, `prediction_results`, `model_predictions`, Telegram, ensemble weights, production imports hoặc auto-promotion.

**Never:** Sửa hành vi hay public contract của ensemble hiện tại; dùng frequency, gap, Markov, XGBoost, LSTM, CDM hoặc output ensemble làm feature; đọc dữ liệu tại/sau target để tạo context hay neighbor; gọi score là calibrated probability; pad số khi evidence không đủ.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Đủ lịch sử | Hai tỉnh có anchor hoàn chỉnh và đủ historical cases | Trả `success`, đúng Top 3, audit evidence và không quá một overlap | N/A |
| Anchor bất đồng bộ | TP.HCM target thứ Hai, kỳ gần nhất là thứ Bảy | Dùng kỳ thứ Bảy làm anchor TP.HCM, tỉnh còn lại dùng kỳ gần nhất của chính nó | N/A |
| Thiếu dữ liệu giải | Một anchor không đủ 18 prize rows hoặc thiếu mã giải | Không dùng snapshot đó | Trả `insufficient_evidence` nếu không còn đủ context |
| Ít historical cases | Số neighbor hợp lệ dưới ngưỡng cấu hình | Không sinh dự đoán giả | Trả `insufficient_evidence` kèm reason |
| Override sai scope | Lịch resolve ra khác hai tỉnh | Không chạy CMR | Trả lỗi validation rõ ràng |

</frozen-after-approval>

## Code Map

- `src/xsmn_coupled/domain.py` -- immutable draw/context/result contracts và prize completeness rules.
- `src/xsmn_coupled/repository.py` -- query phân trang read-only từ `tails_2d`, không dùng repository production.
- `src/xsmn_coupled/fingerprint.py` -- all-to-all relational features và equal-prize cosine similarity.
- `src/xsmn_coupled/predictor.py` -- historical sample construction, retrieval, shrinkage scoring và constrained Top 3 selector.
- `src/scripts/predict_xsmn_coupled.py` -- shadow CLI, xuất audit JSON ra filesystem hoặc stdout.
- `tests/test_xsmn_coupled.py` -- synthetic unit/integration tests không cần Supabase.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_coupled/domain.py` -- định nghĩa typed contracts, normalize rows và reject incomplete draws.
- [x] `src/xsmn_coupled/fingerprint.py` -- triển khai fingerprint permutation-invariant và similarity theo chín prize blocks.
- [x] `src/xsmn_coupled/predictor.py` -- dựng anchors/samples không leakage, score candidates và selector constraint.
- [x] `src/xsmn_coupled/repository.py` -- tải đầy đủ history của đúng hai tỉnh bằng pagination và cutoff nghiêm ngặt.
- [x] `src/scripts/predict_xsmn_coupled.py` -- cung cấp CLI shadow deterministic, không side effect production.
- [x] `tests/test_xsmn_coupled.py` -- khóa happy path, async anchor, leakage cutoff, completeness, equal-prize effect, shrinkage và overlap constraint.

**Acceptance Criteria:**
- Given cùng dataset, target date và config, when chạy CMR nhiều lần, then output Top 3, scores và neighbor ordering giống nhau.
- Given target date thứ Hai của TP.HCM, when dựng context, then anchor TP.HCM là kỳ thứ Bảy gần nhất nếu đó là latest same-province draw.
- Given historical rows có date bằng hoặc sau target, when retrieve neighbors, then không row nào tham gia context, label hoặc score.
- Given nhiều direct-overlap có score cao, when select Top 3, then chỉ tối đa một số overlap được chọn.
- Given chạy toàn bộ regression tests hiện tại, when feature mới được thêm, then ensemble production vẫn pass và không import `src.xsmn_coupled`.

## Spec Change Log

## Design Notes

Historical sample cho ngày `d` dùng latest draw của từng tỉnh trước `d` làm context và tails đúng ngày `d` làm labels A/B. Candidate pool là union của direct overlap hiện tại với labels từ Top-K neighbors. Score `q(n) = (alpha*p0 + sum(sim_i*hit_i)) / (alpha + sum(sim_i))`, trong đó `p0` lấy từ mean merged-label prevalence của training cases và luôn được mô tả là estimated hit likelihood chưa calibration. Effective neighbor count dùng Kish ESS; selector duyệt tổ hợp để tối đa tổng score dưới constraint overlap.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmn_coupled.py` -- expected: toàn bộ CMR tests pass.
- `python3 -m pytest -q` -- expected: toàn bộ regression suite pass.
- `rg -n "xsmn_coupled" src/xsmn_ensemble src/scripts/predict_ensemble.py .github/workflows` -- expected: không có production integration/import.

## Suggested Review Order

**Shadow entry point**

- Khóa đúng cặp tỉnh theo lịch trước khi đọc dữ liệu.
  [`predict_xsmn_coupled.py:33`](../../src/scripts/predict_xsmn_coupled.py#L33)

- CLI chỉ đọc history, suy luận và xuất JSON audit.
  [`predict_xsmn_coupled.py:45`](../../src/scripts/predict_xsmn_coupled.py#L45)

**Coupled inference**

- Dựng historical cases với same-province anchors và cutoff nghiêm ngặt.
  [`predictor.py:45`](../../src/xsmn_coupled/predictor.py#L45)

- Chấm motif evidence và trả Top 3 shadow có giải thích.
  [`predictor.py:130`](../../src/xsmn_coupled/predictor.py#L130)

- Tối ưu tổ hợp dưới giới hạn tối đa một overlap.
  [`predictor.py:86`](../../src/xsmn_coupled/predictor.py#L86)

**Relational signal**

- Tạo fingerprint all-to-all riêng cho chín mã giải.
  [`fingerprint.py:63`](../../src/xsmn_coupled/fingerprint.py#L63)

- Giữ mỗi prize block có tổng trọng số bằng nhau.
  [`fingerprint.py:98`](../../src/xsmn_coupled/fingerprint.py#L98)

**Data safety**

- Keyset pagination read-only tránh trùng hoặc thiếu rows.
  [`repository.py:9`](../../src/xsmn_coupled/repository.py#L9)

- Chỉ nhận complete draws trước target date.
  [`domain.py:69`](../../src/xsmn_coupled/domain.py#L69)

**Verification**

- Khóa determinism, cutoff và overlap constraint.
  [`test_xsmn_coupled.py:114`](../../tests/test_xsmn_coupled.py#L114)

- Kiểm chứng query read-only và keyset pagination.
  [`test_xsmn_coupled.py:241`](../../tests/test_xsmn_coupled.py#L241)

- Ngăn production entrypoints import CMR trong tương lai.
  [`test_xsmn_coupled.py:256`](../../tests/test_xsmn_coupled.py#L256)
