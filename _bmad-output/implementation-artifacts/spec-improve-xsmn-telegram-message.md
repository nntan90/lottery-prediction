---
title: 'Cải thiện thông báo Telegram XSMN ensemble'
type: 'feature'
created: '2026-07-24'
status: 'done'
route: 'one-shot'
---

# Cải thiện thông báo Telegram XSMN ensemble

## Intent

**Problem:** Message XSMN hiện dồn kết quả production, shadow diagnostics và tình trạng model vào một khối nên khó quét nhanh trên Telegram; trạng thái DDT không khả dụng cũng chưa có mức cảnh báo trực quan phù hợp.

**Approach:** Chia message thành các vùng dự đoán chính, shadow chỉ tham khảo và sức khỏe ensemble; dùng thứ hạng, dấu phân cách, tree lines và trạng thái model rõ ràng mà không thay đổi dữ liệu hay thuật toán dự đoán.

## Suggested Review Order

- Formatter chính tổ chức lại production, shadow, consensus và model health.
  [`ensemble_messages.py:50`](../../src/bot/ensemble_messages.py#L50)

- Shadow status phân biệt đang chờ với lỗi hoặc không khả dụng.
  [`ensemble_messages.py:24`](../../src/bot/ensemble_messages.py#L24)

- Snapshot ngày 24/07 khóa đúng bố cục XSMN người dùng yêu cầu.
  [`test_ensemble_telegram_messages.py:187`](../../tests/test_ensemble_telegram_messages.py#L187)

- Regression giữ health icon trung thực khi count và danh sách thiếu mâu thuẫn.
  [`test_ensemble_telegram_messages.py:239`](../../tests/test_ensemble_telegram_messages.py#L239)

- Formatter dùng chung vẫn giữ bố cục XSMB không có station hoặc shadow.
  [`test_ensemble_telegram_messages.py:255`](../../tests/test_ensemble_telegram_messages.py#L255)
