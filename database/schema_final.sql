-- =====================================================
-- LOTTERY PREDICTION SYSTEM V3 - FINAL DATABASE SCHEMA
-- =====================================================
-- Full schema definition for V3 system.
-- Includes: lottery_draws, crawler_logs (Base)
-- Includes: tails_2d, pair_features, model_registry, prediction_results, training_queue (V3)
-- Removed: predictions, model_training_logs (V2)
-- Generated: 2026-02-20
-- =====================================================

-- =====================================================
-- 1. TABLE: lottery_draws
-- Lưu trữ kết quả xổ số kiến thiết hằng ngày (Raw Data)
-- =====================================================
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

COMMENT ON TABLE lottery_draws IS 'Lưu trữ kết quả xổ số kiến thiết hằng ngày (Raw Data)';
CREATE INDEX IF NOT EXISTS idx_lottery_draws_date ON lottery_draws(draw_date DESC);
CREATE INDEX IF NOT EXISTS idx_lottery_draws_region ON lottery_draws(region);
CREATE INDEX IF NOT EXISTS idx_lottery_draws_province ON lottery_draws(province);


-- =====================================================
-- 2. TABLE: crawler_logs
-- Theo dõi hoạt động crawler
-- =====================================================
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


-- =====================================================
-- 3. TABLE: tails_2d (V3)
-- 2 số cuối của mọi giải, theo từng kỳ quay
-- =====================================================
CREATE TABLE IF NOT EXISTS tails_2d (
  id          SERIAL PRIMARY KEY,
  draw_id     INT NOT NULL REFERENCES lottery_draws(id) ON DELETE CASCADE,
  draw_date   DATE NOT NULL,
  region      VARCHAR(10) NOT NULL,   -- 'XSMB' | 'XSMN'
  province    VARCHAR(50),            -- NULL cho XSMB, slug cho XSMN
  prize_code  VARCHAR(20) NOT NULL,   -- 'DB' | '1' | '2' | ... | '8'
  tail_2d     SMALLINT NOT NULL       -- 0–99
);

COMMENT ON TABLE tails_2d IS '2 số cuối của mọi giải trong từng kỳ quay';
CREATE INDEX IF NOT EXISTS idx_tails_draw_id   ON tails_2d(draw_id);
CREATE INDEX IF NOT EXISTS idx_tails_date      ON tails_2d(draw_date DESC);
CREATE INDEX IF NOT EXISTS idx_tails_region    ON tails_2d(region, province);
CREATE INDEX IF NOT EXISTS idx_tails_pair      ON tails_2d(tail_2d);


-- =====================================================
-- 4. TABLE: pair_features (V3)
-- Feature vector cho mỗi cặp 00–99, mỗi ngày, mỗi đài
-- =====================================================
CREATE TABLE IF NOT EXISTS pair_features (
  id            SERIAL PRIMARY KEY,
  feature_date  DATE NOT NULL,        -- ngày tính feature (= ngày cần dự đoán)
  region        VARCHAR(10) NOT NULL,
  province      VARCHAR(50),
  pair          SMALLINT NOT NULL,    -- 0–99

  -- Frequency features
  freq_30       FLOAT,
  freq_60       FLOAT,
  freq_100      FLOAT,

  -- Gap features
  gap_since_last  INT,    -- số kỳ kể từ lần xuất hiện gần nhất
  avg_gap_100     FLOAT,  -- trung bình khoảng cách 100 kỳ
  std_gap_100     FLOAT,  -- độ lệch chuẩn khoảng cách
  gap_zscore      FLOAT,  -- (gap_since_last - avg_gap) / std_gap

  -- Pair characteristics
  is_even       BOOLEAN,  -- pair chẵn?
  is_high       BOOLEAN,  -- pair >= 50?
  sum_digits    SMALLINT, -- (pair//10) + (pair%10)

  -- Context
  day_of_week   SMALLINT, -- 0=Mon ... 6=Sun

  -- Label (for training)
  hit           BOOLEAN,  -- 1 nếu pair xuất hiện trong TAIL_SET ngày đó

  CONSTRAINT pair_features_unique UNIQUE (feature_date, region, province, pair)
);

COMMENT ON TABLE pair_features IS 'Feature vector cho ML: 100 cặp (00–99) × ngày × đài';
CREATE INDEX IF NOT EXISTS idx_pf_date     ON pair_features(feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_pf_region   ON pair_features(region, province);
CREATE INDEX IF NOT EXISTS idx_pf_pair     ON pair_features(pair);
CREATE INDEX IF NOT EXISTS idx_pf_hit      ON pair_features(hit);


-- =====================================================
-- 5. TABLE: model_registry (V3)
-- Quản lý các version model XGBoost theo từng đài
-- =====================================================
CREATE TABLE IF NOT EXISTS model_registry (
  id              SERIAL PRIMARY KEY,
  region          VARCHAR(10) NOT NULL,
  province        VARCHAR(50),          -- NULL = all (cho XSMB)
  version         VARCHAR(50) NOT NULL, -- e.g. 'v3_20260219'
  status          VARCHAR(20) NOT NULL DEFAULT 'active',
                                        -- 'active' | 'deprecated'
  file_path       TEXT NOT NULL,        -- Supabase Storage path: models/XSMB/all_v3_20260219.pkl
  train_start_date DATE,
  train_end_date   DATE,
  train_draws      INT,                 -- số kỳ dùng để train
  metric_auc       FLOAT,
  metric_hit_rate  FLOAT,               -- hit_rate_top3 trên tập validation
  trained_at       TIMESTAMP DEFAULT NOW(),
  created_at       TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE model_registry IS 'Quản lý model XGBoost V3 theo đài';
CREATE INDEX IF NOT EXISTS idx_registry_region  ON model_registry(region, province);
CREATE INDEX IF NOT EXISTS idx_registry_status  ON model_registry(status);


-- =====================================================
-- 6. TABLE: prediction_results (V3)
-- Kết quả dự đoán hàng ngày (3 cặp) + verify
-- =====================================================
CREATE TABLE IF NOT EXISTS prediction_results (
  id              SERIAL PRIMARY KEY,
  prediction_date DATE NOT NULL,
  region          VARCHAR(10) NOT NULL,
  province        VARCHAR(50),

  -- 3 cặp số dự đoán (00–99)
  pair_1          SMALLINT NOT NULL,
  pair_2          SMALLINT NOT NULL,
  pair_3          SMALLINT NOT NULL,

  -- Xác suất tương ứng từ model
  prob_1          FLOAT,
  prob_2          FLOAT,
  prob_3          FLOAT,

  model_version   VARCHAR(50),

  -- Kết quả verify (điền sau khi có KQXS)
  hit             BOOLEAN,             -- TRUE nếu ít nhất 1 cặp trúng
  matched_pairs   SMALLINT[],          -- danh sách cặp thực sự trúng
  tail_set        SMALLINT[],          -- toàn bộ TAIL_SET ngày đó (để debug)
  verified_at     TIMESTAMP,

  created_at      TIMESTAMP DEFAULT NOW(),

  CONSTRAINT prediction_results_unique UNIQUE (prediction_date, region, province)
);

COMMENT ON TABLE prediction_results IS 'Kết quả dự đoán V3: 3 cặp 2-số-cuối per ngày per đài';
CREATE INDEX IF NOT EXISTS idx_pr_date    ON prediction_results(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_pr_region  ON prediction_results(region, province);
CREATE INDEX IF NOT EXISTS idx_pr_hit     ON prediction_results(hit);


-- =====================================================
-- 7. TABLE: training_queue (V3)
-- Hàng đợi yêu cầu train lại model
-- =====================================================
CREATE TABLE IF NOT EXISTS training_queue (
  id              SERIAL PRIMARY KEY,
  region          VARCHAR(10) NOT NULL,
  province        VARCHAR(50),
  trigger_reason  VARCHAR(50) NOT NULL,  -- 'new_data' | 'perf_drop' | 'manual'

  -- Số liệu tại thời điểm check
  new_draws       INT,
  train_draws     INT,
  hit_rate_train  FLOAT,
  hit_rate_recent FLOAT,

  status          VARCHAR(20) NOT NULL DEFAULT 'pending',
                                         -- 'pending' | 'triggered' | 'done' | 'skipped'

  -- GitHub Actions run info (điền sau khi trigger)
  gh_run_id       TEXT,
  notified_at     TIMESTAMP,
  completed_at    TIMESTAMP,
  created_at      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE training_queue IS 'Hàng đợi đề xuất/trigger train lại model';
CREATE INDEX IF NOT EXISTS idx_tq_status   ON training_queue(status);
CREATE INDEX IF NOT EXISTS idx_tq_region   ON training_queue(region, province);
CREATE INDEX IF NOT EXISTS idx_tq_created  ON training_queue(created_at DESC);


-- =====================================================
-- 8. TABLE: model_predictions (V3.1 — XSMN Ensemble)
-- Log Top-N output từ mỗi sub-model trong ensemble pipeline
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
  execution_time_ms INT,
  error_message   TEXT,
  status          VARCHAR(20) DEFAULT 'success',

  created_at      TIMESTAMP DEFAULT NOW(),

  CONSTRAINT model_predictions_unique UNIQUE (prediction_date, region, province, model_name)
);

COMMENT ON TABLE model_predictions IS 'Log Top-5 output từ mỗi sub-model trong XSMN ensemble pipeline';
CREATE INDEX IF NOT EXISTS idx_mp_date    ON model_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_mp_region  ON model_predictions(region, province);
CREATE INDEX IF NOT EXISTS idx_mp_model   ON model_predictions(model_name);


-- =====================================================
-- ROW LEVEL SECURITY (RLS)
-- =====================================================

-- Enable RLS
ALTER TABLE lottery_draws ENABLE ROW LEVEL SECURITY;
ALTER TABLE crawler_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE tails_2d ENABLE ROW LEVEL SECURITY;
ALTER TABLE pair_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE prediction_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE training_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_predictions ENABLE ROW LEVEL SECURITY;

-- Policies: Public Read
CREATE POLICY "Public read access" ON lottery_draws FOR SELECT USING (true);
CREATE POLICY "Public read access" ON crawler_logs FOR SELECT USING (true);
CREATE POLICY "Public read access" ON tails_2d FOR SELECT USING (true);
CREATE POLICY "Public read access" ON pair_features FOR SELECT USING (true);
CREATE POLICY "Public read access" ON model_registry FOR SELECT USING (true);
CREATE POLICY "Public read access" ON prediction_results FOR SELECT USING (true);
CREATE POLICY "Public read access" ON training_queue FOR SELECT USING (true);
CREATE POLICY "Public read access" ON model_predictions FOR SELECT USING (true);

-- Policies: Service Write Only
CREATE POLICY "Service write access" ON lottery_draws FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON crawler_logs FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON tails_2d FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON pair_features FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON pair_features FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON model_registry FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON model_registry FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON prediction_results FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON prediction_results FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON training_queue FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON training_queue FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON model_predictions FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON model_predictions FOR UPDATE USING (auth.role() = 'service_role');

