# 🎯 Các Bước Tiếp Theo - Làm Theo Thứ Tự

## ✅ Đã Hoàn Thành

Tôi đã tạo sẵn tất cả code cho bạn:

- ✅ Database schema (`database/schema.sql`)
- ✅ Python modules (database, crawler, models, bot)
- ✅ 5 GitHub Actions workflows
- ✅ Requirements.txt
- ✅ README.md và SETUP_GUIDE.md
- ✅ .gitignore và .env.example

## 📋 Bạn Cần Làm Gì Tiếp Theo?

### Bước 1: Setup Supabase (15 phút)

**Mở file này để xem hướng dẫn chi tiết**: `SETUP_GUIDE.md` → Bước 1

**Tóm tắt**:
1. Vào https://supabase.com → Đăng ký/Đăng nhập
2. Tạo project mới (chọn Singapore region)
3. Vào **SQL Editor** → Copy nội dung file `database/schema.sql` → Paste → Run
4. Vào **Settings → API** → Copy:
   - `Project URL`
   - `service_role key`
5. **LƯU 2 THÔNG TIN NÀY!** (cần dùng ở bước sau)

### Bước 2: Tạo Telegram Bot (10 phút)

**Mở file**: `SETUP_GUIDE.md` → Bước 2

**Tóm tắt**:
1. Mở Telegram → Tìm `@BotFather`
2. Gửi `/newbot` → Làm theo hướng dẫn
3. Copy **Bot Token** (dạng: `1234567890:ABC...`)
4. Gửi `/start` cho bot của bạn
5. Vào URL: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Copy **Chat ID** từ response
7. **LƯU 2 THÔNG TIN NÀY!**

### Bước 3: Push Code Lên GitHub (10 phút)

```bash
# Nếu chưa có Git repo, tạo mới trên GitHub:
# 1. Vào github.com → New repository
# 2. Tên: lottery-prediction
# 3. Chọn Public (để có unlimited GitHub Actions)
# 4. KHÔNG check "Add README" (vì đã có sẵn)

# Trong terminal:
cd /Users/tannguyen/Workspace/Anlysis_Lottery

# Initialize Git (nếu chưa có)
git init

# Add tất cả files
git add .

# Commit
git commit -m "Initial setup: complete lottery prediction system"

# Link với GitHub repo (thay YOUR_USERNAME)
git remote add origin https://github.com/YOUR_USERNAME/lottery-prediction.git

# Push
git branch -M main
git push -u origin main
```

### Bước 4: Thêm GitHub Secrets (5 phút)

1. Vào repository trên GitHub
2. Click **Settings** tab
3. Bên trái: **Secrets and variables** → **Actions**
4. Click **"New repository secret"**
5. Thêm 4 secrets (từng cái một):

```
Name: SUPABASE_URL
Value: <paste từ Bước 1>

Name: SUPABASE_SERVICE_KEY
Value: <paste từ Bước 1>

Name: TELEGRAM_BOT_TOKEN
Value: <paste từ Bước 2>

Name: TELEGRAM_CHAT_ID
Value: <paste từ Bước 2>
```

### Bước 5: Chạy Initial Backfill (20 phút)

1. Vào repository → Tab **Actions**
2. Bên trái, click workflow: **"05 - Initial Data Backfill"**
3. Click **"Run workflow"** (nút xanh bên phải)
4. Nhập:
   - Days: `30` (test với 30 ngày trước)
   - Region: `BOTH`
5. Click **"Run workflow"**
6. Đợi 5-10 phút, workflow sẽ chạy

**Kiểm tra kết quả**:
- Vào Supabase → **Table Editor** → `lottery_draws`
- Bạn sẽ thấy ~60 records (30 XSMB + 30 XSMN)

### Bước 6: Test Workflows (15 phút)

Chạy thủ công từng workflow để test:

**6.1. Test Prediction**
1. Actions → **"02 - Generate Predictions"**
2. Click **"Run workflow"**
3. Đợi 2-3 phút
4. Check Supabase → `predictions` table → sẽ có 2 records mới

**6.2. Test Telegram Notification**
1. Actions → **"04 - Send Telegram Notifications"**
2. Click **"Run workflow"**
3. Check Telegram app → bạn sẽ nhận được 2 messages!

**6.3. Test Evaluation** (optional)
1. Actions → **"03 - Evaluate Predictions"**
2. Click **"Run workflow"**
3. Check Supabase → `evaluation_metrics` table

### Bước 7: Hoàn Thành! 🎉

Nếu tất cả workflows chạy thành công:

✅ **Hệ thống đã sẵn sàng!**

Từ giờ, hệ thống sẽ tự động:
- **19:00 mỗi ngày**: Crawl kết quả mới
- **19:30 mỗi ngày**: Đánh giá predictions hôm qua
- **20:00 mỗi ngày**: Tạo predictions cho ngày mai
- **20:05 mỗi ngày**: Gửi Telegram notification

Bạn chỉ cần đợi nhận notification hàng ngày!

## 🔍 Monitoring

### Xem Logs
- Vào **GitHub Actions** tab để xem logs của từng workflow

### Xem Data
- Vào **Supabase → Table Editor** để xem data

### Nhận Notifications
- Check Telegram app lúc ~20:00 mỗi ngày

## ❓ Nếu Gặp Lỗi

### Workflow failed
- Click vào workflow failed → Xem logs
- Thường do: sai secrets hoặc website nguồn thay đổi

### Không nhận Telegram message
- Kiểm tra lại `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID`
- Đảm bảo đã click "Start" bot

### Crawler không có data
- Website nguồn có thể chưa có kết quả cho ngày đó
- Thử chạy lại với ngày khác

## 📞 Cần Giúp?

Nếu gặp vấn đề ở bất kỳ bước nào:
1. Copy error message
2. Cho tôi biết bạn đang ở bước nào
3. Tôi sẽ giúp debug!

---

**Chúc bạn thành công! 🚀**


https://islcxaqdqhwgcqkdozeq.supabase.co
sb_publishable_UoAFhuDaaImQS3lA4O7_mw_xgPpCjyw

{"ok":true,"result":[{"update_id":110750190,
"message":{"message_id":922,"from":{"id":1185662651,"is_bot":false,"first_name":"TaN","username":"tannguyen1000","language_code":"vi"},"chat":{"id":1185662651,"first_name":"TaN","username":"tannguyen1000","type":"private"},"date":1770828913,"text":"/history","entities":[{"offset":0,"length":8,"type":"bot_command"}]}}]}

7541610102:AAHA-yKQwGv9pbeVsXWuZsi-cPX7AkI3d80