-- Migration 10: Model Credibility Scoring System
-- Cache credibility scores cho mỗi model, mỗi ngày, mỗi region.
-- Có thể delete & recalculate bất kỳ lúc nào — không phải source-of-truth.

CREATE TABLE IF NOT EXISTS model_credibility (
  id              SERIAL PRIMARY KEY,
  score_date      DATE NOT NULL,
  region          VARCHAR(10) NOT NULL,
  model_name      VARCHAR(50) NOT NULL,

  -- 6 Credibility Dimensions (all normalized 0.0 - 1.0)
  recency_mrr         FLOAT,       -- Dim 1: Recency-weighted MRR
  streak_momentum     FLOAT,       -- Dim 2: Hot/cold streak score
  ndcg_score          FLOAT,       -- Dim 3: NDCG@5 ranking quality
  consensus_accuracy  FLOAT,       -- Dim 4: Consensus hit accuracy
  stability_index     FLOAT,       -- Dim 5: Output stability
  recovery_speed      FLOAT,       -- Dim 6: Recovery after miss streaks

  -- Composite result
  composite_score     FLOAT NOT NULL,    -- Weighted sum of 6 dimensions
  credibility_weight  FLOAT NOT NULL,    -- Final weight after smoothing + clamp

  -- Context metadata
  lookback_draws  INT,             -- Number of draws evaluated
  total_evaluated INT,             -- Number of draws with actual results
  streak_type     VARCHAR(10),     -- 'hot_3', 'hot_2', 'warm', 'neutral', 'cold_2', 'cold_3'

  created_at      TIMESTAMP DEFAULT NOW(),

  CONSTRAINT model_credibility_unique UNIQUE (score_date, region, model_name)
);

COMMENT ON TABLE model_credibility IS 'Cache credibility scores cho Model Credibility Scoring System';
CREATE INDEX IF NOT EXISTS idx_mc_date   ON model_credibility(score_date DESC);
CREATE INDEX IF NOT EXISTS idx_mc_region ON model_credibility(region, model_name);

-- RLS
ALTER TABLE model_credibility ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON model_credibility FOR SELECT USING (true);
CREATE POLICY "Service write access" ON model_credibility FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "Service update access" ON model_credibility FOR UPDATE USING (auth.role() = 'service_role');
