-- Migration: Add eighth_prize column to lottery_draws table
-- XSMN has an 8th prize which was missing in the original schema

ALTER TABLE lottery_draws 
ADD COLUMN IF NOT EXISTS eighth_prize VARCHAR(10);

-- Verify
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'lottery_draws' AND column_name = 'eighth_prize';
