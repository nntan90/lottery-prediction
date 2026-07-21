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
