-- =====================================================
-- LOTTERY PREDICTION SYSTEM V3 — NEW TABLES
-- =====================================================
-- Run this AFTER schema_final.sql (which keeps lottery_draws, crawler_logs)
-- Generated: 2026-02-19
-- =====================================================

-- =====================================================
-- DROP OLD V2 TABLES
-- =====================================================
DROP TABLE IF EXISTS predictions CASCADE;
DROP TABLE IF EXISTS model_training_logs CASCADE;


-- =====================================================
-- 1. tails_2d
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
COMMENT ON COLUMN tails_2d.prize_code IS 'Mã giải: DB=đặc biệt, 1=nhất, 2=nhì, ..., 8=tám';
COMMENT ON COLUMN tails_2d.tail_2d IS '2 số cuối (0–99) của giải đó';

CREATE INDEX IF NOT EXISTS idx_tails_draw_id   ON tails_2d(draw_id);
CREATE INDEX IF NOT EXISTS idx_tails_date      ON tails_2d(draw_date DESC);
CREATE INDEX IF NOT EXISTS idx_tails_region    ON tails_2d(region, province);
CREATE INDEX IF NOT EXISTS idx_tails_pair      ON tails_2d(tail_2d);

ALTER TABLE tails_2d ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON tails_2d FOR SELECT USING (true);
CREATE POLICY "Service write access" ON tails_2d FOR INSERT WITH CHECK (auth.role() = 'service_role');


-- =====================================================
-- 2. pair_features
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
COMMENT ON COLUMN pair_features.hit IS '1 nếu cặp xuất hiện trong bất kỳ giải nào của ngày đó';

CREATE INDEX IF NOT EXISTS idx_pf_date     ON pair_features(feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_pf_region   ON pair_features(region, province);
CREATE INDEX IF NOT EXISTS idx_pf_pair     ON pair_features(pair);
CREATE INDEX IF NOT EXISTS idx_pf_hit      ON pair_features(hit);

ALTER TABLE pair_features ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON pair_features FOR SELECT USING (true);
CREATE POLICY "Service write access" ON pair_features FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON pair_features FOR UPDATE USING (auth.role() = 'service_role');


-- =====================================================
-- 3. model_registry
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
COMMENT ON COLUMN model_registry.status IS 'active = đang dùng, deprecated = đã thay thế';
COMMENT ON COLUMN model_registry.metric_hit_rate IS 'Hit-rate khi chọn top-3 pairs trên tập val';

CREATE INDEX IF NOT EXISTS idx_registry_region  ON model_registry(region, province);
CREATE INDEX IF NOT EXISTS idx_registry_status  ON model_registry(status);

ALTER TABLE model_registry ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON model_registry FOR SELECT USING (true);
CREATE POLICY "Service write access" ON model_registry FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON model_registry FOR UPDATE USING (auth.role() = 'service_role');


-- =====================================================
-- 4. prediction_results
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
COMMENT ON COLUMN prediction_results.hit IS 'TRUE nếu ≥1 cặp trong top-3 xuất hiện trong TAIL_SET';
COMMENT ON COLUMN prediction_results.matched_pairs IS 'Cặp số nào thực sự trúng';
COMMENT ON COLUMN prediction_results.tail_set IS 'Tất cả 2 số cuối mọi giải của ngày đó';

CREATE INDEX IF NOT EXISTS idx_pr_date    ON prediction_results(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_pr_region  ON prediction_results(region, province);
CREATE INDEX IF NOT EXISTS idx_pr_hit     ON prediction_results(hit);

ALTER TABLE prediction_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON prediction_results FOR SELECT USING (true);
CREATE POLICY "Service write access" ON prediction_results FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON prediction_results FOR UPDATE USING (auth.role() = 'service_role');


-- =====================================================
-- 5. training_queue
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
COMMENT ON COLUMN training_queue.trigger_reason IS 'new_data: đủ data mới | perf_drop: hiệu năng giảm | manual: thủ công';
COMMENT ON COLUMN training_queue.status IS 'pending → triggered → done | skipped';

CREATE INDEX IF NOT EXISTS idx_tq_status   ON training_queue(status);
CREATE INDEX IF NOT EXISTS idx_tq_region   ON training_queue(region, province);
CREATE INDEX IF NOT EXISTS idx_tq_created  ON training_queue(created_at DESC);

ALTER TABLE training_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON training_queue FOR SELECT USING (true);
CREATE POLICY "Service write access" ON training_queue FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON training_queue FOR UPDATE USING (auth.role() = 'service_role');

-- =====================================================
-- DONE — V3 Schema
-- =====================================================
