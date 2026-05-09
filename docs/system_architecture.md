# 📑 Đặc Tả Phần Mềm & Kiến Trúc Hệ Thống (System Architecture & Specification)

**Dự án:** Analysis Lottery (VietlottAI)  
**Phiên bản:** 3.1  
**Cập nhật lần cuối:** 2026-05-08  
**Tài liệu:** Tài liệu mô tả kiến trúc tổng thể, đặc tả chức năng phần mềm và luồng dữ liệu (Data Flow) dành cho Developer & AI Agents.

---

## 1. Tổng Quan Hệ Thống (System Overview)

**Analysis Lottery** là một hệ thống tự động hóa hoàn toàn (Cron-job based) chạy trên nền tảng Serverless CI/CD. Hệ thống làm nhiệm vụ thu thập dữ liệu Xổ số kiến thiết (XSMB, XSMN) mỗi ngày, áp dụng các thuật toán phân tích kỹ thuật và mô hình Machine Learning (XGBoost) để dự đoán Top 3 cặp số có xác suất về cao nhất trong ngày tiếp theo, sau đó tự động phân phối thông báo đến người dùng qua nền tảng Telegram.

### 1.1. Mục Tiêu Cốt Lõi (Core Objectives)
- **Tự động hóa 100%:** Không cần can thiệp thủ công từ quá trình crawl đến phân phối kết quả.
- **Dự đoán bằng AI/ML:** Sử dụng XGBoost classifier để tối ưu xác suất, đánh giá lịch sử (hit rate) qua tính năng backtesting/evaluation.
- **Zero-Cost Infrastructure:** Triển khai qua GitHub Actions (Compute), Supabase (Database), Telegram (Messaging).

---

## 2. Kiến Trúc Tổng Thể (Architecture Diagram)

Hệ thống được thiết kế theo kiến trúc **Event-Driven Micro-Pipelines**, chia làm 4 công đoạn độc lập giao tiếp qua cơ sở dữ liệu trung tâm.

```mermaid
graph TD
    subgraph Trigger [Cloud Automation]
        GA1[GH Action: Daily Crawl]
        GA2[GH Action: Evaluate]
        GA3[GH Action: Predict XSMB]
        GA4[GH Action: Train Model]
        GA5[GH Action: Notify]
        GA7[GH Action: Predict XSMN Ensemble]
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

    subgraph ML_XSMB [XSMB ML Layer]
        FE[Feature Engineering Pipeline]
        TR[XGBoost Trainer]
        PR[XGBoost Predictor]
        STORAGE[Supabase Object Storage: Models .pkl]
    end

    subgraph ML_XSMN [XSMN Ensemble Layer v3.1]
        RESOLVE[Province Resolver DOW]
        MA[Model A: Freq/Gap]
        MB[Model B: Markov Chain]
        MC[Model C: XGBoost]
        BORDA[Ensemble Engine: Weighted Borda Count]
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

    %% Luồng Predict XSMB (Kéo theo tạo Feature)
    GA3 --> FE
    DB_RAW -->|Load Data| FE
    FE -->|Lưu Data ML| DB_FEAT
    DB_FEAT --> PR
    STORAGE -.->|Load .pkl| PR
    DB_META -.->|Fetch active version| PR
    PR -->|Lưu Top 3| DB_PRED

    %% Luồng XSMN Ensemble
    GA7 --> RESOLVE
    RESOLVE -->|2 đài/ngày| MA & MB & MC
    DB_RAW -->|Lookback theo kỳ| MA & MB
    DB_FEAT -->|Features on-the-fly| MC
    STORAGE -.->|Load .pkl| MC
    MA & MB & MC -->|Top 5 mỗi model| BORDA
    BORDA -->|Top 3 Ensemble| DB_PRED
    BORDA -->|Log sub-model| DB_MP

    %% Luồng Train Model
    GA4 --> TR
    DB_FEAT --> TR
    TR -->|Upload .pkl| STORAGE
    TR -->|Update Registry| DB_META

    %% Luồng Notify
    GA5 --> BOT
    DB_PRED -->|Đọc dự đoán ngày mai| BOT
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

### 3.3. Phân hệ Machine Learning — XSMB (XGBoost)
- **Component:** `src/models/`, `src/scripts/train_xgb.py`, `src/scripts/predict_v3.py`
- **Mô tả:** Vòng đời model XGBoost cho XSMB với hỗ trợ **Weekday Split** (v3.1).
- **Weekday Model Split:** Mỗi ngày trong tuần có 1 model riêng (7 models XSMB/weekday) vì pattern số có thể khác nhau theo thứ. Model legacy (weekday=NULL) dùng làm fallback nếu chưa có model weekday-specific.
  - **Training (`train_xgb.py`):** Nhận `--weekday 0..6` → filter `day_of_week` khi load data → lưu vào `models/XSMB/wd{N}/` trên Storage. Hỗ trợ hyperparameter injection qua CLI args (dùng bởi Master Retrain Agent).
  - **Inference (`predict_v3.py`):** Ưu tiên load model weekday-specific → fallback legacy. Gửi Telegram lúc **07:00 VN** với label `[XGBoost v3]` để phân biệt với XSMN Ensemble (20:00 VN).
  - **DB Schema:** `model_registry.weekday` (INT, NULL=legacy). Migration: `02_add_weekday_to_model_registry.sql`.

### 3.4. Phân hệ XSMN Multi-Model Ensemble (v3.1) 🆕
- **Component:** `src/xsmn_ensemble/`
- **Scope:** Chỉ áp dụng cho Xổ Số Miền Nam. XSMB pipeline 100% không bị ảnh hưởng.
- **Đặc thù Lookback:** XSMN mỗi tỉnh chỉ xổ 1 lần/tuần → Lookback theo **số kỳ quay** (LIMIT N), KHÔNG theo số ngày.
- **3 Sub-Models chạy song song:**
  - **Model A — Freq/Gap (`model_freq_gap.py`):** Rule-based scoring dựa trên tần suất (30/60/100 kỳ) + gap z-score + hot streak. Weight = **0.25**.
  - **Model B — Markov Chain (`model_markov.py`):** Xây dựng ma trận chuyển 100×100, dùng tail_set kỳ gần nhất làm context. Weight = **0.25**.
  - **Model C — XGBoost (`model_xgboost.py`):** Tái sử dụng `LotteryXGB` class, tự build features on-the-fly nếu DB chưa có. Weight = **0.50**.
- **Ensemble Engine (`ensemble_engine.py`):**
  - Phương pháp: **Weighted Borda Count** (Rank 1→5pts, Rank 2→4pts, ...Rank 5→1pt)
  - Consensus bonus: ×1.5 khi ≥2/3 model cùng chọn 1 cặp
  - Fault tolerant: 1 model lỗi → 2 model còn lại vẫn chạy
- **Province Resolution (`resolve_provinces.py`):**
  - Hỗ trợ env var `TARGET_PROVINCES` override từ GH Actions
  - Fallback: schedule tĩnh theo DOW (2 đài/ngày × 7 ngày)
- **Output:** Top 3 cặp số mỗi đài → `prediction_results` + log sub-model → `model_predictions`
- **Gửi Telegram:** Lúc **20:00 VN** với label `[3-Model Ensemble]` — tách biệt với XSMB XGBoost v3 gửi lúc 07:00.

### 3.5. Phân hệ Master Retrain Agent 🆕
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

### 3.6. Phân hệ Thông Báo (Telegram Bot)
- **Component:** `src/bot/telegram_bot.py`
- **Mô tả:** Async Telegram bot dùng `requests` gọi trực tiếp Bot API, hỗ trợ Mock Mode khi thiếu credentials.
- **Luồng:** Format HTML template với Emoji, các cặp số may mắn, xác suất. Gửi qua `chat_id` cấu hình trong Secrets.
- **Lịch gửi:**
  - **07:00 VN** — `predict_v3.py` gửi dự đoán XSMB `[XGBoost v3]` + XSMN preview `[XGBoost v3 — Preview]`.
  - **20:00 VN** — `predict_xsmn_ensemble.py` gửi dự đoán XSMN `[3-Model Ensemble]` (chính thức).
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
| **prediction_results** | Top 3 dự đoán + ensemble metadata (method, contributing_models, hit). | `UNIQUE(prediction_date, region, province)` |
| **model_predictions** 🆕 | Log Top 5 output từ mỗi sub-model trong XSMN ensemble pipeline. | `UNIQUE(prediction_date, region, province, model_name)` |
| **agent_actions** 🆕 | Lịch sử quyết định của Master Retrain Agent (retrain/skip/no_action). | `(action_date, region, province, weekday)` |
| **profit_tracking** 🆕 | Theo dõi lãi/lỗ thực tế per pair per station per ngày. | `UNIQUE(prediction_date, region, province, pair)` |
| **training_queue** | Queue trigger việc retrain (manual/perf_drop/new_data). | `status` ('pending'/'triggered'/'done') |

---

## 5. Deployment & CI/CD Strategy

Hệ thống không chạy ứng dụng dạng Long-lived (ví dụ Flask/FastAPI), mà chạy dạng **Task-based (Cron-Jobs)**.

**Các Job thiết lập trong `.github/workflows/`**:

| Workflow | Giờ VN | UTC | Chức năng |
|---|---|---|---|
| `01-daily-crawl.yml` | 19:00 hàng ngày | 12:00 | Crawl KQXS XSMB + XSMN, lưu vào `lottery_draws` & `tails_2d` |
| `02-predict.yml` | 07:00 hàng ngày | 00:00 | XGBoost predict XSMB + XSMN preview, gửi Telegram `[XGBoost v3]` |
| `03-verify-predictions.yml` | 19:30 hàng ngày | 12:30 | Verify hit/miss, profit tracking, kích hoạt Master Retrain Agent |
| `04-check-training.yml` | 21:00 Chủ Nhật | 14:00 CN | Kiểm tra điều kiện retrain định kỳ (new_data/perf_drop/manual) |
| `05-train-model.yml` | Trigger thủ công | — | Train/retrain XGBoost, hỗ trợ `--weekday`, `--region`, hyperparams |
| `06-query-report.yml` | Trigger thủ công | — | Query báo cáo từ DB |
| `07-predict-xsmn-ensemble.yml` 🆕 | 20:00 hàng ngày | 13:00 | XSMN 3-model Ensemble (Freq/Gap + Markov + XGBoost), 2 đài/ngày |

### 5.1. Môi trường Thực Thi (Environment Variables)
- `SUPABASE_URL`: Endpoint kết nối Supabase Postgres API & Storage.
- `SUPABASE_SERVICE_KEY`: Key quản trị bypassing RLS (Do chạy từ Server Backend).
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`: Credentials thông báo.

---

## 6. Yêu Cầu Phi Chức Năng (Non-Functional Requirements)

- **Fault Tolerance:** Crawler nếu rớt (do Cloudflare chặn hoặc mạng chậm) sẽ tự retry, hoặc cron job ghi lỗi vào bảng `crawler_logs` để dễ debug. Pipeline phải cho phép backfill (chạy bù) thông qua script manual mà không phá vỡ tính toàn vẹn dữ liệu. XSMN ensemble engine chịu lỗi: 1 model fail → 2 model còn lại vẫn chạy ensemble.
- **Scalability:** Hệ thống có thể scale lên 63 đài (các tỉnh miền Nam và miền Trung) do cấu trúc Database đã thiết kế hỗ trợ Column `region` và `province`. XSMN ensemble hiện cover 12 tỉnh/thành (2 đài/ngày × 7 ngày).
- **Security:** Model artifacts (file .pkl) được lưu trên Storage private, chỉ truy xuất qua IAM / Service Role. Data DB được bảo mật 100% bằng Supabase RLS.

---
*(Tài liệu này nằm trong bộ đặc tả chuẩn GitNexus để định hướng phát triển các module ML và Backend trong tương lai.)*
