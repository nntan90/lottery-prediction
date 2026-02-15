-- =====================================================
-- MIGRATION V2: UPGRADE SCHEMA FOR CLOSED-LOOP SYSTEM
-- =====================================================

-- 1. Upgrade 'predictions' table to store verification results
ALTER TABLE predictions 
ADD COLUMN IF NOT EXISTS is_correct BOOLEAN DEFAULT NULL,
ADD COLUMN IF NOT EXISTS win_prize JSONB DEFAULT NULL, -- Format: {"prize": "Giai 8", "amount": 100000}
ADD COLUMN IF NOT EXISTS check_time TIMESTAMP DEFAULT NULL;

-- 2. Create 'model_training_logs' to track training history
CREATE TABLE IF NOT EXISTS model_training_logs (
    id SERIAL PRIMARY KEY,
    training_date TIMESTAMP DEFAULT NOW(),
    
    region VARCHAR(10) NOT NULL, -- 'XSMB' or 'XSMN'
    province VARCHAR(50),        -- NULL for XSMB
    
    model_version VARCHAR(50),   -- e.g., 'lstm_v2_20240215'
    
    data_range_start DATE,
    data_range_end DATE,
    
    training_params JSONB,       -- {"epochs": 50, "batch_size": 32, "loss": 0.05}
    metrics JSONB,               -- {"accuracy": 0.85, "val_loss": 0.06}
    
    model_path TEXT,             -- Supabase Storage path: 'models/lstm_xsmb_20240215.h5'
    
    trigger_reason VARCHAR(100)  -- 'scheduled', 'performance_drop', 'manual'
);

-- Enable RLS for new table
ALTER TABLE model_training_logs ENABLE ROW LEVEL SECURITY;

-- Allow public read
CREATE POLICY "Public read access" ON model_training_logs FOR SELECT USING (true);

-- Allow service role write
CREATE POLICY "Service write access" ON model_training_logs FOR INSERT 
WITH CHECK (auth.role() = 'service_role');

-- Create index for faster lookup
CREATE INDEX IF NOT EXISTS idx_predictions_is_correct ON predictions(is_correct);
CREATE INDEX IF NOT EXISTS idx_training_logs_date ON model_training_logs(training_date DESC);
