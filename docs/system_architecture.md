# 📑 Đặc Tả Phần Mềm & Kiến Trúc Hệ Thống (System Architecture & Specification)

**Dự án:** Analysis Lottery (VietlottAI)  
**Phiên bản:** 3.1  
**Cập nhật lần cuối:** 2026-05-08  
**Tài liệu:** Tài liệu mô tả kiến trúc tổng thể, đặc tả chức năng phần mềm và luồng dữ liệu (Data Flow) dành cho Developer & AI Agents.

---

## 1. Tổng Quan Hệ Thống (System Overview)

**Analysis Lottery** là một hệ thống tự động hóa hoàn toàn (Cron-job based) chạy trên nền tảng Serverless CI/CD. Hệ thống thu thập dữ liệu Xổ số kiến thiết (XSMB, XSMN) mỗi ngày, áp dụng các thuật toán phân tích kỹ thuật và Machine Learning để xếp hạng Top 3 tín hiệu thống kê cho cặp 2 số cuối trong ngày tiếp theo, sau đó phân phối thông báo qua Telegram.

### 1.1. Mục Tiêu Cốt Lõi (Core Objectives)
- **Tự động hóa 100%:** Không cần can thiệp thủ công từ quá trình crawl đến phân phối kết quả.
- **Xếp hạng bằng AI/ML:** Sử dụng ensemble để xếp hạng xác suất tương đối, đánh giá bằng Hit@1, Hit@3, lift so với random baseline và ROI.
- **Zero-Cost Infrastructure:** Triển khai qua GitHub Actions (Compute), Supabase (Database), Telegram (Messaging).

---

## 2. Kiến Trúc Tổng Thể (Architecture Diagram)

Hệ thống được thiết kế theo kiến trúc **Event-Driven Micro-Pipelines**, chia làm 4 công đoạn độc lập giao tiếp qua cơ sở dữ liệu trung tâm.

```mermaid
graph TD
    subgraph Trigger [Cloud Automation]
        GA1[GH Action: Daily Crawl]
        GA2[GH Action: Evaluate]
        GA3[GH Action: Predict Ensemble (XSMB & XSMN)]
        GA4[GH Action: Train Model]
        GA5[GH Action: Notify]
    end

    subgraph DataIngestion [Crawl Layer]
        CR[Web Crawler / BeautifulSoup]
    end

    subgraph CoreDB [Supabase - PostgreSQL DB]
        DB_RAW[(lottery_draws & tails_2d)]
        DB_FEAT[(pair_features)]
        DB_PRED[(prediction_results)]
        DB_MP[(model_predictions)]
        DB_META[(model_registry & training_queue)]
    end

    subgraph ML_Ensemble [Multi-Model Ensemble Layer v3.2]
        RESOLVE[Province Resolver DOW]
        MA[Model A: Frequency/Hot-Cool]
        MB[Model B: Gap/Overdue]
        MC[Model C: Markov Chain]
        MD[Model D: XGBoost]
        ME[Model E: LSTM]
        BORDA[Ensemble Engine: Borda + CombSUM]
    end

    subgraph Delivery [Notification Layer]
        BOT[Telegram Bot API]
        USER((End Users))
    end

    %% Luồng chạy Crawler
    GA1 --> CR
    CR -->|Lưu KQXS & Cắt 2 số| DB_RAW

    %% Luồng Evaluate
    GA2 -->|Kiểm tra KQ hôm nay & Update Hit Rate| DB_PRED

    %% Luồng Predict Ensemble
    GA3 --> RESOLVE
    RESOLVE -->|2-4 đài/ngày| MA & MB & MC & MD & ME
    DB_RAW -->|Lookback theo kỳ| MA & MB & MC
    DB_FEAT -->|Features on-the-fly| MD & ME
    MA & MB & MC & MD & ME -->|Top 5 mỗi model| BORDA
    BORDA -->|Top 3 Ensemble| DB_PRED
    BORDA -->|Log sub-model| DB_MP

    %% Luồng Notify
    GA5 --> BOT
    DB_PRED -->|Đọc Top 3 tín hiệu ngày mai| BOT
    BOT -->|Gửi Message| USER
```

---

## 3. Đặc Tả Các Phân Hệ (Module Specifications)

### 3.1. Phân hệ Data Ingestion (Crawler)
- **Component:** `src/crawler/`
- **Mô tả:** Chịu trách nhiệm fetch HTML từ trang web nguồn, parse HTML DOM, bóc tách chuỗi để lấy ra cấu trúc giải (Giải Đặc Biệt -> Giải 8).
- **Luồng xử lý:**
  1. Yêu cầu request với random User-Agent để tránh block.
  2. Parse DOM dùng `BeautifulSoup`.
  3. Insert vào bảng `lottery_draws` (Raw data).
  4. Flatten dữ liệu để lấy 2 số cuối (00-99) của từng giải và insert vào `tails_2d` (Sử dụng cho label ML).

### 3.2. Phân hệ Feature Engineering & Data Prep
- **Component:** `src/features/feature_builder.py`
- **Mô tả:** Biến đổi dữ liệu Raw thành ma trận features (X) cho ML. Hiện hỗ trợ **17 features** chia thành v1 (cổ điển) và v2 (tín hiệu ngắn hạn).
- **Features v1 (11 features — cổ điển):**
  - `freq_30`, `freq_60`, `freq_100`: Tần suất xuất hiện trong N kỳ gần nhất.
  - `gap_since_last`: Số kỳ từ lần xuất hiện cuối cùng (Gan).
  - `avg_gap_100`, `std_gap_100`, `gap_zscore`: Thống kê & chuẩn hóa độ trễ.
  - `is_even`, `is_high`, `sum_digits`: Đặc trưng toán học.
  - `day_of_week`: Thứ trong tuần (0=T2..6=CN) — phục vụ weekday models.
- **Features v2 (6 features mới — tín hiệu ngắn hạn):**
  - `freq_7`: Tần suất 7 kỳ gần nhất (nóng/lạnh ngắn hạn).
  - `consecutive_miss`: Số kỳ liên tiếp chưa xuất hiện (momentum âm).
  - `is_hot_3`: TRUE nếu xuất hiện trong cả 3 kỳ liên tiếp gần nhất.
  - `decade_freq_30`: Tần suất nhóm thập phân (00-09, 10-19...) trong 30 kỳ.
  - `mirror_freq_30`: Tần suất cặp đảo số (23↔32) trong 30 kỳ.
  - `month_freq`: Tần suất trong cùng tháng toàn lịch sử.
- **DB Migration:** `database/migrations/05_add_new_features_to_pair_features.sql`
- **Output:** Upsert 100 rows/ngày/station vào bảng `pair_features`.

### 3.3. Phân hệ Machine Learning — Đào tạo mô hình (XGBoost)
- **Component:** `src/models/`, `src/scripts/train_xgb.py`
- **Mô tả:** Vòng đời model XGBoost với hỗ trợ **Weekday Split**.
- **Weekday Model Split:** Mỗi ngày trong tuần có 1 model riêng (7 models/weekday) vì pattern số có thể khác nhau theo thứ. Model legacy (weekday=NULL) dùng làm fallback nếu chưa có model weekday-specific.
  - **Training (`train_xgb.py`):** Nhận `--weekday 0..6` → filter `day_of_week` khi load data → lưu vào `models/region/wd{N}/` trên Storage. Hỗ trợ hyperparameter injection qua CLI args.
  - **DB Schema:** `model_registry.weekday` (INT, NULL=legacy). Migration: `02_add_weekday_to_model_registry.sql`.

### 3.4. Phân hệ Multi-Model Ensemble (v3.2)
- **Component:** `src/xsmn_ensemble/`
- **Scope:** Áp dụng chung cho cả XSMB và XSMN.
- **Đặc thù Lookback:** XSMN mỗi tỉnh chỉ xổ 1 lần/tuần → Lookback theo **số kỳ quay** (LIMIT N), KHÔNG theo số ngày.
- **5 Sub-Models chạy song song:**
  - **Model A — Frequency/Hot-Cool (`model_frequency.py`):** Rule-based scoring dựa trên tần suất (30/60/100 kỳ) + hot streak.
  - **Model B — Gap/Overdue (`model_gap.py`):** Rule-based dựa trên gan cực đại và z-score.
  - **Model C — Markov Chain (`model_markov.py`):** Xây dựng ma trận chuyển 100×100.
  - **Model D — XGBoost (`model_xgboost.py`):** Cây quyết định Gradient Boosting (v3 core).
  - **Model E — LSTM/GRU (`model_lstm.py`):** Mạng nơ-ron hồi quy cho chuỗi thời gian.
- **Ensemble Engine (`ensemble_engine.py`):**
  - Phương pháp: **Borda Count + CombSUM** (Rank & Probability).
  - Consensus bonus: Khi ≥2/3 model cùng chọn 1 cặp.
  - Fault tolerant: model lỗi → các model còn lại vẫn chạy và bù đắp kết quả.
- **Province Resolution (`resolve_provinces.py`):**
  - Hỗ trợ env var `TARGET_PROVINCES` override từ GH Actions.
  - Fallback: schedule tĩnh theo DOW (XSMB: 1 đài/ngày, XSMN: 2-4 đài/ngày).
- **Output:** Top 3 tín hiệu mỗi đài → `prediction_results` + ensemble metadata + log sub-model → `model_predictions`.
- **Gửi Telegram:** Lúc **07:00 VN** với label `[Ensemble v3.2]`.

### 3.5. Phân hệ Walk-forward Backtest
- **Component:** `src/analytics/backtest.py`, `src/scripts/backtest_walk_forward.py`
- **Mô tả:** Đọc `prediction_results` đã verify như dữ liệu out-of-time để đánh giá hiệu năng thật, không train lại trên tương lai.
- **Metrics:** Hit@1, Hit@3, random baseline theo kích thước `tail_set`, lift, ROI từ `profit_tracking`, rolling 7/30/90 kỳ và model contribution từ `model_predictions`.
- **Nguyên tắc promotion:** Chỉ tăng trọng số hoặc promote model khi lift so với random baseline ổn định trên cửa sổ out-of-time đủ dài.

### 3.6. Phân hệ Master Retrain Agent 🆕
- **Component:** `src/agent/`
- **Mô tả:** Agent tự động quyết định và kích hoạt retrain model sau mỗi kỳ verify. Chạy ngay sau `verify_v3.py` trong cùng pipeline (không cần workflow riêng).
- **Luồng xử lý:**
  1. Nhận danh sách kết quả verify (hit/miss) từ `verify_v3.py`.
  2. **Decision Engine (`decision_engine.py`):** Phân tích per-station theo 4 bước:
     - Bước 1: Hôm nay trúng → `no_action`.
     - Bước 2: Đọc metric model active (AUC, hit_rate) từ `model_registry`.
     - Bước 3: Nếu metric vẫn OK (AUC ≥ 0.55, hit_rate ≥ 40%) → `skipped`.
     - Bước 4: Cooldown 14 ngày (per-weekday) → `skipped` nếu đã retrain gần đây.
     - Bước 5-6: Đếm consecutive fails + lịch sử AUC improvement → chọn strategy.
  3. **Hyperparameter Strategy (`hyperparameter_strategy.py`):** 4 chiến lược leo thang:
     - `boost_estimators`: 1-4 kỳ fail → tăng n_estimators (300→500), giảm lr.
     - `conservative`: 5-6 kỳ fail → regularization mạnh (max_depth 4→3).
     - `scale_weight`: AUC ~0.5 tái diễn → scale_pos_weight=3.2 (xử lý class imbalance).
     - `full_reset`: 7+ kỳ fail HOẶC ≥3 lần retrain không cải thiện → reset về defaults + `--force`.
  4. Trigger `train_xgb.py` qua subprocess với hyperparameters mới.
  5. Ghi log vào `agent_actions` table + gửi Telegram report.
- **Phạm vi:** Áp dụng cho cả XSMB weekday models lẫn XSMN province models.

### 3.7. Phân hệ Thông Báo (Telegram Bot)
- **Component:** `src/bot/telegram_bot.py`
- **Mô tả:** Async Telegram bot dùng Bot API, hỗ trợ Mock Mode khi thiếu credentials.
- **Luồng:** Format HTML template với Top 3 tín hiệu thống kê, score tương đối và trạng thái model. Gửi qua `chat_id` cấu hình trong Secrets.
- **Config DB:** `notification_configs` điều khiển bật/tắt theo `config_key`, override `chat_id`, `parse_mode`, prefix/suffix và lưu metadata `workflow_file`, `schedule_cron_utc`, `schedule_local_time` để dễ quản trị cron/job.
- **Lịch gửi:**
  - **07:00 VN** — `predict_ensemble.py` gửi Top 3 tín hiệu XSMB & XSMN `[Ensemble v3.2]`.
  - **19:30 VN** — `verify_v3.py` gửi kết quả đúng/sai + Agent report (nếu có retrain).

---

## 4. Đặc Tả Cơ Sở Dữ Liệu (Database Specification V3 + V3.1)

Hệ thống được tổ chức chuẩn hóa, tận dụng Row Level Security (RLS) của Supabase để bảo mật. (Sơ đồ lược giản)

| Bảng | Chức năng | Khóa chính/Ràng buộc Unique |
|---|---|---|
| **lottery_draws** | Lưu trữ toàn bộ giải thưởng cho từng ngày quay của mỗi tỉnh/vùng. | `UNIQUE(draw_date, region, province)` |
| **tails_2d** | Tách riêng mảng 2 số đuôi ứng với `draw_id`. Tối ưu query tìm số. | `(draw_date, region, tail_2d)` INDEXED |
| **pair_features** | State cache cho 17 features ML (v1+v2). 100 cặp số / 1 ngày. | `UNIQUE(feature_date, region, province, pair)` |
| **model_registry** | Versioning model + cột `weekday` (INT, NULL=legacy) hỗ trợ weekday split. | `(region, province, weekday, status)` |
| **prediction_results** | Top 3 tín hiệu + ensemble metadata (method, contributing_models, final_scores, hit). | `UNIQUE(prediction_date, region, province)` |
| **model_predictions** 🆕 | Log Top 5 output từ mỗi sub-model trong XSMN ensemble pipeline. | `UNIQUE(prediction_date, region, province, model_name)` |
| **agent_actions** 🆕 | Lịch sử quyết định của Master Retrain Agent (retrain/skip/no_action). | `(action_date, region, province, weekday)` |
| **profit_tracking** 🆕 | Theo dõi lãi/lỗ thực tế per pair per station per ngày. | `UNIQUE(prediction_date, region, province, pair)` |
| **training_queue** | Queue trigger việc retrain (manual/perf_drop/new_data). | `status` ('pending'/'triggered'/'done') |
| **notification_configs** 🆕 | Config Telegram theo cron/job: enabled, chat_id, parse_mode, schedule metadata. | `UNIQUE(config_key)` |

---

## 5. Deployment & CI/CD Strategy

Hệ thống không chạy ứng dụng dạng Long-lived (ví dụ Flask/FastAPI), mà chạy dạng **Task-based (Cron-Jobs)**.

**Các Job thiết lập trong `.github/workflows/`**:

| Workflow | Giờ VN | UTC | Chức năng |
|---|---|---|---|
| `01-daily-crawl.yml` | 19:00 hàng ngày | 12:00 | Crawl KQXS XSMB + XSMN, lưu vào `lottery_draws` & `tails_2d` |
| `02-predict-ensemble.yml` | 07:00 hàng ngày | 00:00 | Multi-Model Ensemble predict XSMB & XSMN, gửi Telegram `[Ensemble v3.2]` |
| `03-verify-predictions.yml` | 19:30 hàng ngày | 12:30 | Verify hit/miss, profit tracking, kích hoạt Master Retrain Agent |
| `04-check-training.yml` | 21:00 Chủ Nhật | 14:00 CN | Kiểm tra điều kiện retrain định kỳ (new_data/perf_drop/manual) |
| `05-train-model.yml` | Trigger thủ công | — | Train/retrain XGBoost, hỗ trợ `--weekday`, `--region`, hyperparams |
| `06-query-report.yml` | Trigger thủ công | — | Query báo cáo từ DB |
| `11-backtest.yml` | Trigger thủ công | — | Walk-forward backtest: Hit@1, Hit@3, lift random baseline, ROI, model contribution |

### 5.1. Môi trường Thực Thi (Environment Variables)
- `SUPABASE_URL`: Endpoint kết nối Supabase Postgres API & Storage.
- `SUPABASE_SERVICE_KEY`: Key quản trị bypassing RLS (Do chạy từ Server Backend).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Credentials mặc định cho thông báo. `notification_configs.chat_id` có thể override per job.

---

## 6. Yêu Cầu Phi Chức Năng (Non-Functional Requirements)

- **Fault Tolerance:** Crawler nếu rớt (do Cloudflare chặn hoặc mạng chậm) sẽ tự retry, hoặc cron job ghi lỗi vào bảng `crawler_logs` để dễ debug. Pipeline phải cho phép backfill (chạy bù) thông qua script manual mà không phá vỡ tính toàn vẹn dữ liệu. Ensemble chịu lỗi từng model, nhưng workflow fail-fast nếu tất cả model của một region không tạo được candidate.
- **Scalability:** Hệ thống có thể scale lên 63 đài (các tỉnh miền Nam và miền Trung) do cấu trúc Database đã thiết kế hỗ trợ Column `region` và `province`. XSMN ensemble hiện cover 12 tỉnh/thành (2 đài/ngày × 7 ngày).
- **Security:** Model artifacts (file .pkl) được lưu trên Storage private, chỉ truy xuất qua IAM / Service Role. Data DB được bảo mật 100% bằng Supabase RLS.

---
*(Tài liệu này nằm trong bộ đặc tả chuẩn GitNexus để định hướng phát triển các module ML và Backend trong tương lai.)*
