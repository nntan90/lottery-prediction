-- Migration: 01_create_profit_tracking.sql
-- Create profit_tracking table to store daily calculated profits
-- to avoid recalculating from scratch and separate financial logic from raw prediction data.

DROP TABLE IF EXISTS public.profit_tracking;

CREATE TABLE IF NOT EXISTS public.profit_tracking (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    prediction_date DATE NOT NULL,
    region TEXT NOT NULL CHECK (region IN ('xsmn', 'xsmb')),
    province TEXT,
    pair INTEGER NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL,
    revenue INTEGER NOT NULL,
    profit INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    
    -- Ensure we don't insert duplicate profit records for the same station, same pair on the same day
    UNIQUE(prediction_date, region, province, pair)
);

-- Note: We allow province to be NULL or empty string (standardized to empty string or 'all' in code later). 

-- Update RLS policies if necessary
ALTER TABLE public.profit_tracking ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Enable read access for all users" ON public.profit_tracking FOR SELECT USING (true);
CREATE POLICY "Enable insert for service role only" ON public.profit_tracking FOR INSERT WITH CHECK (true);
CREATE POLICY "Enable update for service role only" ON public.profit_tracking FOR UPDATE USING (true);
