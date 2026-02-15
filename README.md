# 🎲 Lottery Random Number Generator (XSMB & XSMN)

**Hệ thống tạo số ngẫu nhiên tự động dựa trên Random.org cho Xổ số Kiến thiết.**

> ⚠️ **EDUCATIONAL PURPOSE ONLY**: Dự án này được xây dựng **hoàn toàn cho mục đích học tập** về lập trình tự động hóa (GitHub Actions) và xử lý dữ liệu. Các con số được tạo ra là **ngẫu nhiên** và chỉ mang tính chất tham khảo vui vẻ.

---

## ✨ Tính Năng Chính
- 🎲 **Random Number Generation**: Tạo dãy số may mắn ngẫu nhiên mỗi ngày cho XSMB và 21 tỉnh XSMN.
- 🤖 **Automated Workflow**: Tự động chạy hàng ngày hoàn toàn miễn phí trên GitHub Actions.
- 📱 **Telegram Notifications**: Gửi kết quả ngẫu nhiên về điện thoại của bạn mỗi sáng.
- ☁️ **Cloud Database**: Lưu trữ lịch sử tạo số trên Supabase để tiện theo dõi.

---

## 🏗️ Cách Hoạt Động

```
[GitHub Actions] --> [Daily Trigger] --> [Fetch Random Numbers] --> [Save to DB] --> [Notify Telegram]
```

Hệ thống hoạt động đơn giản như một cron-job:
1. **16:30**: Tự động lấy kết quả xổ số mới nhất để cập nhật dữ liệu.
2. **17:00**: Chạy thuật toán random để tạo bộ số cho ngày mai.
3. **07:00**: Gửi thông báo kết quả qua Telegram Bot.

---

## 🚀 Hướng Dẫn Cài Đặt Nhanh

### 1. Chuẩn bị Supabase (Database)
1. Tạo project miễn phí tại [supabase.com](https://supabase.com).
2. Vào **SQL Editor**, chạy file `database/schema_final.sql` để tạo bảng.
3. Vào **Settings → API**, lưu lại `Project URL` và `service_role key`.

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
- **Dọn dẹp**: Workflow tự động dọn dẹp dữ liệu cũ mỗi tháng để tiết kiệm tài nguyên.

---

## 📜 Disclaimer

Dự án này sử dụng các thuật toán tạo số ngẫu nhiên (Pseudo-random number generation) và data từ các nguồn công khai. 
**Tác giả không chịu trách nhiệm về việc sử dụng các con số này vào mục đích cá cược hay cờ bạc.** Vui lòng tuân thủ pháp luật sở tại.

---

## License
MIT License.
