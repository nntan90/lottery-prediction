# 🤖 GitNexus Agent Protocols (agents.md)

## 📌 1. System Overview
**Project Name:** Analysis Lottery (lottery-prediction / VietlottAI)
**Domain:** Automated Data Crawling & Machine Learning Prediction
**Core Goal:** Hệ thống cron-job tự động cào kết quả Xổ số (XSMB, XSMN) mỗi ngày, thực hiện trích xuất dữ liệu, huấn luyện/sử dụng model XGBoost để dự đoán 3 cặp số ngẫu nhiên khả năng cao, lưu trữ lịch sử và gửi thông báo qua Telegram.

## 🏗️ 2. Architecture & Tech Stack
**Stack:**
- **Language:** Python >= 3.9
- **Database:** Supabase (PostgreSQL)
- **Machine Learning:** XGBoost, Scikit-learn, Pandas, Numpy
- **Automation/CI-CD:** GitHub Actions (daily workflows)
- **Web Scraping:** BeautifulSoup4, lxml, Requests
- **Notification:** Telegram Bot API

**Data Flow:**
1. **Daily Crawl (`src/crawler`):** Tự động cào kết quả xổ số mỗi ngày -> parse và lưu thành raw data vào `lottery_draws`, cắt 2 số cuối đưa vào `tails_2d`.
2. **Feature Engineering (`src/features`):** Từ raw data tạo các metrics (frequency 30/60/100, gap_since_last, is_even...) cho cặp 00-99 -> lưu vào `pair_features`.
3. **Model Prediction (`src/models`):** Load model version tốt nhất từ Storage / bảng `model_registry` -> Dự đoán xác suất 100 cặp -> Chọn Top 3 -> Lưu vào `prediction_results`.
4. **Evaluation:** Xác minh lại dự đoán ngày hôm trước dựa trên kết quả đã có.
5. **Notification (`src/bot`):** Bắn tin nhắn qua Telegram Bot.

## 📂 3. Repository Structure
```
.
├── .github/workflows/   # Các pipeline GitHub Actions (Crawl, Evaluate, Predict, Train)
├── database/            # File SQL định nghĩa Schema, Migrations (.sql)
├── src/
│   ├── crawler/         # Cào kết quả xổ số (Minh Ngọc, v.v)
│   ├── features/        # Feature builder/engineering pipeline
│   ├── models/          # XGBoost train/predict wrapper (XSMB)
│   ├── xsmn_ensemble/   # 🆕 v3.1 — Multi-Model Ensemble cho XSMN
│   │   ├── resolve_provinces.py   # Dynamic province resolution theo DOW
│   │   ├── model_freq_gap.py      # Model A: Freq/Gap rule-based scorer
│   │   ├── model_markov.py        # Model B: Markov Chain transition
│   │   ├── model_xgboost.py       # Model C: XGBoost wrapper
│   │   └── ensemble_engine.py     # Weighted Borda Count aggregation
│   ├── database/        # Client kết nối Supabase (CRUD)
│   ├── bot/             # Telegram messaging
│   ├── agent/           # Master Retrain Agent
│   └── utils/           # Helper & config
├── requirements.txt     # Thư viện core
└── README.md            # Thông tin khởi chạy nhanh
```

## 🗄️ 4. Database Schema Structure (V3 + V3.1)
- `lottery_draws`: Dữ liệu gốc các giải thưởng hằng ngày.
- `tails_2d`: Flatten 2 số cuối từng giải (từ giải ĐB -> G8).
- `pair_features`: Vector đặc trưng (freq, gap, stats) cho 100 cặp số phục vụ XGBoost.
- `model_registry`: Track các phiên bản model (.pkl) + ensemble metadata (model_name, weight, is_ensemble_member).
- `prediction_results`: Kết quả top 3 mỗi ngày / mỗi đài + ensemble metadata (ensemble_method, contributing_models, final_scores).
- `model_predictions`: 🆕 v3.1 — Log Top 5 output từ mỗi sub-model trong ensemble pipeline.
- `training_queue`: Quản lý yêu cầu train lại model tự động nếu perf rớt.
- `crawler_logs`: Lưu log crawler để tracking lỗi rớt trang.

## ⚠️ 5. GitNexus Requirements & Rules
> **Dành cho Agent mới: Hãy đọc thật kỹ trước khi tạo bất kỳ thay đổi nào vào hệ thống.**

**1. Impact Analysis First (Nguyên tắc phân tích ảnh hưởng):**
- Dự án lệ thuộc nặng vào GitHub Actions. Nếu thay đổi thư mục `src/`, đường dẫn file hoặc tên hàm script, PHẢI kiểm tra các file `.yml` trong `.github/workflows/` có bị hỏng path / parameters hay không.
- Bất kỳ thay đổi nào liên quan đến cột, bảng trong Database PHẢI tuân thủ luồng: tạo một script `xx_name.sql` mới trong `database/migrations/` VÀ cập nhật lại `database/schema_final.sql`. KHÔNG xóa / sửa trực tiếp cấu trúc cũ chưa qua migration.

**2. State Management & Credentials:**
- TUYỆT ĐỐI không hardcode API keys (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`). Dùng `os.getenv` hoặc thư viện `.env`.
- Storage URLs trỏ tới file mô hình (`.pkl`) lưu trong `model_registry` phải được resolve động thông qua SDK, không dùng hardcoded URL tránh chết link.

**3. Change Detection & Symbol Refactoring:**
- **Code ML (Features/Models):** Nếu thêm một feature mới (vd: `sum_digits`) vào `src/features/feature_builder.py`, phải chắc chắn rằng cấu trúc bảng `pair_features` đã có column tương ứng.
- **Workflow Pipeline:** Quy trình phải tuân thủ nghiêm ngặt 3 bước: _Crawl -> Feature Build -> Predict / Evaluate_. Các bước này phải cô lập, có thể chạy lại an toàn nếu fail ngang.

**4. Code Style:**
- Rõ ràng, có type hint trong Python (vd: `def get_draws(region: str) -> pd.DataFrame:`).
- Document docstrings chi tiết cho các logic tính toán xác suất.
- Không Hallucination: Chỉ gọi các thư viện có sẵn trong `requirements.txt`. Nếu cần package mới, báo người dùng cập nhật hoặc xin permission cài đặt.

## 🎯 6. XSMN Multi-Model Ensemble (v3.1)
> **Scope**: Chỉ XSMN. XSMB pipeline (predict_v3.py, 02-predict.yml) giữ nguyên 100%.

**⚠️ Quy tắc Lookback XSMN — QUAN TRỌNG:**
- XSMN mỗi tỉnh chỉ xổ 1 lần/tuần (~156 kỳ/3 năm vs 1,095 kỳ XSMB).
- Lookback bằng **số kỳ quay** (LIMIT N), KHÔNG bằng số ngày.
- Query: `WHERE province = ? ORDER BY draw_date DESC LIMIT 100`.

**3 Sub-Models chạy song song:**
- **Model A (freq_gap):** Rule-based freq/gap scoring. Weight = 0.25.
- **Model B (markov):** Markov Chain transition matrix. Weight = 0.25.
- **Model C (xgboost_core):** XGBoost classifier, reuse LotteryXGB class. Weight = 0.50.

**Ensemble:** Weighted Borda Count. Consensus bonus x1.5 khi ≥2/3 model đồng ý.

**Workflow:** `07-predict-xsmn-ensemble.yml` — Chạy 20:00 VN 7 ngày/tuần.
**Orchestration:** `src/scripts/predict_xsmn_ensemble.py` — Fault tolerant (1 model lỗi → 2 model còn lại vẫn chạy).
