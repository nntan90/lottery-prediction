-- =====================================================
-- LOTTERY PREDICTION SYSTEM - DATABASE SCHEMA
-- =====================================================
-- Tạo 6 tables cho hệ thống dự đoán xổ số
-- Copy toàn bộ file này vào Supabase SQL Editor và Run
-- =====================================================

-- Table 1: Lưu kết quả quay số
CREATE TABLE IF NOT EXISTS lottery_draws (
  id SERIAL PRIMARY KEY,
  draw_date DATE NOT NULL,
  region VARCHAR(10) NOT NULL, -- 'XSMB' hoặc 'XSMN'
  
  -- Các giải thưởng
  special_prize VARCHAR(20),
  first_prize VARCHAR(20),
  second_prize TEXT[],
  third_prize TEXT[],
  fourth_prize TEXT[],
  fifth_prize TEXT[],
  sixth_prize TEXT[],
  seventh_prize TEXT[],
  eighth_prize VARCHAR(20),
  
  created_at TIMESTAMP DEFAULT NOW(),
  
  -- Đảm bảo không trùng lặp
  UNIQUE(draw_date, region)
);

-- Table 2: Lưu dự đoán
CREATE TABLE IF NOT EXISTS predictions (
  id SERIAL PRIMARY KEY,
  prediction_date DATE NOT NULL,
  region VARCHAR(10) NOT NULL,
  model_version VARCHAR(20),
  
  -- Lưu dự đoán dạng JSON
  -- Ví dụ: {"special": "12345", "first": "67890"}
  predicted_numbers JSONB,
  
  confidence_score FLOAT,
  created_at TIMESTAMP DEFAULT NOW()
);

-- Table 3: Đánh giá độ chính xác
CREATE TABLE IF NOT EXISTS evaluation_metrics (
  id SERIAL PRIMARY KEY,
  evaluation_date DATE NOT NULL,
  region VARCHAR(10),
  
  total_predictions INT,
  correct_predictions INT,
  accuracy_rate FLOAT,
  
  model_version VARCHAR(20),
  created_at TIMESTAMP DEFAULT NOW()
);

-- Table 4: Quản lý người dùng Telegram
CREATE TABLE IF NOT EXISTS telegram_subscribers (
  chat_id BIGINT PRIMARY KEY,
  username VARCHAR(100),
  
  -- Người dùng muốn nhận thông báo cho miền nào
  subscribed_regions VARCHAR(20)[], -- ['XSMB', 'XSMN']
  
  is_active BOOLEAN DEFAULT true,
  subscribed_at TIMESTAMP DEFAULT NOW()
);

-- Table 5: Theo dõi crawler
CREATE TABLE IF NOT EXISTS crawler_logs (
  id SERIAL PRIMARY KEY,
  crawl_date DATE,
  region VARCHAR(10),
  
  status VARCHAR(20), -- 'success', 'failed', 'partial'
  error_message TEXT,
  records_inserted INT,
  
  created_at TIMESTAMP DEFAULT NOW()
);

-- Table 6: Quản lý phiên bản mô hình
CREATE TABLE IF NOT EXISTS model_metadata (
  version VARCHAR(20) PRIMARY KEY,
  model_type VARCHAR(50), -- 'prophet', 'frequency_analysis'
  training_date DATE,
  accuracy_baseline FLOAT,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMP DEFAULT NOW()
);

-- =====================================================
-- ROW LEVEL SECURITY (RLS)
-- =====================================================
-- Cho phép public đọc, chỉ service_role mới ghi được

-- Enable RLS cho tất cả tables
ALTER TABLE lottery_draws ENABLE ROW LEVEL SECURITY;
ALTER TABLE predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE telegram_subscribers ENABLE ROW LEVEL SECURITY;
ALTER TABLE crawler_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_metadata ENABLE ROW LEVEL SECURITY;

-- Policy: Cho phép mọi người đọc
CREATE POLICY "Public read access" ON lottery_draws FOR SELECT USING (true);
CREATE POLICY "Public read access" ON predictions FOR SELECT USING (true);
CREATE POLICY "Public read access" ON evaluation_metrics FOR SELECT USING (true);
CREATE POLICY "Public read access" ON model_metadata FOR SELECT USING (true);

-- Policy: Chỉ service_role mới ghi được
CREATE POLICY "Service write access" ON lottery_draws FOR INSERT 
  WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON predictions FOR INSERT 
  WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON evaluation_metrics FOR INSERT 
  WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON crawler_logs FOR INSERT 
  WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON model_metadata FOR INSERT 
  WITH CHECK (auth.role() = 'service_role');

-- =====================================================
-- INDEXES (Tối ưu query performance)
-- =====================================================

CREATE INDEX idx_lottery_draws_date ON lottery_draws(draw_date DESC);
CREATE INDEX idx_lottery_draws_region ON lottery_draws(region);
CREATE INDEX idx_predictions_date ON predictions(prediction_date DESC);
CREATE INDEX idx_crawler_logs_date ON crawler_logs(crawl_date DESC);

-- =====================================================
-- DONE! 
-- =====================================================
-- Sau khi chạy xong, check Table Editor để verify 6 tables
