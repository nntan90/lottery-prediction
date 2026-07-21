-- Persist enough ensemble context to reproduce and audit a prediction run.
ALTER TABLE prediction_results
  ADD COLUMN IF NOT EXISTS run_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN prediction_results.run_metadata IS
  'Target provinces, effective weights, score type, combo score and Top-10 candidates';
