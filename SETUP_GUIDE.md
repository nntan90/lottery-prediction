# 🚀 Hướng Dẫn Setup Từng Bước - Dành Cho Junior

## Bước 1: Tạo Tài Khoản Supabase (15 phút)

### 1.1 Đăng ký Supabase
1. Mở trình duyệt, vào: https://supabase.com
2. Click nút **"Start your project"** (góc trên bên phải)
3. Đăng nhập bằng GitHub account của bạn
4. Click **"Authorize Supabase"**

### 1.2 Tạo Project Mới
1. Sau khi đăng nhập, click **"New Project"**
2. Điền thông tin:
   - **Name**: `lottery-prediction` (hoặc tên bạn thích)
   - **Database Password**: Tạo password mạnh (LƯU LẠI password này!)
   - **Region**: Chọn `Southeast Asia (Singapore)` (gần VN nhất)
   - **Pricing Plan**: Chọn **Free** (đã đủ dùng)
3. Click **"Create new project"**
4. Đợi 2-3 phút để Supabase setup database

### 1.3 Lấy API Keys
1. Sau khi project được tạo, vào **Settings** (icon bánh răng bên trái)
2. Click **API** trong menu
3. Bạn sẽ thấy 2 thông tin quan trọng:
   - **Project URL**: Copy và lưu lại (dạng: `https://xxxxx.supabase.co`)
   - **service_role key**: Click **"Reveal"** → Copy và lưu lại (dạng: `eyJhbGc...`)

> ⚠️ **LƯU Ý**: `service_role key` rất quan trọng, KHÔNG share công khai!

### 1.4 Tạo Database Schema
1. Trong Supabase dashboard, click **SQL Editor** (bên trái)
2. Click **"New query"**
3. Copy toàn bộ nội dung file `database/schema.sql` (tôi sẽ tạo file này ở bước sau)
4. Paste vào SQL Editor
5. Click **"Run"** (hoặc Ctrl+Enter)
6. Nếu thành công, bạn sẽ thấy message: "Success. No rows returned"

### 1.5 Kiểm Tra Tables
1. Click **Table Editor** (bên trái)
2. Bạn sẽ thấy 6 tables mới:
   - `lottery_draws`
   - `predictions`
   - `evaluation_metrics`
   - `telegram_subscribers`
   - `crawler_logs`
   - `model_metadata`

✅ **Hoàn thành Bước 1!**

---

## Bước 2: Tạo Telegram Bot (10 phút)

### 2.1 Tìm BotFather
1. Mở Telegram app (mobile hoặc desktop)
2. Tìm kiếm: `@BotFather`
3. Click vào bot có dấu tick xanh (verified)

### 2.2 Tạo Bot Mới
1. Gửi message: `/newbot`
2. BotFather sẽ hỏi tên bot, reply: `Lottery Prediction Bot` (hoặc tên bạn thích)
3. BotFather hỏi username, reply: `your_name_lottery_bot` (phải kết thúc bằng `_bot`)
4. Nếu username đã tồn tại, thử tên khác

### 2.3 Lấy Bot Token
1. Sau khi tạo thành công, BotFather sẽ gửi message chứa **token**
2. Token có dạng: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`
3. **Copy và lưu lại token này!**

### 2.4 Lấy Chat ID
1. Tìm bot bạn vừa tạo trong Telegram (search username)
2. Click **"Start"** hoặc gửi message `/start`
3. Mở trình duyệt, vào URL (thay YOUR_BOT_TOKEN):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
4. Bạn sẽ thấy JSON response, tìm `"chat":{"id":123456789}`
5. **Copy số `id` này** (đây là CHAT_ID của bạn)

✅ **Hoàn thành Bước 2!**

---

## Bước 3: Setup GitHub Repository (15 phút)

### 3.1 Tạo Repository
1. Vào https://github.com
2. Click nút **"+"** (góc trên bên phải) → **"New repository"**
3. Điền thông tin:
   - **Repository name**: `lottery-prediction`
   - **Description**: `Automated lottery prediction system`
   - **Visibility**: Chọn **Public** (để có unlimited GitHub Actions)
   - ✅ Check **"Add a README file"**
4. Click **"Create repository"**

### 3.2 Clone Repository Về Máy
Mở Terminal và chạy:
```bash
cd ~/Workspace
git clone https://github.com/YOUR_USERNAME/lottery-prediction.git
cd lottery-prediction
```

### 3.3 Thêm GitHub Secrets
1. Vào repository trên GitHub
2. Click **Settings** tab
3. Bên trái, click **Secrets and variables** → **Actions**
4. Click **"New repository secret"**
5. Thêm 4 secrets (từng cái một):

**Secret 1:**
- Name: `SUPABASE_URL`
- Value: Paste Project URL từ Bước 1.3

**Secret 2:**
- Name: `SUPABASE_SERVICE_KEY`
- Value: Paste service_role key từ Bước 1.3

**Secret 3:**
- Name: `TELEGRAM_BOT_TOKEN`
- Value: Paste bot token từ Bước 2.3

**Secret 4:**
- Name: `TELEGRAM_CHAT_ID`
- Value: Paste chat ID từ Bước 2.4

✅ **Hoàn thành Bước 3!**

---

## Bước 4: Chuẩn Bị Code (Tôi sẽ làm giúp bạn)

Tôi sẽ tạo tất cả files cần thiết:
- ✅ Cấu trúc thư mục
- ✅ Database schema SQL
- ✅ Python modules (crawler, models, bot)
- ✅ GitHub Actions workflows
- ✅ Requirements.txt

Bạn chỉ cần:
1. Review code tôi tạo
2. Push lên GitHub
3. Test workflows

---

## Bước 5: Test Hệ Thống (20 phút)

### 5.1 Push Code Lên GitHub
```bash
git add .
git commit -m "Initial setup: complete lottery prediction system"
git push origin main
```

### 5.2 Chạy Initial Backfill
1. Vào repository trên GitHub
2. Click tab **Actions**
3. Bên trái, click workflow **"Initial Data Backfill"**
4. Click **"Run workflow"** (nút xanh bên phải)
5. Nhập số ngày: `30` (test với 30 ngày trước)
6. Click **"Run workflow"**
7. Đợi 5-10 phút để workflow chạy

### 5.3 Kiểm Tra Kết Quả
1. Vào Supabase dashboard
2. Click **Table Editor** → `lottery_draws`
3. Bạn sẽ thấy ~30 records mới

### 5.4 Test Prediction
1. Trong GitHub Actions, click workflow **"Generate Predictions"**
2. Click **"Run workflow"**
3. Đợi 2-3 phút
4. Check Supabase table `predictions` → sẽ có 1 record mới

### 5.5 Test Telegram Notification
1. Trong GitHub Actions, click workflow **"Send Telegram Notifications"**
2. Click **"Run workflow"**
3. Check Telegram app → bạn sẽ nhận được message từ bot!

✅ **Hoàn thành Setup!**

---

## Bước 6: Enable Tự Động (5 phút)

Sau khi test thành công, hệ thống sẽ tự động chạy:
- **19:00 mỗi ngày**: Crawl kết quả mới
- **19:30 mỗi ngày**: Đánh giá predictions hôm qua
- **20:00 mỗi ngày**: Tạo predictions cho ngày mai
- **Sau prediction**: Gửi Telegram notification

Không cần làm gì thêm, chỉ đợi nhận notification hàng ngày!

---

## ❓ Troubleshooting

### Lỗi: "Workflow failed"
- Check **Actions** tab → Click vào workflow failed → Xem logs
- Thường do: sai secrets hoặc website nguồn thay đổi cấu trúc

### Không nhận Telegram message
- Kiểm tra lại TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID
- Đảm bảo đã click "Start" bot trong Telegram

### Supabase "No rows returned"
- Có thể website nguồn chưa có kết quả cho ngày đó
- Thử chạy lại với ngày khác

---

## 📞 Cần Giúp Đỡ?

Nếu gặp lỗi ở bất kỳ bước nào, hãy:
1. Copy error message
2. Cho tôi biết bạn đang ở bước nào
3. Tôi sẽ giúp debug!
