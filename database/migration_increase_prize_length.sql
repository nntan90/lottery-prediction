-- Migration: Increase prize column lengths
-- XSMN prizes can be long when concatenated (e.g., 4th prize has 7 results)
-- Changing from VARCHAR(20)[] to TEXT[] to be safe

ALTER TABLE lottery_draws 
ALTER COLUMN second_prize TYPE TEXT[],
ALTER COLUMN third_prize TYPE TEXT[],
ALTER COLUMN fourth_prize TYPE TEXT[],
ALTER COLUMN fifth_prize TYPE TEXT[],
ALTER COLUMN sixth_prize TYPE TEXT[],
ALTER COLUMN seventh_prize TYPE TEXT[];

-- Also increase single value columns just in case
ALTER TABLE lottery_draws
ALTER COLUMN special_prize TYPE VARCHAR(20),
ALTER COLUMN first_prize TYPE VARCHAR(20),
ALTER COLUMN eighth_prize TYPE VARCHAR(20);
