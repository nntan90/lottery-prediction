-- Migration: 01_create_profit_tracking.sql
-- Create profit_tracking table to store daily calculated profits
-- to avoid recalculating from scratch and separate financial logic from raw prediction data.

CREATE TABLE IF NOT EXISTS public.profit_tracking (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    prediction_date DATE NOT NULL,
    region TEXT NOT NULL CHECK (region IN ('xsmn', 'xsmb')),
    province TEXT,
    total_cost INTEGER NOT NULL,
    total_revenue INTEGER NOT NULL,
    profit INTEGER NOT NULL,
    details JSONB, -- e.g. {"10": 2, "35": 1}
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    
    -- Ensure we don't insert duplicate profit records for the same station on the same day
    UNIQUE(prediction_date, region, province)
);

-- Note: We allow province to be NULL or empty string (standardized to empty string or 'all' in code later, but typical schema has NULL). 
-- actually the unique constraint considers NULLs as distinct in postgres prior to 15 without NULLS NOT DISTINCT, 
-- but in our code we usually use province = 'all' or empty string for xsmb. We'll handle that in Python.

-- Update RLS policies if necessary
ALTER TABLE public.profit_tracking ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Enable read access for all users" ON public.profit_tracking FOR SELECT USING (true);
CREATE POLICY "Enable insert for service role only" ON public.profit_tracking FOR INSERT WITH CHECK (true);
CREATE POLICY "Enable update for service role only" ON public.profit_tracking FOR UPDATE USING (true);
