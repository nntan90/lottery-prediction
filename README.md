# 🤖 VietlottAI (Analysis Lottery)
**Hệ thống AI tự động hóa cào dữ liệu, phân tích và xếp hạng tín hiệu thống kê 2 số cuối bằng Ensemble Machine Learning.**

> ⚠️ **EDUCATIONAL PURPOSE ONLY**: Dự án này được xây dựng **hoàn toàn cho mục đích học tập** về lập trình tự động hóa (GitHub Actions), xử lý dữ liệu và Machine Learning (XGBoost, LSTM, Markov Chain). Các con số được tạo ra chỉ mang tính chất tham khảo vui vẻ.

---

## ✨ Tính Năng Chính
- 🧠 **Multi-Model Ensemble XSMN**: Xếp hạng Top 3 tín hiệu thống kê 2 số cuối từ 6 mô hình (Frequency, Gap, Markov, XGBoost, LSTM, CDM).
- 🤖 **Master Retrain Agent**: Hệ thống tự đánh giá hiệu năng (Hit Rate, AUC) và quyết định retrain mô hình XGBoost với các chiến lược linh hoạt.
- 📈 **Out-of-time Evaluation**: Đo Hit@1, any-hit, mục tiêu >=2/3, lift so với random baseline, ROI và đóng góp từng sub-model từ prediction đã lưu.
- ⚙️ **Automated Workflow**: Tự động chạy hàng ngày hoàn toàn miễn phí trên Serverless GitHub Actions.
- 📱 **Telegram Notifications**: Gửi báo cáo kết quả và Top 3 tín hiệu qua Telegram Bot.
- ☁️ **Cloud Database**: Lưu trữ lịch sử tạo số và model registry trên Supabase.

---

## BMAD Development Workflow

Repository cài BMAD Method v6 cho Codex tại `.agents/skills` và `_bmad`.
Ngữ cảnh bắt buộc cho agent nằm ở `docs/project-context.md`; artifacts nằm
trong `_bmad-output`.

```bash
uv run --python 3.11 _bmad/scripts/resolve_config.py --project-root .
```

Các skill chính: `bmad-help`, `bmad-quick-dev`, `bmad-code-review` và
`bmad-document-project`.

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

### 5. Chạy DDT shadow trên máy local qua Telegram

DDT không còn chạy subprocess trong GitHub Actions. Production chỉ đọc row
`ddt_shadow` đã được worker local lưu; vì vậy lỗi hoặc việc máy local tắt không
ảnh hưởng Top 3 XSMN production.

1. Chạy migration `database/migrations/12_add_shadow_prediction_tracking.sql`.
2. Tạo `.env` tại project root (không commit) với:

   ```dotenv
   SUPABASE_URL=...
   SUPABASE_SERVICE_KEY=...
   TELEGRAM_BOT_TOKEN=...
   DDT_ALLOWED_CHAT_IDS=123456789
   DDT_ALLOWED_USER_IDS=123456789
   ```

3. Cài và kiểm tra LaunchAgent macOS:

   ```bash
   scripts/manage_ddt_local_bot.sh install
   scripts/manage_ddt_local_bot.sh status
   ```

4. Khi máy không có repeating power schedule khác, cài wake 20:55 bằng một
   lệnh quản trị riêng:

   ```bash
   scripts/manage_ddt_local_bot.sh wake-status
   scripts/manage_ddt_local_bot.sh wake-install
   ```

   `wake-install` cần quyền admin của macOS và sẽ dừng nếu phát hiện repeating
   schedule không thuộc DDT; script không âm thầm ghi đè lịch power toàn máy.

5. Bot giữ máy thức từ 20:55, gửi prompt lúc 21:00 cho kỳ quay ngày hôm sau và
   nhận xác nhận đến trước 12:00 trưa ngày quay. `/ddt` tự suy ngày từ cửa sổ
   đang mở; `/ddt YYYY-MM-DD` cũng chỉ hợp lệ trong đúng cửa sổ của ngày đó.
   Callback chỉ dùng một lần và chỉ chat/user trong allowlist mới được chạy.
   Run được duyệt trước 12:00 vẫn hoàn tất và gửi kết quả nếu chạy qua 12:00.

Các lệnh vận hành:

```bash
scripts/manage_ddt_local_bot.sh start
scripts/manage_ddt_local_bot.sh stop
scripts/manage_ddt_local_bot.sh restart
scripts/manage_ddt_local_bot.sh status
scripts/manage_ddt_local_bot.sh wake-status
```

Log local nằm tại `.local/ddt-bot/` và đã được ignore. File launchd chỉ chứa
đường dẫn thực thi; token/key vẫn được process nạp từ `.env`. Để wake/giữ thức
ổn định, giữ nắp MacBook mở; cắm sạc được khuyến nghị. Wake từ trạng thái tắt
máy hoặc khi chưa đăng nhập/FileVault chưa mở khóa không bảo đảm LaunchAgent
của user sẽ chạy.

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
