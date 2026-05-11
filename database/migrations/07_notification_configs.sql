-- =====================================================
-- Migration 07: Notification Configs
-- Scope: Centralize Telegram notification switches and schedule metadata
-- =====================================================

CREATE TABLE IF NOT EXISTS notification_configs (
  id                  SERIAL PRIMARY KEY,
  config_key          VARCHAR(80) UNIQUE NOT NULL,
  channel             VARCHAR(30) NOT NULL DEFAULT 'telegram',
  enabled             BOOLEAN NOT NULL DEFAULT TRUE,

  -- Workflow/schedule metadata for operational visibility.
  workflow_file       VARCHAR(120),
  job_name            VARCHAR(120),
  schedule_cron_utc   VARCHAR(80),
  schedule_timezone   VARCHAR(64) DEFAULT 'Asia/Ho_Chi_Minh',
  schedule_local_time VARCHAR(40),

  -- Telegram delivery overrides. NULL means use env var/default.
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
COMMENT ON COLUMN notification_configs.config_key IS 'Stable key used by scripts, e.g. predict_ensemble_xsmb';
COMMENT ON COLUMN notification_configs.enabled IS 'FALSE để skip gửi Telegram mà không cần sửa code';
COMMENT ON COLUMN notification_configs.chat_id IS 'Override Telegram chat_id; NULL dùng TELEGRAM_CHAT_ID env';
COMMENT ON COLUMN notification_configs.schedule_cron_utc IS 'Cron UTC đang dùng trong GitHub Actions, để dễ quản trị lịch';

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

ALTER TABLE notification_configs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access" ON notification_configs
  FOR SELECT USING (true);

CREATE POLICY "Service write access" ON notification_configs
  FOR INSERT WITH CHECK (auth.role() = 'service_role');

CREATE POLICY "Service update access" ON notification_configs
  FOR UPDATE USING (auth.role() = 'service_role');
