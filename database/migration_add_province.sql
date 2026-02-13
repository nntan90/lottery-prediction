-- Migration: Add province column to lottery_draws table
-- Run this in Supabase SQL Editor

-- Step 1: Add province column (nullable for now to not break existing data)
ALTER TABLE lottery_draws 
ADD COLUMN IF NOT EXISTS province VARCHAR(50);

-- Step 2: Drop old unique constraint
ALTER TABLE lottery_draws 
DROP CONSTRAINT IF EXISTS lottery_draws_draw_date_region_key;

-- Step 3: Add new unique constraint including province
-- For XSMB: province will be NULL
-- For XSMN: province will be the province name (e.g., 'tp-hcm', 'dong-thap')
ALTER TABLE lottery_draws 
ADD CONSTRAINT lottery_draws_draw_date_region_province_key 
UNIQUE (draw_date, region, province);

-- Step 4: Create index on province for faster queries
CREATE INDEX IF NOT EXISTS idx_lottery_draws_province ON lottery_draws(province);

-- Verify the changes
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_name = 'lottery_draws' 
ORDER BY ordinal_position;
