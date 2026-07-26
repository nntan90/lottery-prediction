---
title: 'Local DDT qua Telegram và vòng đời shadow XSMN'
type: 'feature'
created: '2026-07-26'
status: 'done'
review_loop_iteration: 0
baseline_commit: '3eb8da54d4a444aa7d0652a93eda48934d13b829'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/epics.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** DDT hiện chạy trong GitHub production với deadline 60 giây nên thường timeout, không lưu được kết quả và Telegram chỉ báo chung chung. CMR/DDT cũng không có vòng đời persistence/verification, khiến báo cáo dò số thiếu hai shadow model và không thể phân tích hiệu quả về sau.

**Approach:** Chuyển DDT thành worker local được điều khiển bằng Telegram long polling: bot gửi prompt hằng ngày hoặc nhận `/ddt`, chỉ chạy sau callback xác nhận. Tối ưu OOF để giữ nguyên tính leakage-safe nhưng giảm runtime; lưu cả CMR/DDT vào `model_predictions`; production chỉ đọc DDT đã lưu, còn post-draw verifier đối chiếu riêng shadow theo KPI ≥2/3.

## Boundaries & Constraints

**Always:** DDT/CMR vẫn là `shadow`, không tham gia weights/consensus/Top 3 production; DDT dùng đúng hai tỉnh theo lịch XSMN và operational date Asia/Ho_Chi_Minh; final DDT inference và mỗi selected OOF fold dùng toàn bộ dữ liệu trước cutoff; bot chỉ chấp nhận chat/user allowlist, callback một lần, không chạy đồng thời; mọi trạng thái success/uncalibrated/insufficient/error đều upsert idempotent và Telegram phản hồi; production result không phụ thuộc local worker; migration đi trước code.

**Ask First:** Thay estimator/merge/selector/calibration gate; đưa shadow vào KPI production, profit, auto-weight hoặc retrain; thêm dependency; đổi giờ prompt mặc định sau khi triển khai.

**Never:** Hardcode token/key/chat/user; dùng webhook/public port; chạy từ callback chưa xác thực; cắt training history hoặc dùng row `>= target_date`/`>= fold_date`; gọi likelihood chưa calibration là probability; để lỗi shadow làm fail prediction/verification; ghi secret, raw traceback hoặc lỗi dài lên Telegram.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Prompt hằng ngày hoặc `/ddt` | User/chat hợp lệ, chưa có run | Gửi nút `Đồng ý chạy`/`Hủy`; approve chạy DDT nền | Bot polling vẫn responsive |
| Callback giả/cũ/trùng | Sai allowlist, hết hạn, đã dùng hoặc run đang chạy | Không khởi chạy subprocess | Trả thông báo ngắn, không lộ cấu hình |
| DDT thành công | JSON/status/exit code hợp lệ | Lưu Top 3 + scores + metadata; gửi runtime, tỉnh, semantics | Retry không tạo row trùng |
| Insufficient/error/timeout | Child trả rc=2, malformed, nonzero hoặc >120s | Lưu status/reason ngắn; gửi Telegram tương ứng | Không ghi đè một success cùng ngày bằng failure |
| Production XSMN | DDT local đã/chưa chạy | Đọc row DDT success để render, hoặc ghi “chờ chạy local” | Không spawn DDT trên GitHub |
| Dò sau quay | Có CMR/DDT row và tails hai tỉnh | Mỗi shadow hiển thị matched, `n/3`, đạt khi `n>=2` | Thiếu kết quả thì chưa verify, không false miss |

</frozen-after-approval>

## Code Map

- `src/xsmn_digit_transition/config.py`, `service.py` -- bounded OOF anchors và full-history inference.
- `src/scripts/predict_xsmn_digit_transition.py` -- machine-readable JSON/exit contract.
- `src/scripts/ddt_local_bot.py` -- local polling, scheduled/manual prompt, callback security, lock và subprocess.
- `src/database/prediction_repo.py` -- canonical shadow normalization/upsert/read, success-wins retry policy.
- `src/scripts/predict_ensemble.py` -- persist CMR; không execute DDT; đọc DDT persisted cho Telegram.
- `src/scripts/verify_v3.py`, `src/bot/verification_messages.py` -- verify/persist/render `🧪 SHADOW — ĐỐI CHIẾU`.
- `database/migrations/12_add_shadow_prediction_tracking.sql`, `database/schema_final.sql` -- audit/verification fields cho `model_predictions`.
- `deploy/launchd/com.vietlottai.ddt-local-bot.plist.template`, `scripts/manage_ddt_local_bot.sh` -- cài/start/status LaunchAgent không chứa secret.
- `.github/workflows/02-predict-ensemble.yml`, `03-verify-predictions.yml` -- impact check; không đổi cron và production sequence.

## Tasks & Acceptance

**Execution:**
- [x] `src/xsmn_digit_transition/config.py`, `service.py` -- chọn deterministic union của 64 recent anchors/tỉnh và 64 recent common anchors; derive local rows từ regional query; giữ full pre-fold/full pre-target data.
- [x] `src/scripts/predict_xsmn_digit_transition.py` -- stdout luôn là một JSON contract; rc `0` cho success/uncalibrated, `2` cho insufficient, khác `0/2` cho error; sanitize reason.
- [x] `database/migrations/12_add_shadow_prediction_tracking.sql`, `database/schema_final.sql` -- thêm `prediction_mode`, `model_version`, `score_semantics`, `run_metadata JSONB`, `hit_count`, `combo_hit`, `verified_at`; giữ columns/unique key cũ tương thích.
- [x] `src/database/prediction_repo.py` -- map payload CMR/DDT vào Top 3 row `XSMN/all`, model names ổn định `cmr_shadow`/`ddt_shadow`, execution source/runtime/config metadata, upsert retry an toàn và legacy-schema fallback.
- [x] `src/scripts/ddt_local_bot.py` -- `/ddt [YYYY-MM-DD]`, daily prompt mặc định cấu hình env, inline approve/cancel, allowlist, TTL, one-shot callback, `asyncio` lock, 120s subprocess, persistence và outcome message.
- [x] `src/scripts/predict_ensemble.py` -- persist CMR sau production save; bỏ DDT subprocess production; đọc persisted DDT row cho message mà không đổi Top 3 cũ.
- [x] `src/scripts/verify_v3.py`, `src/bot/verification_messages.py` -- tách shadow khỏi sub-model diagnostics; verify chỉ Top 3 trên merged target-province tail set; lưu any-hit cũ cùng `hit_count/combo_hit/verified_at`; render CMR/DDT riêng, không cộng headline Multi-Model.
- [x] `deploy/launchd/...`, `scripts/manage_ddt_local_bot.sh`, `.gitignore`, `README.md` -- LaunchAgent `KeepAlive`/`RunAtLoad`, WorkingDirectory và `.env` local, log/state ignored, hướng dẫn install/start/status/stop.
- [x] `tests/` -- thêm regression cho OOF budget/leakage, CLI contract, persistence/fallback, authorization/callback/lock/timeout, production read-only DDT, shadow verification/message và unchanged Multi KPI.

**Acceptance Criteria:**
- Given TP.HCM–Long An ngày 25/07/2026, when chạy cold DDT local, then có ba evidence, transition counts vẫn `475/237`, output deterministic và runtime mục tiêu `<30s`.
- Given user Telegram hợp lệ approve một request, when child hoàn tất, then đúng một DB row `ddt_shadow` được lưu và Telegram nhận success hoặc lỗi cụ thể; duplicate approve không tạo run thứ hai.
- Given production XSMN chạy khi local DDT thiếu hoặc lỗi, when save/send hoàn tất, then production Top 3 và job status không đổi, không có DDT subprocess.
- Given CMR/DDT khớp 2 trong 3 số, when `verify_v3` chạy, then row có `hit_count=2`, `combo_hit=true`, `verified_at` và message ghi `2/3 · đạt shadow`, trong khi footer Multi-Model giữ nguyên.
- Given migration chưa được áp dụng, when shadow được lưu/verify, then legacy fields vẫn hoạt động hoặc cảnh báo actionable; không làm hỏng function cũ.

## Spec Change Log

## Design Notes

`model_predictions` tiếp tục dùng unique key `(prediction_date, region, province, model_name)`; shadow lưu ở `province='all'`, còn hai tỉnh thật nằm trong `run_metadata`. Retry success được phép thay insufficient/error; failure về sau không hạ cấp success. Giờ prompt mặc định `06:30` VN, override bằng `DDT_LOCAL_PROMPT_TIME`; `/ddt` cho phép vận hành thủ công. Launchd chỉ chứa path, không chứa credentials; process tự load `.env`.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_xsmn_digit_transition_service.py tests/test_xsmn_digit_transition_integration.py tests/test_prediction_repo.py tests/test_verify_v3.py tests/test_ensemble_telegram_messages.py tests/test_ddt_local_bot.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/xsmn_digit_transition src/scripts src/bot src/database`
- `zsh -n scripts/manage_ddt_local_bot.sh && plutil -lint deploy/launchd/com.vietlottai.ddt-local-bot.plist.template`
- `git diff --check`

**Manual checks:**
- Cài LaunchAgent, kiểm tra `launchctl`/log, gửi `/ddt`, approve/cancel và xác nhận message + DB row; chạy verifier trên fixture có 2/3 shadow hit.

## Suggested Review Order

**Local Telegram control plane**

- Entry point sở hữu allowlist, one-shot reservation, subprocess và persistence.
  [`ddt_local_bot.py:261`](../../src/scripts/ddt_local_bot.py#L261)

- Callback claim được reserve atomically trước khi task nền bắt đầu.
  [`ddt_local_bot.py:312`](../../src/scripts/ddt_local_bot.py#L312)

- Prompt hằng ngày hỗ trợ catch-up sau restart và cô lập lỗi gửi.
  [`ddt_local_bot.py:663`](../../src/scripts/ddt_local_bot.py#L663)

**Persistence và production isolation**

- Chuẩn hóa CMR/DDT thành contract shadow Top 3 có metadata.
  [`prediction_repo.py:203`](../../src/database/prediction_repo.py#L203)

- Upsert giữ success, verification và chống race unique-key.
  [`prediction_repo.py:317`](../../src/database/prediction_repo.py#L317)

- Migration bổ sung lifecycle fields mà không đổi unique key cũ.
  [`12_add_shadow_prediction_tracking.sql:1`](../../database/migrations/12_add_shadow_prediction_tracking.sql#L1)

- Production chỉ đọc DDT persisted; không spawn subprocess trên GitHub.
  [`predict_ensemble.py:146`](../../src/scripts/predict_ensemble.py#L146)

- CMR được persist sau khi Top 3 production đã lưu.
  [`predict_ensemble.py:1005`](../../src/scripts/predict_ensemble.py#L1005)

**Verification và Telegram report**

- Verifier khóa đúng hai tỉnh, đủ kết quả và KPI shadow ≥2/3.
  [`verify_v3.py:340`](../../src/scripts/verify_v3.py#L340)

- Formatter tách SHADOW khỏi diagnostics và footer Multi-Model.
  [`verification_messages.py:270`](../../src/bot/verification_messages.py#L270)

**Statistical runtime**

- OOF anchors bounded nhưng mỗi fold vẫn dùng full pre-fold history.
  [`service.py:87`](../../src/xsmn_digit_transition/service.py#L87)

- Service derive local rows từ regional history để bỏ query trùng.
  [`service.py:566`](../../src/xsmn_digit_transition/service.py#L566)

**Operations và regression**

- LaunchAgent manager render lại path và restart an toàn.
  [`manage_ddt_local_bot.sh:32`](../../scripts/manage_ddt_local_bot.sh#L32)

- Regression khóa callback race, scope completeness và retry verification.
  [`test_ddt_local_bot.py:139`](../../tests/test_ddt_local_bot.py#L139)
  [`test_verify_v3.py:511`](../../tests/test_verify_v3.py#L511)
  [`test_prediction_repo.py:356`](../../tests/test_prediction_repo.py#L356)
