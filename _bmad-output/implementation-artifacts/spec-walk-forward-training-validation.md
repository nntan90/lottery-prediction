---
title: 'Walk-Forward Validation cho XGBoost Auto-Training'
type: 'refactor'
created: '2026-07-24'
status: 'done'
review_loop_iteration: 0
baseline_commit: '4f177071ff4685c415606b18d7b6cbc591f260df'
context:
  - '{project-root}/docs/project-context.md'
---

<frozen-after-approval reason="User yêu cầu áp dụng walk-forward cho các kỳ train tự động sắp tới">

## Intent

**Problem:** `train_xgb.py` hiện chỉ chia thời gian 80/20 một lần, khiến AUC/Hit@3 phụ thuộc vào một đoạn validation. Model upload cũng chỉ fit trên 80% dữ liệu. Hit@3 giả định mỗi 100 dòng liên tiếp là một kỳ mà chưa kiểm tra đủ cặp.

**Approach:** Thay single holdout bằng expanding walk-forward theo `feature_date`, train model độc lập ở từng fold, tổng hợp metric ổn định, sau đó fit một model production mới trên toàn bộ dữ liệu hợp lệ. Giữ nguyên CLI mặc định để mọi luồng auto-retrain hiện có tự động dùng logic mới.

## Boundaries & Constraints

**Always:** Chia theo kỳ quay, không xé 100 cặp; train chỉ dùng ngày trước validation; sort theo `feature_date, pair`; mỗi kỳ phải có đúng 100 pair duy nhất `00..99`; dùng cùng hyperparameters cho mọi fold và final fit; metric phải reproducible.

**Ask First:** Thay schema database, thay ngưỡng quyết định của Retrain Agent, thêm thư viện, tune ensemble weight/bonus `+0.2`, hoặc mở rộng walk-forward sang LSTM và các rule-based model.

**Never:** Shuffle dữ liệu thời gian; dùng model của fold cuối làm model production; bỏ qua kỳ lỗi dữ liệu một cách im lặng; dùng dữ liệu validation trong train của cùng fold; gọi AUC walk-forward là bảo đảm dự đoán trúng.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Đủ dữ liệu | Ít nhất 60 kỳ hợp lệ | Tối đa 5 fold expanding, validation liên tiếp và fold cuối chạm kỳ mới nhất | Log date range và metric từng fold |
| Force train | Tối thiểu 24 kỳ | Tự giảm số fold/window nhưng cần ít nhất 2 fold | Dừng rõ ràng nếu không tạo đủ fold |
| Kỳ không đủ | Thiếu pair hoặc trùng pair | Không train/upload model mới | Báo ngày và lỗi integrity |
| Fold AUC lỗi | Validation chỉ có một class | Không dùng AUC fold đó để tổng hợp | Cần ít nhất 2 AUC fold hợp lệ |
| Final fit | Walk-forward hoàn tất | Model mới fit toàn bộ các kỳ rồi mới upload/activate | Không deprecate model cũ nếu train/upload thất bại |

</frozen-after-approval>

## Code Map

- `src/scripts/train_xgb.py` -- validate/sort; tạo folds; evaluate, aggregate, final fit và báo metric.
- `src/models/xgb_model.py` -- không biến lỗi tính AUC thành giá trị giả `0.5`.
- `src/agent/master_retrain_agent.py` -- tăng subprocess timeout phù hợp chi phí nhiều fold.
- `src/scripts/retrain_all_models.py` -- đồng bộ timeout khi retrain hàng loạt.
- `src/scripts/retrain_weekday_models.py` -- đồng bộ timeout cho local weekday retrain.
- `.github/workflows/05-train-model.yml` -- dành overhead ngoài 30 phút subprocess training.
- `tests/test_train_xgb.py` -- unit/regression tests cho fold boundaries, integrity, aggregation và final refit.

## Tasks & Acceptance

**Execution:**

- [x] Thay `time_based_split()` bằng bộ chia expanding walk-forward theo distinct draw dates.
- [x] Dùng mặc định tối đa 5 fold; auto window `max(3, min(13, total_draws // 10))`; initial train nhận phần còn lại và có tối thiểu 12 kỳ.
- [x] Train model mới mỗi fold; lưu median AUC hợp lệ và Hit@3 weighted theo số kỳ validation.
- [x] Log thêm mean/min/std AUC, date range và Hit@3 từng fold nhưng không đổi schema.
- [x] Fit model production mới trên toàn bộ dữ liệu sau validation rồi mới save/upload.
- [x] Tăng timeout XGBoost auto/local từ 600 lên 1800 giây và workflow lên 45 phút.
- [x] Thêm test và xác nhận các workflow gọi `train_xgb.py` vẫn tương thích.

**Acceptance Criteria:**

- Given dữ liệu bị shuffle, when tạo folds, then mọi kỳ vẫn nguyên nhóm, train dates tăng dần và luôn nhỏ hơn validation dates.
- Given 60 kỳ hợp lệ, when dùng mặc định, then tạo 5 fold liên tiếp và fold cuối đánh giá đến kỳ mới nhất.
- Given 24 kỳ với `--force`, when validate, then tạo được ít nhất 2 fold mà không dùng số ngày lịch làm lookback.
- Given một kỳ thiếu/trùng pair, when train, then pipeline dừng trước khi upload hoặc deprecate model.
- Given N fold hợp lệ, when training hoàn tất, then có N lần fit validation và một final fit chứa toàn bộ dates.
- Given auto-retrain gọi CLI cũ, when chạy kỳ kế tiếp, then walk-forward được dùng mà không cần flag mới.

## Design Notes

Với `V = max(3, min(13, total_draws // 10))`, số fold là `min(5, floor((total_draws - 12) / V))`; phần dư vào initial train để fold cuối kết thúc ở ngày mới nhất. `metric_auc` là median AUC fold hợp lệ; `metric_hit_rate` là tổng kỳ Hit@3 chia tổng kỳ validation. Không pooling xác suất giữa các model fold vì score có thể khác calibration scale.

## Verification

**Commands:**

- `python3 -m pytest -q tests/test_train_xgb.py tests/test_decision_engine.py tests/test_master_retrain_agent.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/scripts/train_xgb.py src/agent tests/test_train_xgb.py`
- `git diff --check`

## Suggested Review Order

**Walk-forward core**

- Bắt đầu tại orchestration: nhiều fold độc lập, metric tổng hợp, rồi final refit.
  [`train_xgb.py:210`](../../src/scripts/train_xgb.py#L210)

- Khóa integrity 100 cặp và nhãn nhị phân trước mọi lần train.
  [`train_xgb.py:100`](../../src/scripts/train_xgb.py#L100)

- Tạo expanding folds theo kỳ quay, không theo số ngày lịch.
  [`train_xgb.py:149`](../../src/scripts/train_xgb.py#L149)

- Không biến lỗi tính AUC thành giá trị giả 0.5.
  [`xgb_model.py:98`](../../src/models/xgb_model.py#L98)

**Production lifecycle**

- Fit model production mới trên toàn bộ lịch sử hợp lệ.
  [`train_xgb.py:305`](../../src/scripts/train_xgb.py#L305)

- Lỗi validation được cảnh báo trước save, upload hoặc deprecate.
  [`train_xgb.py:380`](../../src/scripts/train_xgb.py#L380)

- Registry giữ scalar median AUC và weighted Hit@3 tương thích.
  [`train_xgb.py:431`](../../src/scripts/train_xgb.py#L431)

**Automation safeguards**

- Master Retrain Agent cho phép tối đa 30 phút XGBoost.
  [`master_retrain_agent.py:148`](../../src/agent/master_retrain_agent.py#L148)

- Batch retrain chỉ tăng timeout XGBoost, giữ nguyên LSTM.
  [`retrain_all_models.py:71`](../../src/scripts/retrain_all_models.py#L71)

- Local weekday timeout trở thành failure và tiếp tục batch.
  [`retrain_weekday_models.py:64`](../../src/scripts/retrain_weekday_models.py#L64)

- Workflow dành thêm 15 phút cho setup và thông báo lỗi.
  [`05-train-model.yml:24`](../../.github/workflows/05-train-model.yml#L24)

**Verification and follow-up**

- Test fold boundaries, corruption, aggregation và final full-data fit.
  [`test_train_xgb.py:40`](../../tests/test_train_xgb.py#L40)

- Test metric invalid, timeout recovery và workflow compatibility.
  [`test_train_xgb.py:165`](../../tests/test_train_xgb.py#L165)

- Ghi riêng các rủi ro activation và runtime ngoài phạm vi.
  [`deferred-work.md:15`](deferred-work.md#L15)
