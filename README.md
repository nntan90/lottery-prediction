# 🎲 Lottery Prediction System (V2)

**Hệ thống dự đoán xổ số tự động sử dụng Machine Learning (LSTM), Crawling và Telegram Bot.**

> ⚠️ **EDUCATIONAL PURPOSE ONLY**: Dự án này chỉ nhằm mục đích học tập về Data Analysis, Automation và ML. Kết quả hoàn toàn ngẫu nhiên và không có giá trị cá cược.

---

## ✨ Tính Năng Chính
- 🤖 **Daily Crawling**: Tự động thu thập kết quả XSMB và XSMN (21 tỉnh) hàng ngày (19:00).
- 🧠 **Smart Prediction**: Sử dụng mô hình **LSTM (Long Short-Term Memory)** để học chuỗi số lịch sử.
  - **XSMB**: 1 model chung.
  - **XSMN**: 21 model riêng biệt cho từng tỉnh.
- 🔄 **Closed-Loop System**:
  - Tự động kiểm tra kết quả dự đoán hôm trước.
  - Tự động train lại model nếu hiệu suất giảm (hoặc định kỳ).
- 📱 **Telegram Notifications**: Gửi dự đoán chi tiết và kết quả verify về điện thoại (07:00 & 16:30).
- ☁️ **Serverless**: Chạy hoàn toàn trên **GitHub Actions** và **Supabase** (Free Tier).

---

## 🏗️ Kiến Trúc Hệ Thống

```mermaid
graph TD
    subgraph GitHub Actions
        Crawl[Daily Crawl (16:30)] -->|Insert Raw Data| DB
        Verify[Verify & Retrain (16:40)] -->|Check Yesterday| DB
        Predict[Generate Predictions (17:00)] -->|Load Model| Storage
        Notify[Telegram Bot (07:00)] -->|Fetch Prediction| DB
    end

    subgraph Supabase
        DB[(Database)]
        Storage[[Model Storage]]
    end

    DB <--> Verify
    DB --> Predict
    Verify -->|Trigger Retrain| Actions[Train Model Workflow]
    Actions -->|Save .h5| Storage
    Actions -->|Log Metadata| DB
```

---

## 🚀 Hướng Dẫn Cài Đặt (Setup Guide)

### 1. Chuẩn bị Supabase
1. Tạo project tại [supabase.com](https://supabase.com).
2. Vào **SQL Editor**, chạy file `database/schema_final.sql` để tạo toàn bộ bảng.
3. Vào **Storage**, tạo 1 public bucket tên `lottery-models`.
4. Vào **Settings → API**, lấy `Project URL` và `service_role key`.

### 2. Chuẩn bị Telegram Bot
1. Chat với `@BotFather` trên Telegram, gửi `/newbot`.
2. Lấy **Bot Token**.
3. Chat với bot vừa tạo (`/start`).
4. Lấy **Chat ID** của bạn (dùng tool như `@userinfobot` hoặc gọi API).

### 3. Cài đặt GitHub Repository
1. Fork/Clone repo này.
2. Vào **Settings → Secrets and variables → Actions**, thêm 4 secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

### 4. Khởi tạo Dữ liệu (Backfill)
1. Vào tab **Actions** trên GitHub.
2. Chạy workflow **"05 - Initial Data Backfill"**:
   - Chọn `BOTH` (XSMB + XSMN).
   - Số ngày: `365` (1 năm dữ liệu).
3. Đợi workflow chạy xong (~15 phút).

---

## 🕹️ Vận Hành (Workflows)

Hệ thống hoạt động hoàn toàn tự động theo lịch trình (giờ VN):

| Thời gian | Workflow | Chức năng |
|---|---|---|
| **16:30** | `01-daily-crawl.yml` | Crawl KQXS mới nhất từ `minhngoc.net.vn`. |
| **17:00** | `02-predict.yml` | Chạy model LSTM dự đoán cho ngày mai. |
| **04:00 (Ngày 1)** | `06-monthly-cleanup.yml` | Xóa các model cũ không dùng để tiết kiệm bộ nhớ. |
| **07:00** | `04-notify.yml` | Gửi tin nhắn tổng hợp dự đoán cho ngày mới. |
| **Manual** | `05-train-model.yml` | Train lại model thủ công nếu cần. |

---

## 📁 Cấu Trúc Dự Án

```
lottery-prediction/
├── .github/workflows/       # Automated workflows
├── database/
│   ├── schema_final.sql     # Database schema (Master)
│   ├── analyze_db_size.sql  # Tool: Check dung lượng
│   └── check_model_status.sql # Tool: Check model usage
├── src/
│   ├── bot/                 # Telegram integration
│   ├── crawler/             # Scrapy/Bsoup crawlers
│   ├── database/            # Supabase client wrapper
│   ├── models/              # LSTM implementation (TensorFlow)
│   ├── scripts/             # Entry points (predict, train, cleanup)
│   └── utils/               # Storage & Helpers
└── README.md                # Documentation
```

---

## 🛠️ Troubleshooting & Tools

### Kiểm tra dung lượng & Models
- Chạy script SQL `database/analyze_db_size.sql` trong Supabase để xem dung lượng các bảng.
- Chạy script SQL `database/check_model_status.sql` để xem model nào đang active/inactive.
- Script Python `src/scripts/cleanup_models.py` dùng để xóa model rác (đã tích hợp vào workflow).

### Reset Dữ liệu?
Nếu muốn làm lại từ đầu:
1. Vào Supabase **SQL Editor**, chạy `TRUNCATE lottery_draws, predictions, model_training_logs CASCADE;`.
2. Xóa hết file trong bucket `lottery-models`.
3. Chạy lại workflow **Backfill**.

---

## 📜 License
MIT License.
