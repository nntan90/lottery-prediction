-- Canonical lifecycle and verification metadata for XSMN shadow predictions.
ALTER TABLE model_predictions
  ADD COLUMN IF NOT EXISTS prediction_mode VARCHAR(20) NOT NULL DEFAULT 'production',
  ADD COLUMN IF NOT EXISTS model_version VARCHAR(100),
  ADD COLUMN IF NOT EXISTS score_semantics VARCHAR(100),
  ADD COLUMN IF NOT EXISTS run_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS hit_count INTEGER,
  ADD COLUMN IF NOT EXISTS combo_hit BOOLEAN,
  ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_mp_prediction_mode
  ON model_predictions(prediction_mode, prediction_date DESC);

COMMENT ON COLUMN model_predictions.prediction_mode IS
  'production | shadow; shadow rows never contribute to ensemble verdicts';
COMMENT ON COLUMN model_predictions.model_version IS
  'Stable producer/model version for production or shadow audit';
COMMENT ON COLUMN model_predictions.score_semantics IS
  'Probability only when calibrated; otherwise explicitly uncalibrated likelihood';
COMMENT ON COLUMN model_predictions.run_metadata IS
  'Province scope, cutoff, execution source, runtime and deterministic config';
COMMENT ON COLUMN model_predictions.hit_count IS
  'Count of unique canonical Top-3 pairs matched against the exact result scope';
COMMENT ON COLUMN model_predictions.combo_hit IS
  'True when at least 2 of the canonical Top 3 pairs matched';
COMMENT ON COLUMN model_predictions.verified_at IS
  'Timestamp when post-draw verification completed';
