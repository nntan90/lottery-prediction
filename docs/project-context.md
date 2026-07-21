# Analysis Lottery Project Context

## Runtime and Architecture

- Python 3.9+ application backed by Supabase/PostgreSQL and scheduled through GitHub Actions.
- Daily order is Crawl -> Build tails/features -> Predict -> Verify. Every stage must remain independently retryable.
- XSMN lookback is measured in draw occurrences, not calendar days. Province and weekday are part of the statistical grain.
- XSMN production scope is two scheduled provinces merged into the stored `XSMN/all` prediction.

## Engineering Rules

- Preserve public function signatures and database conventions unless a migration is included.
- Any schema change requires both a new file in `database/migrations/` and an update to `database/schema_final.sql`.
- Check `.github/workflows/` whenever script names, paths, arguments or execution order change.
- Never hardcode Supabase, Telegram or storage credentials. Resolve model artifacts through `model_registry` and storage APIs.
- Use only packages already declared in `requirements.txt` unless dependency approval is explicit.
- Add type hints and focused docstrings for probability, calibration and scoring logic.

## Statistical Safety

- Evaluate XSMN predictions using `(prediction_date, province, weekday, model_name)`. Never match a prediction against another province's actual tails.
- `prob_*` columns can contain legacy relative scores. Call a value a probability only after out-of-fold calibration is measured.
- The primary merged-combo KPI is `hit_count >= 2`; any-hit remains a backward-compatible secondary metric.
- Dynamic weights require enough matched observations and must be anchored, bounded and reproducible.
- Same target date, data cutoff and model versions must produce the same result. On-the-fly LSTM training is disabled by default.

## Verification

- Run `python3 -m pytest -q` before handoff.
- For database changes, inspect the migration and schema snapshot together.
- For pipeline changes, inspect all affected GitHub Actions paths and environment variables.
