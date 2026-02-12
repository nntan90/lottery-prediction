# 🎯 Lottery Prediction System

Hệ thống dự đoán xổ số tự động **100% miễn phí** sử dụng GitHub Actions, Supabase và Telegram Bot.

> ⚠️ **DISCLAIMER**: Hệ thống này chỉ mang tính **giải trí và nghiên cứu**. Xổ số là ngẫu nhiên và không thể dự đoán chính xác. Không nên dựa vào dự đoán này để đầu tư tiền bạc.

## ✨ Tính Năng

- 🤖 **Tự động crawl** kết quả xổ số hàng ngày (XSMB & XSMN)
- 📊 **Phân tích patterns** dựa trên dữ liệu lịch sử
- 🎯 **Tạo predictions** cho ngày tiếp theo
- 📱 **Gửi thông báo** qua Telegram Bot
- 📈 **Đánh giá độ chính xác** của predictions
- 💾 **Lưu trữ** tất cả dữ liệu trên Supabase
- 🔄 **Hoàn toàn tự động** với GitHub Actions

## 🏗️ Kiến Trúc

```
┌─────────────────┐
│  GitHub Actions │  ← Chạy workflows tự động hàng ngày
└────────┬────────┘
         │
         ├─► 19:00: Crawl kết quả mới
         ├─► 19:30: Đánh giá predictions hôm qua
         ├─► 20:00: Tạo predictions cho ngày mai
         └─► 20:05: Gửi Telegram notification
                │
                ├─► Supabase (Database)
                └─► Telegram Bot
```

## 🚀 Quick Start

### Bước 1: Setup Supabase

1. Tạo account tại [supabase.com](https://supabase.com)
2. Tạo project mới (chọn region Singapore)
3. Vào **SQL Editor**, copy nội dung `database/schema.sql` và run
4. Vào **Settings → API**, lấy:
   - `Project URL`
   - `service_role key`

### Bước 2: Setup Telegram Bot

1. Mở Telegram, tìm `@BotFather`
2. Gửi `/newbot` và làm theo hướng dẫn
3. Lưu lại **Bot Token**
4. Gửi message `/start` cho bot của bạn
5. Vào `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
6. Lấy **Chat ID** từ response

### Bước 3: Setup GitHub Repository

1. Fork hoặc clone repo này
2. Vào **Settings → Secrets → Actions**
3. Thêm 4 secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### Bước 4: Chạy Initial Backfill

1. Vào tab **Actions**
2. Chọn workflow **"05 - Initial Data Backfill"**
3. Click **"Run workflow"**
4. Nhập số ngày (khuyến nghị: 365)
5. Chọn region: BOTH
6. Đợi 15-20 phút để crawl xong

### Bước 5: Test Workflows

Chạy thủ công từng workflow để test:

1. **02 - Generate Predictions** → Check Supabase có prediction mới
2. **04 - Send Telegram Notifications** → Check Telegram nhận được message
3. **03 - Evaluate Predictions** → Check evaluation metrics

✅ **Done!** Hệ thống sẽ tự động chạy hàng ngày.

## 📁 Cấu Trúc Project

```
lottery-prediction/
├── .github/workflows/       # GitHub Actions workflows
│   ├── 01-daily-crawl.yml
│   ├── 02-predict.yml
│   ├── 03-evaluate.yml
│   ├── 04-notify.yml
│   └── 05-initial-backfill.yml
├── src/
│   ├── database/           # Supabase client
│   ├── crawler/            # XSMB & XSMN crawlers
│   ├── models/             # Frequency analyzer
│   └── bot/                # Telegram bot
├── database/
│   └── schema.sql          # Database schema
├── requirements.txt
├── SETUP_GUIDE.md         # Hướng dẫn chi tiết
└── README.md
```

## 🔧 Local Development

### Setup

```bash
# Clone repo
git clone <your-repo-url>
cd lottery-prediction

# Tạo virtual environment
python -m venv venv
source venv/bin/activate  # Mac/Linux
# hoặc: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Sau đó edit .env và điền credentials
```

### Test Modules

```bash
# Test Supabase connection
python src/database/supabase_client.py

# Test XSMB crawler
python src/crawler/xsmb_crawler.py

# Test frequency analyzer
python src/models/frequency_analyzer.py

# Test Telegram bot
python src/bot/telegram_bot.py
```

## 📊 Database Schema

Hệ thống sử dụng 6 tables:

- **lottery_draws**: Kết quả quay số
- **predictions**: Dự đoán
- **evaluation_metrics**: Metrics đánh giá
- **telegram_subscribers**: Người dùng Telegram
- **crawler_logs**: Logs crawler
- **model_metadata**: Metadata mô hình

Chi tiết xem file `database/schema.sql`.

## 💰 Chi Phí: 0 VNĐ

- ✅ **GitHub Actions**: Unlimited cho public repo
- ✅ **Supabase**: 1GB storage + 2GB bandwidth/tháng (free tier)
- ✅ **Telegram Bot**: Hoàn toàn miễn phí

**Estimated usage**:
- Storage: ~50MB/năm
- Bandwidth: ~500MB/tháng
- GitHub Actions: ~600 phút/tháng

→ Rất xa giới hạn free tier!

## 🔍 Monitoring

### Check Logs

Vào **GitHub Actions** tab để xem logs của từng workflow.

### Check Database

Vào **Supabase → Table Editor** để xem data.

### Check Telegram

Bot sẽ gửi message hàng ngày lúc ~20:00 GMT+7.

## 🛠️ Troubleshooting

### Crawler failed

- Check website nguồn có hoạt động không
- CSS selectors có thể thay đổi → cần update code
- Thử với ngày khác (có thể chưa có kết quả)

### Telegram không nhận message

- Kiểm tra `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID`
- Đảm bảo đã click "Start" bot

### Workflow failed

- Check **Actions** tab → Click vào workflow failed → Xem logs
- Thường do: sai secrets hoặc không đủ dữ liệu

## 📝 Roadmap

- [ ] Thêm Prophet model (advanced prediction)
- [ ] Support thêm miền Trung
- [ ] Web dashboard để xem predictions
- [ ] Telegram commands (`/status`, `/stats`)
- [ ] Email notifications

## 🤝 Contributing

Pull requests are welcome! Đặc biệt:

- Cải thiện crawler (thêm nguồn dự phòng)
- Thêm models mới
- Cải thiện accuracy
- Fix bugs

## 📄 License

MIT License - Free to use for personal and educational purposes.

## ⚠️ Legal Disclaimer

Hệ thống này được tạo ra chỉ với mục đích:
- ✅ Giải trí
- ✅ Nghiên cứu machine learning
- ✅ Học tập về automation

**KHÔNG NÊN**:
- ❌ Dựa vào predictions để đầu tư tiền
- ❌ Kỳ vọng thắng xổ số
- ❌ Sử dụng cho mục đích thương mại

Xổ số là **hoàn toàn ngẫu nhiên** và không thể dự đoán chính xác.

---

Made with ❤️ for learning purposes
