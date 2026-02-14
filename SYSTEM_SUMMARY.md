# Lottery Prediction System - Summary

## 🎯 System Overview
Hệ thống dự đoán xổ số tự động với ML, crawl data hàng ngày, và gửi predictions qua Telegram.

## 📊 Current Data Status
- **XSMB**: 767 records (2024-01-01 → 2026-02-13)
- **XSMN**: 16,294 records (2024-01-01 → 2026-02-14)
- **Total**: **17,061 lottery records** ✅

## 🤖 ML Model Strategy
### XSMB (Miền Bắc)
- **1 model** cho toàn bộ XSMB
- Train với 90 ngày data gần nhất
- Predict số Đặc Biệt (5 chữ số)

### XSMN (Miền Nam)
- **21 models riêng biệt** - mỗi tỉnh 1 model
- Mỗi model train với data riêng của tỉnh đó
- Predict số Đặc Biệt (6 chữ số)
- Provinces: TP.HCM, Đồng Tháp, Cà Mau, Bến Tre, Vũng Tàu, Bạc Liêu, Đồng Nai, Cần Thơ, Sóc Trăng, Tây Ninh, An Giang, Bình Thuận, Vĩnh Long, Bình Dương, Trà Vinh, Long An, Bình Phước, Hậu Giang, Tiền Giang, Kiên Giang, Đà Lạt

## 🔄 Daily Workflows (GitHub Actions)

### 1. Daily Crawl (19:00 GMT+7)
**File**: `.github/workflows/01-daily-crawl.yml`
- Crawl XSMB từ xskt.com.vn
- Crawl XSMN (21 tỉnh) từ xskt.com.vn
- Lưu vào Supabase

### 2. Generate Predictions (20:00 GMT+7)
**File**: `.github/workflows/02-predict.yml`
- **XSMB**: Train 1 model, generate 1 prediction
- **XSMN**: Train 21 models, generate 21 predictions (1 per province)
- Lưu predictions vào database

### 3. Evaluate Predictions (19:30 GMT+7)
**File**: `.github/workflows/03-evaluate.yml`
- So sánh predictions với actual results
- Tính accuracy cho từng prediction
- Lưu metrics vào database

### 4. Send Telegram Notifications (07:00 GMT+7) ⭐ NEW
**File**: `.github/workflows/04-notify.yml`
- Gửi XSMB prediction
- Gửi tất cả 21 XSMN predictions trong 1 message
- Format đẹp với HTML

## 🔑 Required GitHub Secrets
Bạn cần set 4 secrets trong GitHub repository:

1. **SUPABASE_URL**: `https://islcxaqdqhwgcqkdozeq.supabase.co`
2. **SUPABASE_SERVICE_KEY**: (service_role key từ Supabase)
3. **TELEGRAM_BOT_TOKEN**: (từ @BotFather)
4. **TELEGRAM_CHAT_ID**: (chat ID để nhận notifications)

## 📁 Database Schema

### Table: `lottery_draws`
```sql
- draw_date (DATE)
- region (VARCHAR) - 'XSMB' or 'XSMN'
- province (VARCHAR) - NULL for XSMB, province code for XSMN
- special_prize (VARCHAR)
- first_prize, second_prize, ... (ARRAY)
- UNIQUE(draw_date, region, province)
```

### Table: `predictions`
```sql
- prediction_date (DATE)
- region (VARCHAR)
- province (VARCHAR) - NULL for XSMB, province code for XSMN
- model_version (VARCHAR)
- predicted_numbers (JSONB)
- confidence_score (FLOAT)
- UNIQUE(prediction_date, region, province, model_version)
```

### Table: `evaluation_metrics`
```sql
- evaluation_date (DATE)
- region (VARCHAR)
- accuracy_rate (FLOAT)
- correct_predictions (INT)
- total_predictions (INT)
- model_version (VARCHAR)
```

## 🗄️ Database Migrations

### Migration 1: Add province to lottery_draws
**File**: `database/migration_add_province.sql`
- ✅ **COMPLETED**

### Migration 2: Add province to predictions
**File**: `database/migration_add_province_to_predictions.sql`
- ✅ **COMPLETED**

## 🚀 Next Steps

1. **Test Telegram Bot**
   - Set TELEGRAM_BOT_TOKEN và TELEGRAM_CHAT_ID secrets
   - Run workflow 04-notify manually để test

2. **Monitor Workflows**
   - Check daily crawl results
   - Monitor prediction accuracy
   - Adjust model parameters if needed

3. **Improve ML Model** (Future)
   - Implement LSTM model
   - Add more features (day of week, holidays, etc.)
   - Ensemble multiple models

## 📝 Important Files

### Crawlers
- `src/crawler/xsmb_crawler.py` - XSMB crawler
- `src/crawler/xsmn_crawler.py` - XSMN crawler
- `import_xsmb_2024.py` - Historical XSMB import
- `import_xsmn_minhngoc.py` - Historical XSMN import

### ML Models
- `src/models/frequency_analyzer.py` - Frequency-based prediction model

### Database
- `src/database/supabase_client.py` - All database operations

### Telegram Bot
- `src/bot/telegram_bot.py` - Telegram notification handler

## 🎨 Telegram Message Format

### XSMB Example:
```
🎯 DỰ ĐOÁN XSMB - 14/02/2026

🔮 Số dự đoán: 12345
📊 Độ tin cậy: 28%
🔥 Số nóng: 12, 34, 56, 78, 90

Model: frequency_v1
```

### XSMN Example:
```
🎯 DỰ ĐOÁN XSMN - 14/02/2026

📍 TP.HCM: 123456 (30%)
📍 Đồng Tháp: 234567 (25%)
📍 Cà Mau: 345678 (28%)
...
(21 tỉnh)

Tổng: 21/21 tỉnh
```

## ⚠️ Important Notes

1. **Province-Specific Training**: XSMN models MUST be trained separately per province
2. **Data Quality**: Ensure crawlers run successfully daily
3. **Rate Limiting**: Crawlers have 1.5-3s delays to avoid blocking
4. **Upsert Logic**: All inserts use upsert to handle duplicates gracefully

## 🔗 Resources

- **GitHub Repo**: https://github.com/nntan90/lottery-prediction
- **Supabase Dashboard**: https://supabase.com/dashboard
- **Data Source**: xskt.com.vn, minhngoc.net.vn
