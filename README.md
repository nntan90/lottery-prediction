# 🤖 VietlottAI (Analysis Lottery)
**Hệ thống AI tự động hóa cào dữ liệu, phân tích và xếp hạng tín hiệu thống kê 2 số cuối bằng Ensemble Machine Learning.**

> ⚠️ **EDUCATIONAL PURPOSE ONLY**: Dự án này được xây dựng **hoàn toàn cho mục đích học tập** về lập trình tự động hóa (GitHub Actions), xử lý dữ liệu và Machine Learning (XGBoost, LSTM, Markov Chain). Các con số được tạo ra chỉ mang tính chất tham khảo vui vẻ.

---

## ✨ Tính Năng Chính
- 🧠 **Multi-Model Ensemble v3.2**: Xếp hạng Top 3 tín hiệu thống kê 2 số cuối từ 5 mô hình song song (Frequency, Gap, Markov, XGBoost, LSTM).
- 🤖 **Master Retrain Agent**: Hệ thống tự đánh giá hiệu năng (Hit Rate, AUC) và quyết định retrain mô hình XGBoost với các chiến lược linh hoạt.
- 📈 **Walk-forward Backtest**: Đo Hit@1, Hit@3, lift so với random baseline, ROI và đóng góp từng sub-model.
- ⚙️ **Automated Workflow**: Tự động chạy hàng ngày hoàn toàn miễn phí trên Serverless GitHub Actions.
- 📱 **Telegram Notifications**: Gửi báo cáo kết quả và Top 3 tín hiệu qua Telegram Bot.
- ☁️ **Cloud Database**: Lưu trữ lịch sử tạo số và model registry trên Supabase.

---

## 🏗️ Cách Hoạt Động

```
[GitHub Actions] --> [Daily Crawl] --> [Feature Engineering] --> [Predict Ensemble] --> [Telegram Notify] --> [Verify & Retrain]
```

Hệ thống hoạt động đơn giản như một chuỗi cron-job:
1. **19:00**: Tự động lấy kết quả xổ số thực tế của XSMB và XSMN hôm nay.
2. **19:30**: Đánh giá kết quả Top 3 tín hiệu của ngày hôm qua, kích hoạt Master Agent nếu mô hình có dấu hiệu suy giảm hiệu năng (Perf Drop).
3. **07:00 (Sáng hôm sau)**: Chạy pipeline Ensemble (5 mô hình) để tạo Top 3 tín hiệu thống kê và gửi Telegram.

---

## 🚀 Hướng Dẫn Cài Đặt Nhanh

### 1. Chuẩn bị Supabase (Database)
1. Tạo project miễn phí tại [supabase.com](https://supabase.com).
2. Vào **SQL Editor**, chạy file `database/schema_final.sql` để tạo bảng.
3. Vào **Settings → API**, lưu lại `Project URL` và `service_role key`.
4. Bảng `notification_configs` cho phép bật/tắt từng nhóm Telegram message và lưu metadata cron/job để dễ điều chỉnh lịch vận hành.

### 2. Tạo Telegram Bot
1. Chat với `@BotFather` trên Telegram để tạo bot mới.
2. Lấy **Bot Token**.
3. Lấy **Chat ID** của bạn (dùng `@userinfobot`).

### 3. Setup GitHub Repository
1. Fork repository này về tài khoản GitHub của bạn.
2. Vào **Settings → Secrets and variables → Actions**, thêm 4 secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### 4. Khởi chạy
1. Vào tab **Actions** trên GitHub.
2. Chọn workflow **"05 - Initial Data Backfill"** -> Run workflow.
3. Đợi vài phút để hệ thống khởi tạo dữ liệu ban đầu.
4. Xong! Hệ thống sẽ tự động chạy hàng ngày.

---

## 🛠️ Công Cụ Hỗ Trợ

- **Kiểm tra Database**: File `database/analyze_db_size.sql` giúp bạn xem dung lượng lưu trữ.
- **Cấu hình Telegram**: Sửa bảng `notification_configs` để bật/tắt từng job, đổi `chat_id`, `parse_mode`, prefix/suffix hoặc ghi chú cron hiện hành.
- **Backtest walk-forward**: Chạy `python src/scripts/backtest_walk_forward.py --from-date YYYY-MM-DD --to-date YYYY-MM-DD` để đo lift so với random baseline.
- **Dọn dẹp**: Workflow tự động dọn dẹp dữ liệu cũ mỗi tháng để tiết kiệm tài nguyên.

---

## 📜 Disclaimer

Dự án này dùng dữ liệu công khai và các mô hình thống kê để xếp hạng tín hiệu xác suất tương đối, không tạo ra cam kết chắc thắng.
**Tác giả không chịu trách nhiệm về việc sử dụng các con số này vào mục đích cá cược hay cờ bạc.** Vui lòng tuân thủ pháp luật sở tại.

---

## License
MIT License.
