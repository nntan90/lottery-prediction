---
title: 'XSMN Ensemble Production Hardening'
type: 'refactor'
created: '2026-07-19'
status: 'done'
review_loop_iteration: 2
context:
  - 'docs/project-context.md'
  - 'docs/system_architecture.md'
---

<frozen-after-approval reason="User requested backward-compatible XSMN refactor and BMAD adoption">

## Intent

**Problem:** XSMN history, credibility and evaluation mixed statistical grains, sparse heuristics were presented too much like probabilities, and prediction runs lacked enough audit context for reliable reproduction.

**Approach:** Preserve orchestration and stored Top-3 contracts while correcting province/weekday grain, bounding dynamic behavior, exposing the true >=2/3 KPI, strengthening data gates, and installing BMAD as the repository development workflow.

## Boundaries & Constraints

**Always:** Keep the daily workflow paths and public model interfaces valid; retain fault tolerance when one model fails; preserve Top-5 model logging while allowing a wider internal candidate pool; include migrations for schema additions.

**Ask First:** Enabling on-the-fly LSTM in production, promoting calibrated weights, replacing the current selector with a learned meta-model, or adding dependencies.

**Never:** Use post-target data, merge actual tails across provinces during credibility evaluation, call an uncalibrated ranking score a probability, or silently accept incomplete target-province crawl data.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Correct history | Three same-weekday draws contain pair 42 | Pair 42 contributes three history occurrences | Missing `draw_date` raises a clear error |
| Scoped credibility | One model predicts two provinces | Each prediction matches only its province actuals | Insufficient samples return fixed XSMN weights |
| Missing LSTM artifact | No registry model | Remaining models continue | LSTM returns fault-tolerant error; explicit env flag restores deterministic legacy fallback |
| Incomplete crawl | One target province or prize field missing | Crawl job is failed and history is not polluted | Error names missing province/fields |

</frozen-after-approval>

## Code Map

- `src/scripts/predict_ensemble.py` -- XSMN orchestration, draw-preserving history and run audit context.
- `src/scoring/credibility_scorer.py` -- scoped evaluation, YAML-driven safeguards and fixed-weight fallback.
- `src/xsmn_ensemble/ensemble_engine.py` -- model-family consensus and uncalibrated combo ranking.
- `src/analytics/backtest.py` -- any-hit compatibility plus primary >=2/3 metrics and baselines.
- `src/crawler/xsmn_crawler.py` -- 18-prize station validation.
- `database/migrations/11_add_prediction_run_metadata.sql` -- reproducibility metadata.

## Tasks & Acceptance

**Execution:**

- [x] Correct draw, province and weekday grain in history/model inputs.
- [x] Anchor and bound credibility weights; use fixed weights below sample threshold.
- [x] Make LSTM fallback deterministic and opt-in; remove Markov numeric truncation bias.
- [x] Separate model-family consensus from source/province counts and shrink sparse combo history.
- [x] Add >=2/3 backtest metrics, crawl/model-run gates and prediction audit metadata.
- [x] Install BMAD v6 for Codex and add persistent project context.

**Acceptance Criteria:**

- Given predictions from two provinces, when credibility is calculated, then a hit in one province cannot score the other province.
- Given fewer than 30 matched XSMN observations, when credibility runs, then configured fixed weights are returned.
- Given the same date/province and fallback permission, when LSTM trains twice, then both runs use the same seed.
- Given a Top-3 prediction, when evaluated, then reports include 0/3 through 3/3, >=2/3 rate and its hypergeometric random lift.
- Given a schema without `run_metadata`, when saving a prediction, then compatibility fallback still writes the legacy record.

## Design Notes

Model Top-5 remains the persisted/display contract. Models now expose Top-10 internally to reduce rank truncation. Combo output explicitly carries `ranking_score_uncalibrated`; true calibration and learned joint-hit optimization require out-of-fold production history and remain gated shadow work.

## Two-Agent Review Record

- **Linh, Principal Lottery Data Analyst:** validated the two-province schedule, draw/weekday grain, PostgREST pagination risk, completeness gates, >=2/3 KPI denominator and run audit fields.
- **Quang, Principal Lottery Data Scientist:** validated temporal leakage controls, active-model weight anchoring, family-level consensus, sparse-history shrinkage, LSTM reproducibility and the distinction between rankings and calibrated probabilities.
- **Joint decision:** preserve the Top-3 database and Top-5 model-log contracts, use Top-10 only inside aggregation, keep dynamic tuning behind sample gates, and defer learned calibration/promotion until out-of-time evidence exists.
- **Baseline clarification:** fixed XSMN weights are neutral and equal at `1/6` for each of the six active model families; credibility can diverge only after the two-province sample gate passes.

## Verification

**Commands:**

- `python3 -m pytest -q` -- 184 tests pass; one dependency deprecation warning remains.
- `python3 -m compileall -q src tests` -- source and tests compile.
- Parse all `.github/workflows/*.yml` and `config/scoring.yaml` with PyYAML -- 13 workflows and scoring config are valid.
- `uv run --python 3.11 _bmad/scripts/resolve_config.py --project-root .` -- BMAD paths and expert/Vietnamese configuration resolve.
- `resolve_party.py` -- `xsmn-model-council`, auto mode, two members and memory enabled.
- `git diff --check` -- no whitespace errors.
