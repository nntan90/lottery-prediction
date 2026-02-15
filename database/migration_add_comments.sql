-- =====================================================
-- MIGRATION: ADD COMMENTS TO DATABASE SCHEMA
-- =====================================================
-- Chạy file này trong Supabase SQL Editor để thêm mô tả cho các bảng và cột.
-- Giúp dễ dàng hiểu ý nghĩa dữ liệu khi xem trong Table Editor.
-- =====================================================

-- 1. Table: lottery_draws
COMMENT ON TABLE lottery_draws IS 'Lưu trữ kết quả xổ số kiến thiết hằng ngày (Raw Data)';
COMMENT ON COLUMN lottery_draws.draw_date IS 'Ngày quay thưởng';
COMMENT ON COLUMN lottery_draws.region IS 'Miền: XSMB (Miền Bắc) hoặc XSMN (Miền Nam)';
COMMENT ON COLUMN lottery_draws.province IS 'Tên tỉnh (slug) cho XSMN, NULL cho XSMB';
COMMENT ON COLUMN lottery_draws.special_prize IS 'Giải Đặc Biệt';
COMMENT ON COLUMN lottery_draws.first_prize IS 'Giải Nhất';
COMMENT ON COLUMN lottery_draws.second_prize IS 'Giải Nhì (Mảng text[])';

-- 2. Table: predictions
COMMENT ON TABLE predictions IS 'Lưu trữ dự đoán hằng ngày và kết quả kiểm tra (Closed-Loop)';
COMMENT ON COLUMN predictions.prediction_date IS 'Ngày dự đoán (cho ngày hôm sau)';
COMMENT ON COLUMN predictions.region IS 'Miền dự đoán (XSMB/XSMN)';
COMMENT ON COLUMN predictions.province IS 'Tỉnh dự đoán (XSMN), NULL hoặc empty cho XSMB';
COMMENT ON COLUMN predictions.model_version IS 'Phiên bản model dùng để dự đoán (ví dụ: lstm_v2)';
COMMENT ON COLUMN predictions.predicted_numbers IS 'JSON chứa số dự đoán và số nóng (hot_numbers)';
COMMENT ON COLUMN predictions.confidence_score IS 'Độ tin cậy của dự đoán (0.0 - 1.0)';
COMMENT ON COLUMN predictions.is_correct IS 'Kết quả kiểm tra: TRUE nếu trúng, FALSE nếu trượt (V2)';
COMMENT ON COLUMN predictions.win_prize IS 'Chi tiết giải thưởng nếu trúng (JSON) (V2)';
COMMENT ON COLUMN predictions.check_time IS 'Thời điểm chạy script kiểm tra (verify_predictions.py) (V2)';

-- 3. Table: model_training_logs (New in V2)
COMMENT ON TABLE model_training_logs IS 'Lịch sử huấn luyện model LSTM (Training History)';
COMMENT ON COLUMN model_training_logs.training_date IS 'Thời điểm bắt đầu huấn luyện';
COMMENT ON COLUMN model_training_logs.region IS 'Miền dữ liệu dùng để train';
COMMENT ON COLUMN model_training_logs.province IS 'Tỉnh dữ liệu (hoặc NULL cho XSMB)';
COMMENT ON COLUMN model_training_logs.model_version IS 'Tên phiên bản model được tạo ra';
COMMENT ON COLUMN model_training_logs.data_range_start IS 'Dữ liệu train bắt đầu từ ngày nào';
COMMENT ON COLUMN model_training_logs.data_range_end IS 'Dữ liệu train kết thúc ngày nào';
COMMENT ON COLUMN model_training_logs.training_params IS 'Tham số huấn luyện (Epochs, Batch size, Neural config)';
COMMENT ON COLUMN model_training_logs.metrics IS 'Kết quả đánh giá model (Accuracy, Loss)';
COMMENT ON COLUMN model_training_logs.model_path IS 'Đường dẫn file .h5 trong Supabase Storage';
COMMENT ON COLUMN model_training_logs.trigger_reason IS 'Lý do train: scheduled (định kỳ), performance_drop (suy giảm), manual (thủ công)';

-- 4. Table: telegram_subscribers
COMMENT ON TABLE telegram_subscribers IS 'Danh sách người dùng đăng ký nhận thông báo Telegram';
COMMENT ON COLUMN telegram_subscribers.chat_id IS 'ID Telegram của người dùng';
COMMENT ON COLUMN telegram_subscribers.subscribed_regions IS 'Danh sách miền đăng ký nhận tin (Array)';

-- 5. Table: crawler_logs
COMMENT ON TABLE crawler_logs IS 'Nhật ký hoạt động của Crawler';
COMMENT ON COLUMN crawler_logs.status IS 'Trạng thái: success, failed, partial';
COMMENT ON COLUMN crawler_logs.records_inserted IS 'Số bản ghi mới đã lưu vào DB';

-- 6. Table: evaluation_metrics (Legacy/V1)
COMMENT ON TABLE evaluation_metrics IS 'Bảng đánh giá hiệu quả dự đoán (Ít dùng trong V2, thay bằng model_training_logs)';

-- 7. Table: model_metadata (Legacy/V1)
COMMENT ON TABLE model_metadata IS 'Metadata cho các model cũ (Prophet, Frequency) - Ít dùng trong V2';
