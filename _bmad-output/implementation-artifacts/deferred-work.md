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
