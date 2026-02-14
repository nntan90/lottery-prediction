-- Migration: Add province column to predictions table
-- Run this in Supabase SQL Editor

-- Step 1: Add province column (nullable for backward compatibility)
ALTER TABLE predictions 
ADD COLUMN IF NOT EXISTS province VARCHAR(50);

-- Step 2: Create index on province for faster queries
CREATE INDEX IF NOT EXISTS idx_predictions_province ON predictions(province);

-- Step 3: Update unique constraint to include province
-- This ensures one prediction per (date, region, province, model_version)
CREATE UNIQUE INDEX IF NOT EXISTS predictions_unique_idx 
ON predictions(prediction_date, region, COALESCE(province, ''), model_version);

-- Verify the changes
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_name = 'predictions' 
ORDER BY ordinal_position;
