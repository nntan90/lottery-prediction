-- Migration 08: Thêm cột để tracking hit/miss cho từng sub-model trong ensemble pipeline
ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS hit BOOLEAN;
ALTER TABLE model_predictions ADD COLUMN IF NOT EXISTS matched_pairs SMALLINT[];

-- Cập nhật comment
COMMENT ON COLUMN model_predictions.hit IS 'TRUE nếu ít nhất 1 cặp trong top 5 trúng (tồn tại trong tail_set)';
COMMENT ON COLUMN model_predictions.matched_pairs IS 'Danh sách các cặp thực sự trúng của model này';
