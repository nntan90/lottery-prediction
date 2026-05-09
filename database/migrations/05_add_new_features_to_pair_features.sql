-- Migration 05: Add new features to pair_features table
-- Run in Supabase SQL Editor (Dashboard > SQL Editor)
-- Adds 6 new ML features for improved XGBoost prediction accuracy
-- Applies to both XSMB and XSMN pair_features

ALTER TABLE pair_features
  ADD COLUMN IF NOT EXISTS freq_7         FLOAT,      -- Tần suất 7 kỳ gần nhất
  ADD COLUMN IF NOT EXISTS consecutive_miss INT,      -- Số kỳ liên tiếp chưa xuất hiện
  ADD COLUMN IF NOT EXISTS is_hot_3        BOOLEAN,   -- Xuất hiện trong 3 kỳ liên tiếp gần nhất?
  ADD COLUMN IF NOT EXISTS decade_freq_30  FLOAT,     -- Tần suất nhóm thập phân (00-09,10-19..) 30 kỳ
  ADD COLUMN IF NOT EXISTS mirror_freq_30  FLOAT,     -- Tần suất cặp đảo số (23↔32) 30 kỳ
  ADD COLUMN IF NOT EXISTS month_freq      FLOAT;     -- Tần suất trong cùng tháng, toàn bộ lịch sử

COMMENT ON COLUMN pair_features.freq_7         IS 'Tần suất xuất hiện 7 kỳ gần nhất (nóng/lạnh ngắn hạn)';
COMMENT ON COLUMN pair_features.consecutive_miss IS 'Số kỳ liên tiếp hiện tại chưa xuất hiện (0 = kỳ trước có)';
COMMENT ON COLUMN pair_features.is_hot_3       IS 'TRUE nếu pair xuất hiện trong cả 3 kỳ liên tiếp gần nhất';
COMMENT ON COLUMN pair_features.decade_freq_30 IS 'Tần suất nhóm thập phân cùng chữ số đầu (00-09, 10-19...) trong 30 kỳ';
COMMENT ON COLUMN pair_features.mirror_freq_30 IS 'Tần suất cặp đảo vị trí (23↔32) trong 30 kỳ';
COMMENT ON COLUMN pair_features.month_freq     IS 'Tần suất xuất hiện trong cùng tháng so với toàn bộ lịch sử';
