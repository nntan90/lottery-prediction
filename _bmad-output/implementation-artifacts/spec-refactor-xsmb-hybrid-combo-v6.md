---
title: 'Refactor XSMB Hybrid Combo v6 ở chế độ shadow'
type: 'refactor'
created: '2026-07-28'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'a0d98e904e2647970980fb4ea40f9c1f1e4fa8c5'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/research/domain-xsmb-hybrid-report-ensemble-selector-research-2026-07-28.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** XSMB v5.1 đang có lỗi thứ tự medal/score, Markov nén context theo giá trị số, ChiGOF thiên lệch về nhóm tổng có nhiều cặp và combo shadow chưa dùng full evidence/production weights hoặc được lưu để hậu kiểm. Report Top 3 đã nằm trong LotoStat nên không được cộng thành vote mới.

**Approach:** Sửa correctness nhưng giữ nguyên bộ số champion, nâng combo selector thành challenger v6 dùng full 100-vector và duyệt toàn bộ bộ ba, bật shadow trong workflow production, lưu/hiển thị/verify qua contract hiện có; chỉ shadow, không thay prediction production.

## Boundaries & Constraints

**Always:** Giữ public signatures và legacy fields; mọi field mới là additive. History chỉ gồm `draw_date < target_date`. Relative/model/joint score phải ghi rõ chưa calibration. Prediction shadow phải deterministic, idempotent và không làm hỏng pipeline champion khi lỗi. Schema change phải có migration mới và cập nhật `schema_final.sql`.

**Ask First:** Đổi combo v6 thành champion; thay công thức production v5.1 ngoài ba correctness fix; thêm dependency; dùng calibrated-probability wording; thay đổi dữ liệu đã verify.

**Never:** Cộng report/LotoStat hai lần; dùng actual target-date để chọn số; sửa XSMN behavior; hardcode credential; xóa API cũ; gọi uncalibrated score là xác suất dự báo.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| XSMB daily success | 5 model hợp lệ, đủ history | Champion v5.1 được lưu như cũ; combo v6 shadow Top 3 được lưu và hiện riêng trên Telegram | N/A |
| Partial model failure | Ít nhất 3 candidate hợp lệ | Shadow dùng các model còn lại và ghi contributing/skipped models | Không ảnh hưởng champion |
| Thiếu history/candidate | Không đạt minimum gate | Lưu hoặc hiển thị trạng thái không đủ dữ liệu, không tạo Top 3 giả | Warning-only |
| Retry cùng target | Record shadow đã tồn tại | Upsert idempotent; giữ verification nếu Top 3 không đổi | Không tạo duplicate |
| Verify hậu kỳ | Có tails XSMB đúng ngày | Ghi `hit_count`, `combo_hit = hit_count >= 2`, matched pairs; Telegram tách shadow | Pending nếu chưa có kết quả |

</frozen-after-approval>

## Code Map

- `src/xsmb_ensemble/` -- năm model, report analyzer và production ensemble ordering.
- `src/xsmb_combo/` -- adapter, joint estimator, exhaustive selector và shadow service.
- `src/database/prediction_repo.py` -- persistence contract cho shadow.
- `src/scripts/predict_ensemble.py`, `src/scripts/verify_v3.py` -- orchestration prediction/verification.
- `src/bot/ensemble_messages.py`, `src/bot/verification_messages.py` -- Telegram surfaces.
- `.github/workflows/02-predict-ensemble.yml` -- bật XSMB shadow production.
- `database/migrations/`, `database/schema_final.sql` -- mở rộng documented scope của `model_predictions`.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmb_ensemble/model_markov.py`, `model_chisquare_gof.py`, `xsmb_loto_analyzer.py`, `ensemble_engine.py` -- sửa compression, sum-cardinality và display ordering mà không đổi champion set.
- [x] Năm XSMB model + `xsmb_combo/adapters.py` -- bổ sung optional full `score_vector` và `source_family`, fallback Top-N cũ.
- [x] `xsmb_combo/joint_probability.py`, `selector.py`, `shadow.py` -- bitset joint counts, full-pool exhaustive search, production weights và audit metadata.
- [x] `prediction_repo.py`, prediction/verify scripts và bot formatters -- lưu, gửi và hậu kiểm `xsmb_combo_shadow` độc lập.
- [x] Workflow/schema migration -- bật shadow và mô tả `model_predictions` dùng cho cả XSMB/XSMN.
- [x] Tests -- khóa regression champion, cutoff, full enumeration, idempotency, Telegram semantics và verify `≥2/3`.

**Acceptance Criteria:**
- Given cùng model results/history, when shadow off hoặc lỗi, then champion DB payload và Telegram prediction cũ không đổi.
- Given full vectors và 100 candidates, when combo v6 chạy, then duyệt đúng 161.700 triples và output deterministic.
- Given production credibility weights, when shadow fuse evidence, then active weights được truyền và lưu trong audit metadata.
- Given XSMB shadow đã lưu, when verify có kết quả, then `combo_hit` chỉ true tại `hit_count >= 2`.
- Given score chưa calibration, when Telegram hiển thị, then dùng “điểm tổ hợp chưa calibration”, không dùng “xác suất”.

## Design Notes

Production v5.1 vẫn là champion. Combo v6 dùng fusion evidence để xếp candidate và empirical-Bayes joint objective để chọn set; full enumeration loại candidate-pool shortcut nhưng chưa biến objective thành calibrated probability. Telegram hiển thị một aggregate combo score, không lặp score đó như score riêng của từng số.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmb_combo.py tests/test_ensemble_engine.py tests/test_prediction_repo.py tests/test_ensemble_telegram_messages.py tests/test_verify_v3.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/xsmb_combo src/xsmb_ensemble src/scripts/predict_ensemble.py src/scripts/verify_v3.py`
- `git diff --check`

## Spec Change Log

## Suggested Review Order

**Luồng production và ranh giới shadow**

- Bắt đầu tại orchestration giữ champion v5.1 và cô lập Combo v6.
  [`predict_ensemble.py:773`](../../src/scripts/predict_ensemble.py#L773)

- Shadow chỉ bật qua cấu hình workflow production rõ ràng.
  [`02-predict-ensemble.yml:40`](../../.github/workflows/02-predict-ensemble.yml#L40)

- Telegram phân tách score tổ hợp chưa calibration khỏi dự đoán chính.
  [`ensemble_messages.py:68`](../../src/bot/ensemble_messages.py#L68)

**Selector và evidence**

- Duyệt toàn bộ 161.700 bộ ba với joint objective và tie-break production.
  [`selector.py:75`](../../src/xsmb_combo/selector.py#L75)

- Bitset làm joint counting đủ nhanh cho exhaustive search.
  [`joint_probability.py:15`](../../src/xsmb_combo/joint_probability.py#L15)

- Adapter ưu tiên full 100-vector nhưng vẫn fallback Top-N cũ.
  [`adapters.py:12`](../../src/xsmb_combo/adapters.py#L12)

**Correctness của model và báo cáo**

- Ensemble chỉ sửa thứ tự medal, không đổi tập champion được chọn.
  [`ensemble_engine.py:226`](../../src/xsmb_ensemble/ensemble_engine.py#L226)

- Markov nén context theo evidence thay vì thiên vị số nhỏ.
  [`model_markov.py:76`](../../src/xsmb_ensemble/model_markov.py#L76)

- ChiGOF chuẩn hóa kỳ vọng theo cardinality của nhóm tổng.
  [`model_chisquare_gof.py:18`](../../src/xsmb_ensemble/model_chisquare_gof.py#L18)

- Báo cáo tổng/chạm xếp hạng theo quan sát trên mỗi cặp.
  [`xsmb_loto_analyzer.py:264`](../../src/xsmb_ensemble/xsmb_loto_analyzer.py#L264)

**Ledger, verify và schema**

- Persistence idempotent bảo vệ record đã verify trước mọi retry.
  [`prediction_repo.py:335`](../../src/database/prediction_repo.py#L335)

- Normalization lưu một aggregate score cùng audit metadata.
  [`prediction_repo.py:442`](../../src/database/prediction_repo.py#L442)

- Verify chờ đủ 27 rows và chấm đúng điều kiện từ 2/3.
  [`verify_v3.py:177`](../../src/scripts/verify_v3.py#L177)

- Partial unique index khóa một shadow record mỗi ngày XSMB.
  [`13_xsmb_combo_shadow_scope.sql:6`](../../database/migrations/13_xsmb_combo_shadow_scope.sql#L6)

**Regression tests**

- Full-vector contract khóa đủ candidate và số tổ hợp.
  [`test_xsmb_combo.py:98`](../../tests/test_xsmb_combo.py#L98)

- Persistence failure không được phát Telegram shadow.
  [`test_xsmb_combo.py:435`](../../tests/test_xsmb_combo.py#L435)

- Verified ledger từ chối bộ ba khác trong retry.
  [`test_prediction_repo.py:451`](../../tests/test_prediction_repo.py#L451)

- Partial XSMB draw luôn giữ verification ở trạng thái pending.
  [`test_verify_v3.py:599`](../../tests/test_verify_v3.py#L599)
