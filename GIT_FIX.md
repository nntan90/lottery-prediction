# 🔧 Fix Git Push Error

## Vấn đề
Repository `https://github.com/nntan90/lottery-prediction.git` không tồn tại.

## Giải pháp: Tạo Repository Trên GitHub

### Bước 1: Tạo Repository Mới

1. Mở trình duyệt, vào: https://github.com/new
2. Điền thông tin:
   - **Repository name**: `lottery-prediction` (hoặc tên khác)
   - **Description**: `Automated lottery prediction system`
   - **Visibility**: Chọn **Public** (quan trọng! để có unlimited GitHub Actions)
   - **KHÔNG** check "Add a README file" (vì đã có sẵn)
   - **KHÔNG** check "Add .gitignore" (vì đã có sẵn)
3. Click **"Create repository"**

### Bước 2: Update Remote URL (Nếu Cần)

Nếu bạn đặt tên repository khác (không phải `lottery-prediction`), chạy:

```bash
cd /Users/tannguyen/Workspace/Anlysis_Lottery

# Xóa remote cũ
git remote remove origin

# Thêm remote mới (thay YOUR_REPO_NAME)
git remote add origin https://github.com/nntan90/YOUR_REPO_NAME.git
```

### Bước 3: Push Code

```bash
cd /Users/tannguyen/Workspace/Anlysis_Lottery

# Đảm bảo đã commit
git add .
git commit -m "Initial setup: complete lottery prediction system"

# Push
git push -u origin main
```

**Nếu gặp lỗi "main branch doesn't exist"**, chạy:
```bash
git branch -M main
git push -u origin main
```

### Bước 4: Xác Nhận

Sau khi push thành công:
1. Refresh trang GitHub repository
2. Bạn sẽ thấy tất cả files đã được upload
3. Tab **Actions** sẽ xuất hiện

---

## Alternative: Sử dụng Repository Hiện Tại

Nếu bạn muốn dùng repository hiện tại `Anlysis_Lottery`:

```bash
cd /Users/tannguyen/Workspace/Anlysis_Lottery

# Xóa remote cũ
git remote remove origin

# Thêm remote đúng (nếu repo đã tồn tại)
git remote add origin https://github.com/nntan90/Anlysis_Lottery.git

# Push
git push -u origin main
```

---

## ⚠️ Lưu Ý Quan Trọng

Tôi thấy bạn đã paste **credentials** vào file `NEXT_STEPS.md`:
- Supabase URL
- Supabase publishable key  
- Telegram bot token
- Chat ID

**NGUY HIỂM!** Khi push lên GitHub, thông tin này sẽ public!

### Cần làm ngay:

1. **XÓA credentials khỏi NEXT_STEPS.md**:
```bash
# Mở file và xóa 7 dòng cuối (từ dòng 177-183)
```

2. **Commit lại**:
```bash
git add NEXT_STEPS.md
git commit -m "Remove sensitive credentials"
```

3. **Sau đó mới push**

---

Bạn muốn tôi giúp tạo repository hay fix remote URL?
