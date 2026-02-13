# Historical Data Import Guide

## Mục đích
Import 2 năm dữ liệu lịch sử xổ số để train ML model

## Nguồn dữ liệu
- **GitHub Repository**: [khiemdoan/vietnam-lottery-xsmb-analysis](https://github.com/khiemdoan/vietnam-lottery-xsmb-analysis)
- **Format**: CSV files, cập nhật hàng ngày qua GitHub Actions
- **Data**: XSMB từ 2002 đến hiện tại

## Cách sử dụng

### 1. Kiểm tra CSV structure trước
```bash
# Download CSV để xem cấu trúc
curl -o xsmb_sample.csv "https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/main/data/xsmb.csv"

# Xem 10 dòng đầu
head -10 xsmb_sample.csv
```

### 2. Chạy import script
```bash
# Cài dependencies
pip3 install pandas

# Chạy script
python3 import_historical_data.py
```

### 3. Verify data trong Supabase
- Vào Supabase Dashboard
- Kiểm tra table `lottery_draws`
- Nên có ~730 records cho XSMB

## Lưu ý quan trọng

### ⚠️ CSV Structure
Script hiện tại giả định CSV có columns:
- `date` hoặc `Date`
- `DB` (Giải đặc biệt)
- `G1`, `G2`, `G3`, `G4`, `G5`, `G6`, `G7`

**Bạn cần kiểm tra CSV thực tế và adjust hàm `_convert_csv_row_to_draw()` cho đúng!**

### 🔧 Customize Script
Nếu CSV structure khác, sửa trong file `import_historical_data.py`:

```python
def _convert_csv_row_to_draw(self, row):
    # Adjust column names here based on actual CSV
    draw_data = {
        'draw_date': row['actual_date_column'],
        'special_prize': str(row['actual_special_prize_column']),
        # ... etc
    }
```

### 📊 Alternative Sources
Nếu source trên không hoạt động, có thể dùng:
1. **luatnd/ketquaxoso-crawler-puppeteer** - JSON files từ 2002
2. **vietvudanh/vietlott-data** - Vietlott data với dashboard

## Troubleshooting

### Lỗi: "Column not found"
→ CSV structure khác với expected. Xem CSV và update `_convert_csv_row_to_draw()`

### Lỗi: "Duplicate key"
→ Data đã tồn tại. Script sẽ skip và continue.

### Lỗi: "Invalid date format"
→ Kiểm tra format ngày trong CSV và convert đúng format ISO (YYYY-MM-DD)

## Next Steps
Sau khi import xong:
1. ✅ Verify data trong Supabase
2. ✅ Test prediction model với historical data
3. ✅ Enable daily crawl workflow để tiếp tục thu thập data mới
