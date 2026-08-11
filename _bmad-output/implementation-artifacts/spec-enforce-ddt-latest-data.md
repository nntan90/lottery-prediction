---
title: 'Enforce latest-data integrity for XSMN DDT'
type: 'bugfix'
created: '2026-08-11'
status: 'done'
review_loop_iteration: 0
baseline_commit: 'f3e1dbfad9a3606cdeae9a3f6ce905f713114563'
context:
  - 'docs/project-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** DDT queries Supabase on every approved run, but incomplete newest draws are silently discarded and any older complete anchor is accepted. A persisted success also blocks regeneration after inputs advance, while its metadata records only the cutoff—not the actual anchors or input version—so identical Top 3 sets cannot be audited.

**Approach:** Add a fail-closed, leakage-safe freshness manifest before DDT scoring, certify the latest scheduled boundary draws against both `lottery_draws` and `tails_2d`, persist deterministic input fingerprints, and deduplicate by certified watermark rather than status alone. Keep DDT local, shadow-only, and explicitly approved through Telegram.

## Boundaries & Constraints

**Always:** Query current Supabase state for each run; retain strict `draw_date < target_date` and the existing CLI status/exit contract; resolve exactly two target provinces from the existing schedule; derive each expected anchor from its latest scheduled occurrence before the target (including TP.HCM Mon/Sat routing); require complete 18-tail target anchors and every scheduled XSMN draw on `D-1`; verify boundary raw draws and extracted tails agree; compute order-independent SHA-256 fingerprints without IDs or credentials; recheck the boundary watermark before persistence; store manifest version, expected/actual dates, coverage counts, full-history hash and boundary watermark in `run_metadata`; preserve successful/verified ledger lifecycle and all approval-window, allowlist, locking and wake behavior.

**Ask First:** Changing the DDT estimator, calibration, selector, Top 3 rules, province schedules, crawler write behavior, database schema, automatic execution policy, or any verified historical prediction.

**Never:** Use target/future rows, infer freshness from timestamps alone, cache a prior prediction as current, silently fall back to an older anchor, auto-run after data changes, overwrite a verified row, expose secrets/raw provider errors, or change XSMB/CMR/relationship/LLM_Gen behavior.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Certified current input | Both target anchors and all `D-1` scheduled draws are complete and raw/tails match | Run model; persist Top 3 plus manifest; Telegram shows anchors, regional date and short watermark | N/A |
| Same Top 3, new week | Input fingerprint/anchors changed but selector returns the same numbers | Treat as a fresh valid run; changed scores/hash prove new input | Do not label as cached/stale |
| Missing or partial boundary | Older complete history exists but any required latest draw lacks exactly 18 tails | Do not score or persist a new success | Return existing `insufficient_evidence` status with reason `input_not_fresh` and missing province/date; Telegram asks user to retry after crawl |
| Raw/tails mismatch | Latest `lottery_draws` was corrected but `tails_2d` still has old content | Do not score | Return source-integrity detail without leaking raw prizes |
| Input changes during run | Post-run boundary watermark differs from pre-run watermark | Discard result | Return retryable `input_changed_during_run` |
| Existing current success | Unverified success has the same certified watermark | Suppress duplicate prompt/run | Report already current |
| Existing stale/legacy success | Unverified success has a different or missing watermark | Offer a new approval; rerun once and update audit even if Top 3 is unchanged | A failed rerun must not downgrade prior success |
| Verified success | Watermark differs or metadata is legacy | Keep immutable | Require separate user approval for historical correction |
| DB/read failure | Freshness cannot be proven | No model result is certified | Fail closed with sanitized Telegram error; remain manually retryable in the approval window |

</frozen-after-approval>

## Code Map

- `src/xsmn_ensemble/resolve_provinces.py` -- canonical full XSMN draw schedule and previous-occurrence resolution while preserving the existing two-province API.
- `src/crawler/xsmn_crawler.py` -- consume the shared full schedule without changing crawl behavior or public class constants.
- `src/xsmn_digit_transition/domain.py` -- pure completeness, canonical content and manifest contracts.
- `src/xsmn_digit_transition/repository.py` -- fresh history plus small boundary/raw-draw reads; no writes.
- `src/xsmn_digit_transition/service.py` -- pre/post freshness gates and audit metadata around the unchanged predictor.
- `src/database/prediction_repo.py` -- preserve manifest through normalization and safely refresh an unverified DDT row.
- `src/scripts/ddt_local_bot.py` -- watermark-aware dedupe/retry and clearer Telegram evidence.
- `src/scripts/predict_ensemble.py` -- display only persisted DDT results whose certified watermark still matches the requested target/scope and current boundary data.
- `tests/test_xsmn_digit_transition_data.py`, `tests/test_xsmn_digit_transition_service.py`, `tests/test_xsmn_digit_transition_integration.py`, `tests/test_prediction_repo.py`, `tests/test_ddt_local_bot.py` -- regression coverage.

## Tasks & Acceptance

**Execution:**
- [x] Add shared full-schedule/previous-anchor resolution without changing existing schedule outputs.
- [x] Build and validate a deterministic freshness manifest from complete normalized draws and boundary raw/tail parity.
- [x] Gate operational generation before scoring and again before returning; attach audit metadata to success and failure payloads.
- [x] Replace status-only DDT dedupe with verified/current/stale/unknown decisions; preserve explicit approval and success-wins persistence.
- [x] Improve local outcome and production shadow messages with certified input evidence; reject uncertified current rows.
- [x] Add matrix tests and inspect workflow/LaunchAgent references; no migration or cron/path change.

**Acceptance Criteria:**
- Given the production snapshots for 04/08 and 11/08, fresh recomputation may return `01,32,92` for both, but manifests show anchors `28/07→04/08`, regional dates `03/08→10/08`, distinct fingerprints and changed evidence scores.
- Given an incomplete newest scheduled draw, DDT cannot emit or persist a newly certified Top 3 from an older anchor.
- Given an unverified success whose watermark advances, one newly approved run can replace/update it; the same watermark remains idempotent and verified rows remain immutable.
- Given a persisted DDT row without a valid manifest or whose watermark no longer matches current boundary data, the production Telegram report shows pending/stale rather than presenting it as current.

## Spec Change Log

## Design Notes

The boundary watermark covers only the latest draws required to prove freshness and is cheap enough for Telegram dedupe. A separate full-history hash records the exact normalized evidence consumed by the model. This avoids repeatedly hashing the complete regional history merely to decide whether an existing success is current.

## Verification

**Commands:**
- `.venv/bin/python -m pytest -q tests/test_xsmn_digit_transition_data.py tests/test_xsmn_digit_transition_service.py tests/test_xsmn_digit_transition_integration.py tests/test_prediction_repo.py tests/test_ddt_local_bot.py` -- all focused contracts pass.
- `.venv/bin/python -m pytest -q` -- full suite passes.
- `.venv/bin/python -m compileall -q src` -- Python compiles.
- `zsh -n scripts/manage_ddt_local_bot.sh && plutil -lint deploy/launchd/com.vietlottai.ddt-local-bot.plist.template` -- local scheduler artifacts remain valid.
- `git diff --check` -- clean patch formatting.

## Suggested Review Order

**Freshness execution**

- Start here: operational generation certifies, scores, then postchecks the same boundary.
  [`service.py:697`](../../src/xsmn_digit_transition/service.py#L697)

- Pure manifest construction enforces exact raw/tail parity and deterministic fingerprints.
  [`domain.py:214`](../../src/xsmn_digit_transition/domain.py#L214)

- Bounded repository reads keep Telegram currentness checks cheap and leakage-safe.
  [`repository.py:138`](../../src/xsmn_digit_transition/repository.py#L138)

- Previous-occurrence routing handles TP.HCM's Monday and Saturday schedule correctly.
  [`resolve_provinces.py:99`](../../src/xsmn_ensemble/resolve_provinces.py#L99)

**Ledger and delivery safety**

- Approved local execution revalidates the watermark immediately before persistence.
  [`ddt_local_bot.py:612`](../../src/scripts/ddt_local_bot.py#L612)

- Manifest validation and CAS rules protect verified rows and concurrent refreshes.
  [`prediction_repo.py:407`](../../src/database/prediction_repo.py#L407)

- Production renders only rows still matching current certified boundary data.
  [`predict_ensemble.py:167`](../../src/scripts/predict_ensemble.py#L167)

**Regression evidence**

- Service tests cover incomplete inputs, consumed-anchor mismatch, and mid-run drift.
  [`test_xsmn_digit_transition_service.py:225`](../../tests/test_xsmn_digit_transition_service.py#L225)

- Persistence tests lock verified-null CAS and unique-insert race behavior.
  [`test_prediction_repo.py:1029`](../../tests/test_prediction_repo.py#L1029)

- Bot tests prevent uncertified display and stale pre-save persistence.
  [`test_ddt_local_bot.py:442`](../../tests/test_ddt_local_bot.py#L442)
