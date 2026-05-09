-- =====================================================
-- Migration 06: XSMN Multi-Model Ensemble Schema
-- Version: v3.1-rev2
-- Scope: Additive only — XSMB không bị ảnh hưởng
-- Run in Supabase SQL Editor (Dashboard > SQL Editor)
-- =====================================================

-- =====================================================
-- 1. ALTER model_registry: thêm cột ensemble metadata
-- =====================================================
ALTER TABLE model_registry
  ADD COLUMN IF NOT EXISTS model_name        VARCHAR(50),   -- 'freq_gap' | 'markov' | 'xgboost_core'
  ADD COLUMN IF NOT EXISTS model_type        VARCHAR(30),   -- 'rule_based' | 'ml' | 'ensemble'
  ADD COLUMN IF NOT EXISTS weight            FLOAT DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS is_ensemble_member BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS top_n_output      INT DEFAULT 3; -- Số cặp output (5 cho sub-model, 3 cho final)

COMMENT ON COLUMN model_registry.model_name        IS 'Tên model con: freq_gap, markov, xgboost_core';
COMMENT ON COLUMN model_registry.model_type        IS 'Loại model: rule_based, ml, ensemble';
COMMENT ON COLUMN model_registry.weight            IS 'Trọng số trong ensemble (0-1)';
COMMENT ON COLUMN model_registry.is_ensemble_member IS 'TRUE nếu model này là thành phần của ensemble';
COMMENT ON COLUMN model_registry.top_n_output      IS 'Số cặp top-N mà model này output (5 cho sub, 3 cho final)';


-- =====================================================
-- 2. ALTER prediction_results: thêm cột ensemble info
-- =====================================================
ALTER TABLE prediction_results
  ADD COLUMN IF NOT EXISTS ensemble_method      VARCHAR(50),   -- 'weighted_borda' | 'single_model'
  ADD COLUMN IF NOT EXISTS contributing_models  TEXT[],         -- ['freq_gap', 'markov', 'xgboost_core']
  ADD COLUMN IF NOT EXISTS final_scores         FLOAT[];       -- [12.5, 10.0, 8.5] Borda scores

COMMENT ON COLUMN prediction_results.ensemble_method      IS 'Phương pháp ensemble: weighted_borda, single_model';
COMMENT ON COLUMN prediction_results.contributing_models  IS 'Danh sách model đã tham gia tạo kết quả';
COMMENT ON COLUMN prediction_results.final_scores         IS 'Điểm Borda cuối cùng tương ứng pair_1, pair_2, pair_3';


-- =====================================================
-- 3. CREATE TABLE model_predictions (MỚI)
-- Log Top-N output của từng sub-model mỗi ngày
-- =====================================================
CREATE TABLE IF NOT EXISTS model_predictions (
  id              SERIAL PRIMARY KEY,
  prediction_date DATE NOT NULL,
  region          VARCHAR(10) NOT NULL,     -- 'XSMN'
  province        VARCHAR(50),              -- slug tỉnh

  model_name      VARCHAR(50) NOT NULL,     -- 'freq_gap' | 'markov' | 'xgboost_core'
  model_type      VARCHAR(30),              -- 'rule_based' | 'ml'

  -- Top 5 pairs + scores
  pair_1          SMALLINT,
  pair_2          SMALLINT,
  pair_3          SMALLINT,
  pair_4          SMALLINT,
  pair_5          SMALLINT,

  score_1         FLOAT,
  score_2         FLOAT,
  score_3         FLOAT,
  score_4         FLOAT,
  score_5         FLOAT,

  -- Metadata
  execution_time_ms INT,                    -- Thời gian chạy (ms)
  error_message   TEXT,                     -- NULL nếu thành công
  status          VARCHAR(20) DEFAULT 'success',  -- 'success' | 'error' | 'skipped'

  created_at      TIMESTAMP DEFAULT NOW(),

  CONSTRAINT model_predictions_unique UNIQUE (prediction_date, region, province, model_name)
);

COMMENT ON TABLE model_predictions IS 'Log Top-5 output từ mỗi sub-model trong XSMN ensemble pipeline';

CREATE INDEX IF NOT EXISTS idx_mp_date    ON model_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_mp_region  ON model_predictions(region, province);
CREATE INDEX IF NOT EXISTS idx_mp_model   ON model_predictions(model_name);


-- =====================================================
-- 4. RLS cho model_predictions
-- =====================================================
ALTER TABLE model_predictions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access" ON model_predictions FOR SELECT USING (true);
CREATE POLICY "Service write access" ON model_predictions FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON model_predictions FOR UPDATE USING (auth.role() = 'service_role');
