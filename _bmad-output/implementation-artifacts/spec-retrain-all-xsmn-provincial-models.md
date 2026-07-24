---
title: 'Retrain toàn bộ model theo tỉnh XSMN sau mỗi kỳ'
type: 'feature'
created: '2026-07-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'cf9a135'
context:
  - '{project-root}/docs/project-context.md'
---

<frozen-after-approval reason="User yêu cầu tất cả model của tỉnh phải được cập nhật sau mỗi kỳ quay">

## Intent

**Problem:** Sau verify, hệ thống hiện chỉ auto-retrain XGBoost; LSTM vẫn thủ công, bốn model thống kê không có freshness audit, job 23:00 có thể chạy trước crawl fallback, và target phụ thuộc prediction single. Vì vậy không chứng minh được cả sáu model của tỉnh đã cập nhật kỳ mới nhất.

**Approach:** Sau verify, xác định tỉnh từ lịch quay và 100 label, rồi chạy một coordinator cập nhật đủ sáu family: retrain XGBoost/LSTM; refresh và health-check Frequency/Gap/Markov/CDM trên history đã chứa kỳ vừa quay. Ghi audit theo tỉnh–weekday để biết từng family fresh, stale hay failed.

## Boundaries & Constraints

**Always:** Xử lý mỗi `(province, weekday)` đúng một lần và chỉ khi đủ 100 label; sáu family đều phải có audit result cho target date; XGBoost/LSTM có `train_end_date=target`; LSTM final-fit toàn bộ sequences; bốn rule families phải đọc được kỳ target trong same-weekday history và trả refresh health thành công; TP.HCM tách thứ Hai/thứ Bảy; seed/version/output deterministic; registry cô lập theo family; một family lỗi không chặn family/tỉnh khác; rerun cùng ngày idempotent.

**Ask First:** Thay schema database, bật retrain XSMB, đổi ensemble weights/bonus, đổi thuật toán rule-based, hoặc chuyển sang workflow matrix/parallel có chi phí vận hành khác.

**Never:** Tạo artifact ML giả cho rule families; phụ thuộc prediction single để tìm tỉnh; train on-the-fly trong prediction; trộn weekday LSTM TP.HCM; ghi freshness nếu history chưa chứa target; đánh dấu toàn bộ thành công khi còn family lỗi.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Kỳ hoàn chỉnh | Tỉnh đúng lịch có 100 label | Retrain XGB/LSTM và refresh 4 rule families | Ghi audit đủ 6 family |
| Rule refresh | History same-weekday đã có target draw | Recompute Freq/Gap/Markov/CDM và xác nhận cutoff | Fail family nếu latest draw khác target |
| Thiếu single prediction | Feature label đầy đủ nhưng không có row single | Vẫn train cả hai; XGB dùng strategy maintain | Log thiếu prediction, không bỏ tỉnh |
| Rerun cùng ngày | Một hoặc cả hai family đã fresh | Chỉ chạy family còn stale | Không ghi retrain trùng |
| Partial failure | XGB lỗi, LSTM chạy được hoặc ngược lại | Tiếp tục family/tỉnh còn lại | Workflow fail cuối cùng và Telegram nêu family lỗi |
| Thiếu/khuyết label | Tỉnh lịch quay không có đúng 100 pair | Không train tỉnh đó | Workflow fail, giữ model active cũ |
| TP.HCM hai lịch | Kỳ thứ Hai hoặc thứ Bảy | Train/load đúng artifact weekday | Legacy NULL chỉ là fallback đọc |

</frozen-after-approval>

## Code Map

- `src/agent/master_retrain_agent.py` -- resolve targets; điều phối/idempotency đủ sáu family; ghi audit và tổng hợp failure.
- `src/agent/provincial_model_refresh.py` -- refresh/health-check Frequency, Gap, Markov, CDM trên latest same-weekday history.
- `src/agent/hyperparameter_strategy.py` -- tạo command arguments deterministic cho hai model families.
- `src/scripts/train_lstm.py` -- weekday-filtered loading, deterministic validation và final full-data fit, registry metadata.
- `src/xsmn_ensemble/model_lstm.py` -- lookup LSTM theo tỉnh–weekday với legacy fallback.
- `src/scripts/train_xgb.py` -- ghi/filter `model_name=xgboost_core` để cô lập family.
- `src/xsmn_ensemble/model_xgboost.py` -- lookup XGB family-aware với legacy fallback.
- `.github/workflows/01-daily-crawl.yml` -- job retrain sau verify thành công.
- `.github/workflows/04-check-training.yml` -- recovery 23:00 và qua đêm dùng cùng coordinator idempotent.
- `.github/workflows/05-train-model.yml`, `.github/workflows/06-train-lstm.yml` -- serialize manual training với coordinator để không publish chồng artifact.
- `tests/test_master_retrain_agent.py`, `tests/test_provincial_model_refresh.py`, `tests/test_train_lstm.py`, `tests/test_train_xgb.py`, `tests/test_xsmn_refactor.py` -- regression coverage.

## Tasks & Acceptance

**Execution:**

- [x] Refactor coordinator để lấy tỉnh từ schedule + labeled `pair_features`, không từ single predictions.
- [x] Chạy XGBoost và LSTM độc lập cho từng target; skip artifact đã fresh.
- [x] Refresh Frequency/Gap/Markov/CDM và xác nhận history latest date bằng target.
- [x] Thêm weekday và final full-sequence refit deterministic cho LSTM.
- [x] Cô lập lookup/deprecation/registration của XGB và LSTM theo `model_name` + weekday.
- [x] Nối coordinator sau verify; giữ job 23:00 làm idempotent recovery.
- [x] Ghi audit đủ sáu family trong `agent_actions` JSON hiện hữu, không đổi schema.
- [x] Test target completeness, đủ 6 family, missing single, rerun, partial failure, weekday và final refit.

**Acceptance Criteria:**

- Given một tỉnh có đủ 100 label hôm nay, when post-verify chạy, then active XGB và LSTM đúng weekday đều có `train_end_date=today`.
- Given kỳ hôm nay đã có trong `tails_2d`, when refresh chạy, then Freq/Gap/Markov/CDM đều xác nhận latest same-weekday draw là hôm nay.
- Given không có prediction single, when labeled feature hoàn chỉnh, then tỉnh vẫn được train với strategy maintain.
- Given XGB đã fresh nhưng LSTM stale, when recovery chạy, then chỉ LSTM được gọi.
- Given một subprocess lỗi, when coordinator hoàn tất, then các target khác vẫn chạy và workflow trả failure.
- Given prediction ngày kế tiếp, when rule scorers đọc history, then kỳ vừa verify nằm trong lookback trước target.

## Spec Change Log

## Design Notes

Job post-verify là đường chính; cron 23:00 chỉ recovery. Artifact freshness dùng `(province, weekday, model_name, train_end_date)`. Rule freshness là một lần recompute không persist prediction, kèm `latest_history_date`, `n_draws_used`, status và error trong `agent_actions.new_params.model_updates`. LSTM chọn `best_epoch`, rồi model mới cùng seed fit toàn bộ sequences; artifact validation không được publish.

## Verification

**Commands:**

- `python3 -m pytest -q tests/test_master_retrain_agent.py tests/test_provincial_model_refresh.py tests/test_train_lstm.py tests/test_train_xgb.py tests/test_xsmn_refactor.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src tests`
- Parse toàn bộ `.github/workflows/*.yml` bằng PyYAML.
- `git diff --check`

## Suggested Review Order

**Điều phối và tính mới**

- Entry point điều phối độc lập đủ sáu family cho từng tỉnh–weekday.
  [`master_retrain_agent.py:313`](../../src/agent/master_retrain_agent.py#L313)

- Target lấy từ lịch quay và chỉ chạy khi đủ đúng 100 label.
  [`master_retrain_agent.py:93`](../../src/agent/master_retrain_agent.py#L93)

- Backfill cũ được chặn để không ghi đè artifact production mới hơn.
  [`master_retrain_agent.py:147`](../../src/agent/master_retrain_agent.py#L147)

- Bốn rule family recompute trên history vừa chứa kỳ mới nhất.
  [`provincial_model_refresh.py:36`](../../src/agent/provincial_model_refresh.py#L36)

**Huấn luyện và publication**

- LSTM chọn epoch theo validation rồi refit model mới trên toàn bộ sequence.
  [`train_lstm.py:32`](../../src/scripts/train_lstm.py#L32)

- XGBoost publish replacement trước khi deprecate fallback đang active.
  [`train_xgb.py:436`](../../src/scripts/train_xgb.py#L436)

- LSTM lookup ưu tiên đúng tỉnh–weekday và vẫn đọc được artifact legacy.
  [`model_lstm.py:280`](../../src/xsmn_ensemble/model_lstm.py#L280)

- XGBoost giữ ưu tiên tỉnh khi fallback qua family metadata cũ.
  [`model_xgboost.py:87`](../../src/xsmn_ensemble/model_xgboost.py#L87)

**Vận hành và phục hồi**

- Post-verify là đường chính cho cập nhật model ngay sau crawl.
  [`01-daily-crawl.yml:305`](../../.github/workflows/01-daily-crawl.yml#L305)

- Recovery qua đêm idempotent xử lý crawl hoặc training bị trễ.
  [`04-check-training.yml:6`](../../.github/workflows/04-check-training.yml#L6)

- TP.HCM không trộn chuỗi miss giữa lịch thứ Hai và thứ Bảy.
  [`decision_engine.py:200`](../../src/agent/decision_engine.py#L200)

**Kiểm thử**

- Regression bao phủ missing prediction, partial failure, backfill và audit.
  [`test_master_retrain_agent.py:144`](../../tests/test_master_retrain_agent.py#L144)

- Rule refresh xác nhận cutoff target và lần chạy next-week tương ứng.
  [`test_provincial_model_refresh.py:8`](../../tests/test_provincial_model_refresh.py#L8)

- Final-fit LSTM bắt buộc dùng toàn bộ sequence với best epoch.
  [`test_train_lstm.py:4`](../../tests/test_train_lstm.py#L4)
