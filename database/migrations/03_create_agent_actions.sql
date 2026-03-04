-- Migration: 03_create_agent_actions
-- Bảng lưu lịch sử hành động của Master Retrain Agent

CREATE TABLE IF NOT EXISTS agent_actions (
    id              BIGSERIAL PRIMARY KEY,
    action_date     DATE NOT NULL,
    region          TEXT NOT NULL,
    province        TEXT,                   -- NULL = XSMB (all)
    weekday         INT,                    -- Weekday model được retrain (0=Mon..6=Sun), NULL = non-weekday model
    action_type     TEXT NOT NULL,          -- 'retrain_triggered', 'skipped', 'no_action'
    reason          TEXT,                   -- Lý do quyết định
    strategy        TEXT,                   -- 'boost_estimators', 'conservative', 'full_reset', NULL
    old_metric_auc  FLOAT,                  -- AUC của model cũ tại thời điểm quyết định
    old_hit_rate    FLOAT,                  -- hit_rate của model cũ
    new_params      JSONB,                  -- Hyperparameters mới được dùng
    old_params      JSONB,                  -- Hyperparameters cũ
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Index để query nhanh theo đài + ngày
CREATE INDEX IF NOT EXISTS idx_agent_actions_station
    ON agent_actions (region, province, action_date DESC);

CREATE INDEX IF NOT EXISTS idx_agent_actions_date
    ON agent_actions (action_date DESC);

COMMENT ON TABLE agent_actions IS 'Lịch sử hành động của Master Retrain Agent — quyết định retrain/skip cho từng đài mỗi ngày';
