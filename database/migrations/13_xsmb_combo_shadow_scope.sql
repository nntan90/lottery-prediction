-- Extend model_predictions to cover auditable XSMB/XSMN production and shadow rows.
-- No destructive column changes: migration 12 already added the lifecycle fields.

-- Scope uniqueness to the brand-new producer. This avoids touching or
-- deduplicating verified legacy NULL-province records.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mp_xsmb_combo_shadow_unique
  ON model_predictions (prediction_date, region, model_name)
  WHERE region = 'XSMB'
    AND province IS NULL
    AND model_name = 'xsmb_combo_shadow';

COMMENT ON TABLE model_predictions IS
  'Log Top-N output and shadow challenger records for XSMB and XSMN';
COMMENT ON COLUMN model_predictions.score_semantics IS
  'Explicit score meaning; probability wording is allowed only after out-of-fold calibration';
COMMENT ON COLUMN model_predictions.run_metadata IS
  'Cutoff, scope, active weights, contributing/skipped models and deterministic audit metadata';
COMMENT ON COLUMN model_predictions.combo_hit IS
  'For canonical Top-3 ensemble/shadow rows, true only when hit_count is at least 2';
