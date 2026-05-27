-- ============================================================================
-- Migration 07: XSMB v4.0 — Thêm 8 features mới cho pair_features
-- ============================================================================
-- Mục đích: Support XGBoost v4 với 25 features (17 cũ + 8 mới)
-- Tương thích: backward compatible — XSMN vẫn dùng 17 features cũ
--              các cột mới sẽ NULL cho XSMN rows
-- ============================================================================

-- Ultra-short frequency (3 kỳ gần nhất)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS freq_3 REAL;

-- Medium-term frequency (14 kỳ)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS freq_14 REAL;

-- Frequency tính trên cùng weekday (30 kỳ cùng thứ)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS weekday_freq_30 REAL;

-- Gap hiện tại so với historical distribution (percentile 0-1)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS gap_percentile REAL;

-- Tần suất 2 pair lân cận (±1, ±10) trong 7 kỳ
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS neighbor_freq_7 REAL;

-- Giải nào xuất hiện lần cuối (encoded: 0=far, 1=recent, 2=very_recent)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS last_position_encoded INT;

-- Số kỳ liên tiếp xuất hiện (+) hoặc vắng mặt (-)
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS streak_length INT;

-- Max correlation với pair khác trong 20 kỳ
ALTER TABLE pair_features ADD COLUMN IF NOT EXISTS cross_pair_corr REAL;
