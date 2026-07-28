---
title: 'Backfill LSTM XSMN cho các tỉnh-thứ còn thiếu'
type: 'chore'
created: '2026-07-28'
status: 'done'
review_loop_iteration: 0
baseline_commit: '5f3fbab'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-retrain-all-xsmn-provincial-models.md'
---

<frozen-after-approval reason="Người dùng yêu cầu backfill model LSTM XSMN production">

## Intent

**Problem:** Production mới có 13/22 artifact LSTM theo grain `(province, weekday)`. Chín tỉnh quay thứ Tư–Sáu chưa có artifact trước kỳ dự đoán 29–31/07/2026, nên LSTM sẽ trả danh sách rỗng và ensemble chỉ chạy 5/6 model.

**Approach:** Dùng CLI training hiện hữu để huấn luyện và publish đúng chín artifact còn thiếu. Mỗi artifact dùng tối đa 250 kỳ cùng thứ và cutoff tại kỳ gần nhất đã hoàn tất trước ngày dự đoán: 22/07 cho thứ Tư, 23/07 cho thứ Năm và 24/07 cho thứ Sáu.

## Boundaries & Constraints

**Always:** Backfill đúng grain tỉnh–thứ; xác minh mỗi tỉnh có tối thiểu 40 kỳ và ngày lịch sử mới nhất trùng cutoff; version deterministic chứa cutoff và weekday; chỉ công nhận thành công khi registry active, `train_end_date` đúng cutoff, artifact tải được và inference cho ngày kế tiếp trả Top 5; giữ artifact cũ nếu upload hoặc registry insert lỗi; Telegram nhận kết quả thành công/lỗi từ CLI.

**Ask First:** Thay code, schema, ensemble weights, thuật toán LSTM, bật on-the-fly training, backfill XGBoost hoặc thay thế 13 artifact LSTM đã active.

**Never:** Dùng kết quả của chính ngày dự đoán; train gộp nhiều weekday; publish artifact có cutoff mismatch; chạy lại prediction lịch sử bằng artifact đã nhìn thấy kết quả target; ghi hoặc in credentials.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Scope thiếu hợp lệ | Không có active LSTM, history đủ và latest đúng cutoff | Train, upload, đăng ký active cho đúng tỉnh–thứ | Tiếp tục scope kế tiếp và tổng hợp lỗi |
| Scope đã xuất hiện trong lúc chạy | Registry đã có artifact active đúng hoặc mới hơn | Không train đè | Ghi nhận skipped/fresh |
| Cutoff hoặc dữ liệu sai | Latest history khác cutoff hoặc dưới ngưỡng | Không upload/activate | Dừng scope đó, giữ production hiện tại |
| Publication lỗi | Train xong nhưng upload/registry thất bại | Không deprecate fallback hiện có | Báo lỗi Telegram và không công nhận scope |

</frozen-after-approval>

## Code Map

- `src/scripts/train_lstm.py` -- CLI deterministic huấn luyện, upload Storage, đăng ký registry và gửi Telegram.
- `src/xsmn_ensemble/model_lstm.py` -- lookup artifact theo tỉnh–weekday và inference verification.
- `src/agent/master_retrain_agent.py` -- nguồn lịch quay chuẩn cho 22 scope XSMN.
- `model_registry` và Supabase Storage `models/XSMN/lstm/` -- đích publication production.

## Tasks & Acceptance

**Execution:**

- [x] Preflight registry và same-weekday history cho chín scope thứ Tư–Sáu.
- [x] Chạy `train_lstm.py` tuần tự cho từng scope với `--weekday`, `--target-date`, version deterministic và `--defer-queue-completion`.
- [x] Đối chiếu registry sau mỗi scope; không tiếp tục coi scope thành công nếu cutoff hoặc file path sai.
- [x] Tải artifact và inference cho ngày quay kế tiếp của đủ chín scope.
- [x] Tổng hợp coverage production phải đạt 22/22; không thay đổi source code.

**Acceptance Criteria:**

- Given chín scope còn thiếu có 238 kỳ hợp lệ, when backfill hoàn tất, then mỗi scope có một LSTM active với đúng `province`, `weekday` và cutoff kỳ trước.
- Given artifact đã publish, when gọi `predict_lstm` cho ngày 29–31/07/2026, then model tải từ Storage và trả `status=success`, đúng version, 180 kỳ dùng và năm cặp số.
- Given registry hiện có 13 scope, when hoàn tất toàn bộ backfill, then coverage active chính xác là 22/22 và các scope cũ không bị thay đổi.
- Given một scope lỗi, when batch kết thúc, then lỗi được báo rõ và các scope độc lập còn lại vẫn được xử lý.

## Spec Change Log

- Review iteration 0: bổ sung execution evidence theo từng scope, registry ID, artifact identity, checksum, inference output, canonical coverage và Telegram delivery. Việc này tránh trạng thái checklist hoàn tất nhưng không thể audit; giữ nguyên cutoff chống leakage, grain tỉnh–thứ và kết quả production đã publish.

## Design Notes

Không dùng coordinator với `target_date` của ngày dự đoán sắp tới vì feature labels cho ngày đó chưa tồn tại. CLI LSTM nhận cutoff inclusive đã hoàn tất, trong khi artifact grain weekday làm nó phù hợp cho lần quay kế tiếp sau cutoff. Backfill được chạy tuần tự để hạn chế CPU và tránh publication chồng chéo.

PyTorch 2.11 trên macOS gặp `SIGSEGV` khi training đa luồng. Batch production được retry an toàn với các backend CPU giới hạn một luồng; không có artifact nào được upload ở các lần crash và logic/model không bị thay đổi.

## Verification

**Commands:**

- Preflight read-only `model_registry` và `_load_tails_by_draws(..., target_weekday=wd)` -- expected: 9 scope thiếu, mỗi scope 238 kỳ và latest đúng cutoff.
- `python3 src/scripts/train_lstm.py --region XSMN ...` cho từng scope -- expected: exit 0, upload và registry active.
- `predict_lstm(..., target_date=29|30|31/07/2026, n_draws=180, seq_len=30)` -- expected: 9/9 success, không fallback.
- Query coverage active LSTM XSMN -- expected: 22/22 scope.

## Execution Evidence

Thời điểm thực thi: 28/07/2026. Prefix Storage của toàn bộ artifact là `models/XSMN/lstm/`. Mỗi scope có 238 kỳ preflight; strict inference yêu cầu `status=success`, đúng version, `n_draws_used=180`, đúng năm cặp duy nhất trong `00..99` và score hữu hạn.

| Registry | Tỉnh | WD | Cutoff | Version | Artifact (bytes; SHA-256 prefix) | Target → Top 5 |
|---------:|------|---:|--------|---------|---------------------------------|----------------|
| 467 | dong-nai | 2 | 2026-07-22 | `lstm_v4_backfill_20260722_wd2` | `dong-nai_wd2_lstm_v4_backfill_20260722_wd2.pth` (199481; `48bc26b161a8`) | 2026-07-29 → 83, 23, 02, 61, 68 |
| 468 | can-tho | 2 | 2026-07-22 | `lstm_v4_backfill_20260722_wd2` | `can-tho_wd2_lstm_v4_backfill_20260722_wd2.pth` (199469; `e9130c9d6873`) | 2026-07-29 → 68, 01, 27, 06, 33 |
| 469 | soc-trang | 2 | 2026-07-22 | `lstm_v4_backfill_20260722_wd2` | `soc-trang_wd2_lstm_v4_backfill_20260722_wd2.pth` (199557; `5ed3ce3c2a0a`) | 2026-07-29 → 63, 28, 93, 03, 96 |
| 470 | tay-ninh | 3 | 2026-07-23 | `lstm_v4_backfill_20260723_wd3` | `tay-ninh_wd3_lstm_v4_backfill_20260723_wd3.pth` (199481; `0d831b25e5ae`) | 2026-07-30 → 09, 62, 24, 71, 96 |
| 471 | an-giang | 3 | 2026-07-23 | `lstm_v4_backfill_20260723_wd3` | `an-giang_wd3_lstm_v4_backfill_20260723_wd3.pth` (199481; `25be7641fea2`) | 2026-07-30 → 14, 69, 50, 02, 49 |
| 472 | binh-thuan | 3 | 2026-07-23 | `lstm_v4_backfill_20260723_wd3` | `binh-thuan_wd3_lstm_v4_backfill_20260723_wd3.pth` (199569; `c77d6851b98e`) | 2026-07-30 → 19, 78, 65, 01, 33 |
| 473 | vinh-long | 4 | 2026-07-24 | `lstm_v4_backfill_20260724_wd4` | `vinh-long_wd4_lstm_v4_backfill_20260724_wd4.pth` (199557; `a8a7f138b4c0`) | 2026-07-31 → 69, 60, 96, 21, 16 |
| 474 | binh-duong | 4 | 2026-07-24 | `lstm_v4_backfill_20260724_wd4` | `binh-duong_wd4_lstm_v4_backfill_20260724_wd4.pth` (199569; `942606794ff9`) | 2026-07-31 → 08, 22, 50, 30, 13 |
| 475 | tra-vinh | 4 | 2026-07-24 | `lstm_v4_backfill_20260724_wd4` | `tra-vinh_wd4_lstm_v4_backfill_20260724_wd4.pth` (199481; `896c565f71b1`) | 2026-07-31 → 00, 62, 52, 54, 80 |

Kết quả tổng:

- LSTM active rows = 22, distinct `(province, weekday)` = 22, canonical schedule scopes = 22 và hai tập hợp bằng nhau; không có duplicate active scope.
- Strict Storage download + inference = 9/9 PASS.
- Batch đầu báo rõ sáu subprocess `SIGSEGV` và vẫn chuyển sang scope kế tiếp; không artifact nào được upload. Retry một luồng publish 9/9 thành công.
- Không chạy lệnh XGBoost hoặc sửa source code. XGBoost vẫn có 13 active rows; `trained_at` mới nhất là `2026-07-28T13:14:04.828236`, trước batch backfill này.
- Telegram summary delivery trả `True`.

## Suggested Review Order

**Production evidence**

- Bắt đầu từ chín artifact, checksum, target và Top 5 đã tải lại.
  [`spec-backfill-xsmn-lstm-missing-weekdays.md:83`](./spec-backfill-xsmn-lstm-missing-weekdays.md#L83)

- Xác nhận canonical coverage 22/22, strict inference và Telegram delivery.
  [`spec-backfill-xsmn-lstm-missing-weekdays.md:99`](./spec-backfill-xsmn-lstm-missing-weekdays.md#L99)

**Leakage và phạm vi**

- Cutoff, grain tỉnh–thứ và các hành động bị cấm được khóa tại đây.
  [`spec-backfill-xsmn-lstm-missing-weekdays.md:21`](./spec-backfill-xsmn-lstm-missing-weekdays.md#L21)

- Runtime một luồng xử lý crash macOS mà không đổi model.
  [`spec-backfill-xsmn-lstm-missing-weekdays.md:68`](./spec-backfill-xsmn-lstm-missing-weekdays.md#L68)

**Rủi ro deferred**

- Các rủi ro publication, signal alert và uniqueness được tách khỏi backfill.
  [`deferred-work.md:50`](./deferred-work.md#L50)
