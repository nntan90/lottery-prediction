-- Migration: Fix predictions table constraint for upsert compatibility
-- Run this in Supabase SQL Editor

-- 1. Ensure province column exists
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS province VARCHAR(50);

-- 2. Update existing NULL provinces to empty string
UPDATE predictions SET province = '' WHERE province IS NULL;

-- 3. Set default value to empty string and make it NOT NULL
ALTER TABLE predictions ALTER COLUMN province SET DEFAULT '';
ALTER TABLE predictions ALTER COLUMN province SET NOT NULL;

-- 4. Drop old constraints/indexes if they exist (to avoid conflicts)
DROP INDEX IF EXISTS predictions_unique_idx;
ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_unique_key;

-- 5. Create the standard UNIQUE constraint that matches the upsert call
-- on_conflict='prediction_date,region,province,model_version'
ALTER TABLE predictions ADD CONSTRAINT predictions_unique_key 
UNIQUE (prediction_date, region, province, model_version);

-- Verify
SELECT * FROM pg_indexes WHERE tablename = 'predictions';
