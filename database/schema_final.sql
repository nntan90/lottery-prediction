-- =====================================================
-- LOTTERY PREDICTION SYSTEM - FINAL DATABASE SCHEMA
-- =====================================================
-- Full schema definition including all tables, indexes,
-- RLS policies, and comments.
-- Generated: 2026-02-15
-- =====================================================

-- 1. TABLE: lottery_draws
-- Lưu trữ kết quả xổ số kiến thiết hằng ngày (Raw Data)
CREATE TABLE IF NOT EXISTS lottery_draws (
  id SERIAL PRIMARY KEY,
  draw_date DATE NOT NULL,
  region VARCHAR(10) NOT NULL, -- 'XSMB' hoặc 'XSMN'
  province VARCHAR(50),        -- Tên tỉnh (slug) cho XSMN, NULL cho XSMB
  
  -- Các giải thưởng
  special_prize VARCHAR(20),
  first_prize VARCHAR(20),
  second_prize TEXT[],
  third_prize TEXT[],
  fourth_prize TEXT[],
  fifth_prize TEXT[],
  sixth_prize TEXT[],
  seventh_prize TEXT[],
  eighth_prize VARCHAR(20), -- Chỉ có ở XSMN
  
  created_at TIMESTAMP DEFAULT NOW(),
  
  -- Constraint: Unique per date + region + province
  CONSTRAINT lottery_draws_draw_date_region_province_key UNIQUE(draw_date, region, province)
);

-- Comments
COMMENT ON TABLE lottery_draws IS 'Lưu trữ kết quả xổ số kiến thiết hằng ngày (Raw Data)';
COMMENT ON COLUMN lottery_draws.draw_date IS 'Ngày quay thưởng';
COMMENT ON COLUMN lottery_draws.region IS 'Miền: XSMB (Miền Bắc) hoặc XSMN (Miền Nam)';
COMMENT ON COLUMN lottery_draws.province IS 'Tên tỉnh (slug) cho XSMN, NULL cho XSMB';
COMMENT ON COLUMN lottery_draws.special_prize IS 'Giải Đặc Biệt';
COMMENT ON COLUMN lottery_draws.first_prize IS 'Giải Nhất';
COMMENT ON COLUMN lottery_draws.second_prize IS 'Giải Nhì (Mảng text[])';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_lottery_draws_date ON lottery_draws(draw_date DESC);
CREATE INDEX IF NOT EXISTS idx_lottery_draws_region ON lottery_draws(region);
CREATE INDEX IF NOT EXISTS idx_lottery_draws_province ON lottery_draws(province);


-- 2. TABLE: predictions
-- Lưu trữ dự đoán hằng ngày và kết quả kiểm tra (Closed-Loop)
CREATE TABLE IF NOT EXISTS predictions (
  id SERIAL PRIMARY KEY,
  prediction_date DATE NOT NULL,
  region VARCHAR(10) NOT NULL,
  province VARCHAR(50) NOT NULL DEFAULT '', -- Empty string for XSMB to ensure unique constraint
  model_version VARCHAR(50),
  
  -- Lưu dự đoán dạng JSON
  -- Ví dụ: {"predicted_number": "12", "hot_numbers": [12, 34, 56]}
  predicted_numbers JSONB,
  
  confidence_score FLOAT,
  
  -- V2 Fields (Verification)
  is_correct BOOLEAN DEFAULT NULL,
  win_prize JSONB DEFAULT NULL, -- Format: {"prize": "Giai 8", "amount": 100000}
  check_time TIMESTAMP DEFAULT NULL,
  
  created_at TIMESTAMP DEFAULT NOW(),
  
  -- Constraint: Unique prediction per context
  CONSTRAINT predictions_unique_key UNIQUE (prediction_date, region, province, model_version)
);

-- Comments
COMMENT ON TABLE predictions IS 'Lưu trữ dự đoán hằng ngày và kết quả kiểm tra (Closed-Loop)';
COMMENT ON COLUMN predictions.prediction_date IS 'Ngày dự đoán (cho ngày hôm sau)';
COMMENT ON COLUMN predictions.region IS 'Miền dự đoán (XSMB/XSMN)';
COMMENT ON COLUMN predictions.province IS 'Tỉnh dự đoán (XSMN), NULL hoặc empty cho XSMB';
COMMENT ON COLUMN predictions.model_version IS 'Phiên bản model dùng để dự đoán (ví dụ: lstm_v2)';
COMMENT ON COLUMN predictions.predicted_numbers IS 'JSON chứa số dự đoán và số nóng (hot_numbers)';
COMMENT ON COLUMN predictions.confidence_score IS 'Độ tin cậy của dự đoán (0.0 - 1.0)';
COMMENT ON COLUMN predictions.is_correct IS 'Kết quả kiểm tra: TRUE nếu trúng, FALSE nếu trượt (V2)';
COMMENT ON COLUMN predictions.win_prize IS 'Chi tiết giải thưởng nếu trúng (JSON) (V2)';
COMMENT ON COLUMN predictions.check_time IS 'Thời điểm chạy script kiểm tra (verify_predictions.py) (V2)';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_province ON predictions(province);
CREATE INDEX IF NOT EXISTS idx_predictions_is_correct ON predictions(is_correct);


-- 3. TABLE: model_training_logs (V2)
-- Lịch sử huấn luyện model LSTM
CREATE TABLE IF NOT EXISTS model_training_logs (
    id SERIAL PRIMARY KEY,
    training_date TIMESTAMP DEFAULT NOW(),
    
    region VARCHAR(10) NOT NULL, -- 'XSMB' or 'XSMN'
    province VARCHAR(50),        -- NULL for XSMB
    
    model_version VARCHAR(50),   -- e.g., 'lstm_v2_20240215'
    
    data_range_start DATE,
    data_range_end DATE,
    
    training_params JSONB,       -- {"epochs": 50, "batch_size": 32}
    metrics JSONB,               -- {"accuracy": 0.85, "loss": 0.05}
    
    model_path TEXT,             -- Supabase Storage path
    
    trigger_reason VARCHAR(100)  -- 'scheduled', 'performance_drop', 'manual'
);

-- Comments
COMMENT ON TABLE model_training_logs IS 'Lịch sử huấn luyện model LSTM (Training History)';
COMMENT ON COLUMN model_training_logs.training_date IS 'Thời điểm bắt đầu huấn luyện';
COMMENT ON COLUMN model_training_logs.model_path IS 'Đường dẫn file .h5 trong Supabase Storage';
COMMENT ON COLUMN model_training_logs.trigger_reason IS 'Lý do train: scheduled (định kỳ), performance_drop (suy giảm), manual (thủ công)';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_training_logs_date ON model_training_logs(training_date DESC);



-- 4. TABLE: telegram_subscribers (REMOVED)
-- Quản lý người dùng Telegram - Đã xóa vì không sử dụng (2026-02-16)
-- CREATE TABLE IF NOT EXISTS telegram_subscribers ...


-- 5. TABLE: crawler_logs
-- Theo dõi hoạt động crawler
CREATE TABLE IF NOT EXISTS crawler_logs (
  id SERIAL PRIMARY KEY,
  crawl_date DATE,
  region VARCHAR(10),
  status VARCHAR(20),     -- 'success', 'failed', 'partial'
  error_message TEXT,
  records_inserted INT,
  created_at TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE crawler_logs IS 'Nhật ký hoạt động của Crawler';
CREATE INDEX IF NOT EXISTS idx_crawler_logs_date ON crawler_logs(crawl_date DESC);



-- 6. TABLE: evaluation_metrics (REMOVED)
-- Bảng đánh giá hiệu quả dự đoán (Legacy/V1) - Đã xóa vì không sử dụng (2026-02-16)

-- 7. TABLE: model_metadata (REMOVED)
-- Metadata cho các model cũ (Prophet, Frequency) - Đã xóa vì không sử dụng (2026-02-16)


-- =====================================================
-- ROW LEVEL SECURITY (RLS)
-- =====================================================

-- Enable RLS
ALTER TABLE lottery_draws ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_metrics ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE telegram_subscribers ENABLE ROW LEVEL SECURITY; -- Removed
ALTER TABLE crawler_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_metadata ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_training_logs ENABLE ROW LEVEL SECURITY;

-- Policies: Public Read
CREATE POLICY "Public read access" ON lottery_draws FOR SELECT USING (true);
CREATE POLICY "Public read access" ON predictions FOR SELECT USING (true);
-- CREATE POLICY "Public read access" ON evaluation_metrics FOR SELECT USING (true); -- Removed
-- CREATE POLICY "Public read access" ON model_metadata FOR SELECT USING (true); -- Removed
CREATE POLICY "Public read access" ON model_training_logs FOR SELECT USING (true);

-- Policies: Service Write Only
CREATE POLICY "Service write access" ON lottery_draws FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON predictions FOR INSERT WITH CHECK (auth.role() = 'service_role');
-- CREATE POLICY "Service write access" ON evaluation_metrics FOR INSERT WITH CHECK (auth.role() = 'service_role'); -- Removed
CREATE POLICY "Service write access" ON crawler_logs FOR INSERT WITH CHECK (auth.role() = 'service_role');
-- CREATE POLICY "Service write access" ON model_metadata FOR INSERT WITH CHECK (auth.role() = 'service_role'); -- Removed
CREATE POLICY "Service write access" ON model_training_logs FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- DONE
-- =====================================================
