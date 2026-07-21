-- =====================================================
-- LOTTERY PREDICTION SYSTEM V3 - FINAL DATABASE SCHEMA
-- =====================================================
-- Full schema definition for V3 system.
-- Includes: lottery_draws, crawler_logs (Base)
-- Includes: tails_2d, pair_features, model_registry, prediction_results, training_queue (V3)
-- Includes: notification_configs (V3.3)
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

  -- XSMB v4 extra features (Migration 07)
  freq_3                 REAL,
  freq_14                REAL,
  weekday_freq_30        REAL,
  gap_percentile         REAL,
  neighbor_freq_7        REAL,
  last_position_encoded  INT,
  streak_length          INT,
  cross_pair_corr        REAL,

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

  -- Legacy score/probability fields.
  -- Ensemble rows store relative scores here unless probabilities are calibrated.
  prob_1          FLOAT,
  prob_2          FLOAT,
  prob_3          FLOAT,

  model_version   VARCHAR(50),

  -- Ensemble audit metadata (Migration 06)
  ensemble_method      VARCHAR(50),
  contributing_models  TEXT[],
  final_scores         FLOAT[],
  run_metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Kết quả verify (điền sau khi có KQXS)
  hit             BOOLEAN,             -- XSMN/all: TRUE nếu >=2/3; legacy scopes may use any-hit
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

  -- Tracking history (Migration 08)
  hit             BOOLEAN,
  matched_pairs   SMALLINT[],

  created_at      TIMESTAMP DEFAULT NOW(),

  CONSTRAINT model_predictions_unique UNIQUE (prediction_date, region, province, model_name)
);

COMMENT ON TABLE model_predictions IS 'Log Top-5 output từ mỗi sub-model trong XSMN ensemble pipeline';
CREATE INDEX IF NOT EXISTS idx_mp_date    ON model_predictions(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_mp_region  ON model_predictions(region, province);
CREATE INDEX IF NOT EXISTS idx_mp_model   ON model_predictions(model_name);


-- =====================================================
-- 9. TABLE: profit_tracking (V3 — Migration 01)
-- Lưu lợi nhuận tính toán hàng ngày theo từng đài và cặp số
-- =====================================================
CREATE TABLE IF NOT EXISTS profit_tracking (
  id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  prediction_date DATE NOT NULL,
  region          TEXT NOT NULL CHECK (region IN ('xsmn', 'xsmb')),
  province        TEXT,
  pair            INTEGER NOT NULL,
  hit_count       INTEGER NOT NULL DEFAULT 0,
  cost            INTEGER NOT NULL,
  revenue         INTEGER NOT NULL,
  profit          INTEGER NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT NOW(),

  -- Unique per date + region + province + pair
  UNIQUE(prediction_date, region, province, pair)
);

COMMENT ON TABLE profit_tracking IS 'Lưu lợi nhuận tính toán hàng ngày theo từng đài và cặp số';
CREATE INDEX IF NOT EXISTS idx_pt_date    ON profit_tracking(prediction_date DESC);
CREATE INDEX IF NOT EXISTS idx_pt_region  ON profit_tracking(region, province);


-- =====================================================
-- 10. TABLE: agent_actions (V3 — Migration 03)
-- Lịch sử hành động của Master Retrain Agent
-- =====================================================
CREATE TABLE IF NOT EXISTS agent_actions (
  id              BIGSERIAL PRIMARY KEY,
  action_date     DATE NOT NULL,
  region          TEXT NOT NULL,
  province        TEXT,                   -- NULL = XSMB (all)
  weekday         INT,                    -- Weekday model (0=Mon..6=Sun), NULL = non-weekday
  action_type     TEXT NOT NULL,          -- 'retrain_triggered' | 'skipped' | 'no_action'
  reason          TEXT,
  strategy        TEXT,                   -- 'boost_estimators' | 'conservative' | 'full_reset'
  old_metric_auc  FLOAT,
  old_hit_rate    FLOAT,
  old_params      JSONB,
  new_params      JSONB,
  created_at      TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE agent_actions IS 'Lịch sử hành động của Master Retrain Agent';
CREATE INDEX IF NOT EXISTS idx_agent_actions_station ON agent_actions(region, province, action_date DESC);
CREATE INDEX IF NOT EXISTS idx_agent_actions_date    ON agent_actions(action_date DESC);


-- =====================================================
-- 11. TABLE: notification_configs (V3.3 — Migration 07)
-- Config bật/tắt và override Telegram notification theo cron/job/script
-- =====================================================
CREATE TABLE IF NOT EXISTS notification_configs (
  id                  SERIAL PRIMARY KEY,
  config_key          VARCHAR(80) UNIQUE NOT NULL,
  channel             VARCHAR(30) NOT NULL DEFAULT 'telegram',
  enabled             BOOLEAN NOT NULL DEFAULT TRUE,

  workflow_file       VARCHAR(120),
  job_name            VARCHAR(120),
  schedule_cron_utc   VARCHAR(80),
  schedule_timezone   VARCHAR(64) DEFAULT 'Asia/Ho_Chi_Minh',
  schedule_local_time VARCHAR(40),

  chat_id             TEXT,
  parse_mode          VARCHAR(20) DEFAULT 'HTML',
  message_prefix      TEXT DEFAULT '',
  message_suffix      TEXT DEFAULT '',

  notes               TEXT,
  metadata            JSONB DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ DEFAULT NOW(),
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE notification_configs IS 'Config bật/tắt và override Telegram notification theo từng cron/job/script';
CREATE INDEX IF NOT EXISTS idx_notification_configs_enabled ON notification_configs(enabled);
CREATE INDEX IF NOT EXISTS idx_notification_configs_workflow ON notification_configs(workflow_file);

INSERT INTO notification_configs (
  config_key, workflow_file, job_name, schedule_cron_utc, schedule_local_time, notes
) VALUES
  ('crawl_xsmb', '01-daily-crawl.yml', 'crawl-xsmb', '0 13 * * *', '20:00 VN', 'XSMB crawl success/no-data/error messages'),
  ('crawl_xsmn', '01-daily-crawl.yml', 'crawl-xsmn', '0 13 * * *', '20:00 VN', 'XSMN crawl success/no-data messages'),
  ('predict_ensemble', '02-predict-ensemble.yml', 'predict-ensemble', '0 0 * * *', '07:00 VN', 'Generic predict ensemble fallback'),
  ('predict_ensemble_xsmb', '02-predict-ensemble.yml', 'predict-ensemble', '0 0 * * *', '07:00 VN', 'XSMB Top 3 statistical-signal message'),
  ('predict_ensemble_xsmn', '02-predict-ensemble.yml', 'predict-ensemble', '0 0 * * *', '07:00 VN', 'XSMN Top 3 statistical-signal message'),
  ('predict_ensemble_scoring_log', '02-predict-ensemble.yml', 'predict-ensemble', '0 0 * * *', '07:00 VN', 'Detailed ensemble scoring log'),
  ('verify_summary', '03-verify-predictions.yml', 'verify-predictions', '30 13 * * *', '20:30 VN', 'Daily hit/miss verification summary'),
  ('master_retrain_agent', '03-verify-predictions.yml', 'verify-predictions', '30 13 * * *', '20:30 VN', 'Agent retrain/skip decision report'),
  ('check_training', '04-check-training.yml', 'check-training', '0 15 * * *', '22:00 VN', 'Scheduled retrain trigger summary'),
  ('train_model', '05-train-model.yml', 'train-model', NULL, 'manual', 'Model training success/error messages'),
  ('profit_report', '06-query-report.yml', 'query-report', NULL, 'manual', 'Manual profit report'),
  ('health_digest', '08-health-digest.yml', 'health-digest', '0 16 * * *', '23:00 VN', 'Daily pipeline health digest'),
  ('weekly_report', '10-weekly-report.yml', 'weekly-report', '0 23 * * 0', '06:00 VN Monday', 'Weekly XML/Telegram system report'),
  ('retrain_weekday_models', NULL, 'retrain-weekday-models', NULL, 'manual', 'Manual weekday-model retrain orchestration'),
  ('system_error_alert', NULL, 'system-error-alert', NULL, 'on error', 'Fallback error alert key for standalone notifier usage')
ON CONFLICT (config_key) DO NOTHING;


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
ALTER TABLE profit_tracking ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_configs ENABLE ROW LEVEL SECURITY;

-- Policies: Public Read
CREATE POLICY "Public read access" ON lottery_draws FOR SELECT USING (true);
CREATE POLICY "Public read access" ON crawler_logs FOR SELECT USING (true);
CREATE POLICY "Public read access" ON tails_2d FOR SELECT USING (true);
CREATE POLICY "Public read access" ON pair_features FOR SELECT USING (true);
CREATE POLICY "Public read access" ON model_registry FOR SELECT USING (true);
CREATE POLICY "Public read access" ON prediction_results FOR SELECT USING (true);
CREATE POLICY "Public read access" ON training_queue FOR SELECT USING (true);
CREATE POLICY "Public read access" ON model_predictions FOR SELECT USING (true);
CREATE POLICY "Public read access" ON profit_tracking FOR SELECT USING (true);
CREATE POLICY "Public read access" ON agent_actions FOR SELECT USING (true);
CREATE POLICY "Public read access" ON notification_configs FOR SELECT USING (true);

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
CREATE POLICY "Service write access" ON profit_tracking FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON profit_tracking FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON agent_actions FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON agent_actions FOR UPDATE USING (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON notification_configs FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service write access" ON notification_configs FOR UPDATE USING (auth.role() = 'service_role');
