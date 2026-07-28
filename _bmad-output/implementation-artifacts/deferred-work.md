# Deferred Work

- source_spec: `_bmad-output/implementation-artifacts/spec-xsmn-ensemble-production-hardening.md`
  summary: Build full production-model walk-forward replay with strict per-date artifact resolution.
  evidence: Saved-prediction evaluation cannot compare candidate model or feature changes; historical model artifacts and feature cutoffs must be resolved explicitly.

- source_spec: `_bmad-output/implementation-artifacts/spec-xsmn-ensemble-production-hardening.md`
  summary: Fit out-of-fold per-model calibration and a learned joint >=2/3 selector.
  evidence: Calibration cannot be validly manufactured from unit tests; require at least 30-52 matched observations per scope and report ECE/Brier score.

- source_spec: `_bmad-output/implementation-artifacts/spec-xsmn-ensemble-production-hardening.md`
  summary: Run legacy and hardened selectors in shadow mode for 8-12 weeks before promotion.
  evidence: Promotion requires bootstrap confidence intervals versus fixed ensemble and strongest single-model baselines.

- source_spec: `_bmad-output/implementation-artifacts/spec-walk-forward-training-validation.md`
  summary: Make model artifact upload and active-registry replacement atomic and retry-safe.
  evidence: Same-day upsert can overwrite bytes referenced by the active row, while a registry insert failure after deprecation can leave no active replacement.

- source_spec: `_bmad-output/implementation-artifacts/spec-walk-forward-training-validation.md`
  summary: Bound or monitor XGBoost training-history growth before walk-forward runtime reaches operational limits.
  evidence: Auto-training loads all station history and now performs up to five validation fits plus one final fit, so runtime and memory grow with every draw.

- source_spec: `_bmad-output/implementation-artifacts/spec-retrain-all-xsmn-provincial-models.md`
  summary: Verify active ML artifact bytes in Storage before treating a registry row as fresh.
  evidence: Current idempotency proves exact province-weekday-family metadata and cutoff, but a missing or corrupt object would only be discovered when prediction loads it.

- source_spec: `_bmad-output/implementation-artifacts/spec-retrain-all-xsmn-provincial-models.md`
  summary: Resolve historical prediction artifacts with a target-date cutoff for backtests and backfills.
  evidence: Production rollback is now prevented, but a backfilled prediction can still load the latest active artifact trained after its historical target date.

- source_spec: `_bmad-output/implementation-artifacts/spec-improve-xsmn-telegram-message.md`
  summary: Validate lottery pair range and finite scores at the shared Telegram formatter boundary.
  evidence: Existing upstream contracts supply valid values, but the formatter itself still accepts malformed pair identifiers and NaN/Infinity scores.

- source_spec: `_bmad-output/implementation-artifacts/spec-refactor-verification-and-result-message.md`
  summary: Report partially skipped prediction rows when some stations have no `tails_2d`.
  evidence: `verify_v3.py` records missing-result labels but only reports them when every prediction is skipped, so a partial verification can appear complete.

- source_spec: `_bmad-output/implementation-artifacts/spec-refactor-verification-and-result-message.md`
  summary: Add Telegram-safe HTML chunking for long verification reports.
  evidence: The notifier sends one message and production target-province overrides or stale model rows can grow the report beyond Telegram's message limit.

- source_spec: `_bmad-output/implementation-artifacts/spec-refactor-verification-and-result-message.md`
  summary: Make verification notification delivery failure observable to the workflow.
  evidence: `verify_v3.py` currently ignores a false return from `send_message()`, so a rejected or failed Telegram delivery still logs successful verification.

- source_spec: `_bmad-output/implementation-artifacts/spec-ddt-approval-window-21-to-12.md`
  summary: Persist accepted DDT run intent so a bot or laptop crash can resume the approved run after restart.
  evidence: Pending approvals and the accepted-run reservation remain in memory; adding durable pending state is explicitly Ask First in the approved spec and cannot be inferred inside this patch.
- source_spec: `_bmad-output/implementation-artifacts/spec-backfill-xsmn-lstm-missing-weekdays.md`
  summary: Bảo đảm publication LSTM không để lại object Storage mồ côi khi registry insert thất bại.
  evidence: `train_lstm.py` upload trước registry insert; lỗi ở bước insert giữ active row cũ nhưng chưa cleanup object vừa upload.
- source_spec: `_bmad-output/implementation-artifacts/spec-backfill-xsmn-lstm-missing-weekdays.md`
  summary: Thêm supervisor Telegram cho lỗi native signal của subprocess training.
  evidence: PyTorch local thoát `SIGSEGV (-11)` trước khi notifier bên trong CLI có thể gửi cảnh báo; wrapper hiện chỉ log console.
- source_spec: `_bmad-output/implementation-artifacts/spec-backfill-xsmn-lstm-missing-weekdays.md`
  summary: Khóa uniqueness cho active model theo `(region, province, weekday, model_name)`.
  evidence: Preflight và postflight application-level không loại trừ hai publisher đồng thời cùng tạo duplicate active rows; schema chưa chứng minh có partial unique constraint tương ứng.
- source_spec: `_bmad-output/implementation-artifacts/spec-refactor-xsmb-hybrid-combo-v6.md`
  summary: Bổ sung định danh prize-slot hoặc constraint ingestion để chứng minh một kỳ XSMB có đủ đúng 27 vị trí giải.
  evidence: `tails_2d` chỉ lưu `prize_code` và không có slot index/uniqueness, nên gate đếm 27 rows fail-safe với partial count nhưng không thể phân biệt một slot bị thiếu được thay bằng duplicate cùng giải.
