# 🧪 Test Supabase Connection - Quick Guide

## ✅ Dependencies đã được cài đặt!

Bây giờ chạy test script:

```bash
python3 test_supabase.py
```

## Script sẽ hỏi bạn 2 thông tin:

### 1. SUPABASE_URL
Vào Supabase dashboard → Settings → API → Copy **Project URL**

Ví dụ: `https://islcxaqdqhwgcqkdozeq.supabase.co`

### 2. SUPABASE_SERVICE_KEY  
Vào Supabase dashboard → Settings → API → Tìm key **service_role** → Click "Reveal" → Copy

⚠️ **QUAN TRỌNG**: Phải là key `service_role`, KHÔNG phải `anon` hay `publishable`!

Key sẽ bắt đầu bằng: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...` (rất dài, ~200-300 ký tự)

---

## Kết quả mong đợi

### ✅ Nếu thành công:
```
✅ Import successful
✅ Client created successfully
✅ Database accessible!
   Current records in lottery_draws: 0

Checking all tables...
   ✅ lottery_draws: OK
   ✅ predictions: OK
   ✅ evaluation_metrics: OK
   ✅ telegram_subscribers: OK
   ✅ crawler_logs: OK
   ✅ model_metadata: OK

🎉 ALL TESTS PASSED!

Your credentials are correct. You can use them in GitHub Secrets:
SUPABASE_URL: https://islcxaqdqhwgcqkdozeq.supabase.co
SUPABASE_SERVICE_KEY: eyJhbGciOiJIUzI1N...
```

→ **Credentials đúng!** Copy chính xác 2 giá trị này vào GitHub Secrets.

### ❌ Nếu lỗi:

**"Invalid API key"**
- Bạn đang dùng nhầm `anon` key thay vì `service_role` key
- Hoặc copy thiếu/thừa ký tự (có khoảng trắng đầu/cuối)

**"relation ... does not exist"**
- Bạn chưa chạy `database/schema.sql` trong Supabase SQL Editor

---

## Sau khi test thành công

1. Vào: https://github.com/nntan90/lottery-prediction/settings/secrets/actions
2. Update/Add 2 secrets:
   - `SUPABASE_URL`: Paste URL vừa test
   - `SUPABASE_SERVICE_KEY`: Paste key vừa test
3. Chạy lại workflow "05 - Initial Data Backfill"

---

**Chạy ngay: `python3 test_supabase.py`**
