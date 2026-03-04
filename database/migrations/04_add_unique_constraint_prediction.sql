-- Migration 04: Add unique index on prediction_results
-- Prevents duplicate predictions for the same (date, region, province)
-- NOTE: Use UNIQUE INDEX with COALESCE instead of CONSTRAINT because
--       PostgreSQL NULL != NULL in regular UNIQUE constraints.

-- Step 1: Clean up any existing duplicates (keep lowest id)
DELETE FROM prediction_results
WHERE id NOT IN (
  SELECT MIN(id)
  FROM prediction_results
  GROUP BY prediction_date, region, COALESCE(province, '')
);

-- Step 2: Drop old constraint if exists (from previous migration attempt)
ALTER TABLE prediction_results
  DROP CONSTRAINT IF EXISTS uq_prediction_date_region_province;

-- Step 3: Add unique index with COALESCE to handle NULL province
CREATE UNIQUE INDEX IF NOT EXISTS uix_prediction_date_region_province
  ON prediction_results (prediction_date, region, COALESCE(province, ''));
