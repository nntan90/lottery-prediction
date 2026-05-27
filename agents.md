# 🤖 GitNexus Agent Protocols (agents.md)

## 📌 1. System Overview
**Project Name:** Analysis Lottery (lottery-prediction / VietlottAI)
**Domain:** Automated Data Crawling & Machine Learning Prediction
**Core Goal:** Hệ thống cron-job tự động cào kết quả Xổ số (XSMB, XSMN) mỗi ngày, thực hiện trích xuất dữ liệu, huấn luyện/sử dụng model để xếp hạng Top 3 tín hiệu thống kê cho cặp 2 số cuối, lưu trữ lịch sử và gửi thông báo qua Telegram.

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
3. **Model Prediction (`src/models`):** Load model version tốt nhất từ Storage / bảng `model_registry` -> chấm điểm tương đối 100 cặp -> Chọn Top 3 tín hiệu -> Lưu vào `prediction_results`.
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
- `notification_configs`: Config gửi Telegram theo job/cron (`enabled`, `chat_id`, `parse_mode`, schedule metadata).

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

## 🎯 6. Multi-Model Ensemble (v3.2)
> **Scope**: Áp dụng cho cả XSMB và XSMN.

**⚠️ Quy tắc Lookback XSMN — QUAN TRỌNG:**
- XSMN mỗi tỉnh chỉ xổ 1 lần/tuần (~156 kỳ/3 năm vs 1,095 kỳ XSMB).
- Lookback bằng **số kỳ quay** (LIMIT N), KHÔNG bằng số ngày.
- Query: `WHERE province = ? ORDER BY draw_date DESC LIMIT 100`.

**5 Sub-Models chạy song song:**
- **Model A (frequency):** Rule-based hot/cool.
- **Model B (gap):** Rule-based overdue (gan).
- **Model C (markov):** Markov Chain transition matrix.
- **Model D (xgboost):** XGBoost classifier.
- **Model E (lstm):** LSTM / GRU deep learning model.

**Ensemble:** Borda Count kết hợp CombSUM. Consensus bonus khi nhiều model đồng thuận.

**Workflow:** `02-predict-ensemble.yml` — Chạy 07:00 VN hằng ngày.
**Orchestration:** `src/scripts/predict_ensemble.py` — Fault tolerant (model lỗi → các model còn lại vẫn chạy và bù đắp kết quả).

## 🎯 7. XSMB Multi-Model Ensemble (v4.0)
> **Scope**: Áp dụng riêng cho XSMB (tách biệt hoàn toàn với XSMN để tối ưu hóa dữ liệu daily).

**7 Sub-Models chạy song song:**
- **Model A (frequency):** Rule-based hot/cool với multi-window (3/7/14/30/60 kỳ) và same-weekday frequency.
- **Model B (gap):** Gap/overdue model với weekday-specific gap stats và percentile.
- **Model C (markov):** Second-order Markov Chain transition matrix (100x100 state space nén) + weekday-conditioned.
- **Model D (xgboost):** XGBoost classifier nâng cấp lên 25 features (thêm 8 features mới).
- **Model E (lstm):** Bi-directional LSTM/GRU sequence model (lookback 60 kỳ) kết hợp Attention.
- **Model F (bayesian):** Bayesian Network posterior scorer + confidence calibration (entropy-based).
- **Model G (cyclic):** FFT / Autocorrelation chu kỳ tuần hoàn lặp lại của cặp số.

**Ensemble & Weight Tuning:**
- **Adaptive Weighted Borda:** Borda Count kết hợp confidence/entropy calibration của model Bayesian và auto-weights từ backtest.
- **Auto-Weight Tuning:** Tự động điều chỉnh weight của 7 models dựa trên rolling backtest performance (30 ngày gần nhất) qua `src/scripts/tune_weights.py`.

