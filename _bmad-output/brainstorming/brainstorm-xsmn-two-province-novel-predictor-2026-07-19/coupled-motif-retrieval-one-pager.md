# Coupled Motif Retrieval V1

## 1. Tuyên bố thiết kế

CMR là case-based predictor cho scope `XSMN/all`. Nó không chạy model riêng cho từng tỉnh rồi cộng điểm. Mỗi sample là một trạng thái ghép của hai latest same-province anchors; model tìm các trạng thái lịch sử tương tự và dùng next draw của đúng từng tỉnh làm evidence cho exact tails `00-99`.

## 2. Sample contract

Với target `D` và schedule pair `(A, B)`:

```text
anchor_A = latest draw of A where draw_date < D
anchor_B = latest draw of B where draw_date < D
context  = coupled_fingerprint(anchor_A, anchor_B)
label_A  = tails of A at D
label_B  = tails of B at D
label_all = label_A union label_B
```

Training sample chỉ hợp lệ khi có đủ hai anchors, đủ prize rows ở cả hai anchors và đủ kết quả target cho cả hai tỉnh. Bất đồng bộ ngày được giữ bằng `anchor_age_A`, `anchor_age_B`; TP.HCM không có special-case scoring hardcoded.

## 3. Relational fingerprint

Cho mỗi prize code `r` trong `DB, G1..G8`, tạo Cartesian product:

\[
P_r = \{(a,b)\mid a\in A_r, b\in B_r\}
\]

Từng block được chuẩn hóa bởi `1 / |P_r|`, để tổng ảnh hưởng của mọi mã giải bằng nhau. Fingerprint V1 gồm các thống kê permutation-invariant:

- Histogram `delta_units = (unit(b)-unit(a)) mod 10`.
- Histogram `delta_tens = (tens(b)-tens(a)) mod 10`.
- Rate exact match, same head, same tail, reversal và complement-to-99.
- Anchor ages và cờ pair/weekday để phân biệt lịch ghép.

Không dùng raw tail frequency hoặc gap trong fingerprint.

## 4. Candidate generation

```text
overlap = all_tails(anchor_A) intersection all_tails(anchor_B)
neighbors = Top-K historical contexts by fingerprint similarity
motif_candidates = tails appearing in neighbors' label_all
candidate_pool = overlap union motif_candidates
```

Direct overlap không yêu cầu cùng mã giải. Mọi overlap được giữ làm candidate nhưng selector cuối chỉ được lấy tối đa một số thuộc nhóm này.

Similarity V1 dùng weighted cosine trên từng normalized prize block, sau đó lấy trung bình chín block. Các trọng số relation, `K` và shrinkage strength chỉ được chọn bằng nested walk-forward.

## 5. Scoring

Với historical neighbor `i`, similarity không âm `s_i`, merged label `Y_i` và prior mean `p0`:

\[
q(n)=\frac{\alpha p_0 + \sum_i s_i I[n\in Y_i]}{\alpha + \sum_i s_i}
\]

`q(n)` là estimated hit likelihood chưa calibration. Audit cho mỗi số phải lưu:

- `is_direct_overlap`;
- `q_merged`, weighted support và effective neighbor count;
- support riêng từ `label_A`, `label_B`;
- IDs/dates/similarities của nearest cases;
- relation blocks đóng góp nhiều nhất vào similarity.

## 6. Selector

Chọn tổ hợp `S`:

\[
S^*=\arg\max_{|S|=3}\sum_{n\in S}q(n)
\]

với constraint `|S intersect overlap| <= 1`. Không áp quota tỉnh, diversity đầu/đuôi hoặc consensus bonus. Nếu không có ba candidate có evidence hợp lệ, run phải trả trạng thái insufficient evidence thay vì pad số.

## 7. Evaluation và promotion gate

- Expanding walk-forward theo target date; mọi neighbor và tuning result phải có date nhỏ hơn fold target.
- KPI chính: `Hit >= 2/3` trên `label_all`; báo thêm distribution `0/3..3/3`.
- Baselines: hypergeometric random, direct-overlap-only, motif-only và strongest existing single model.
- Permutation test: shuffle context-to-next-label mapping trong training fold.
- Báo sample size, bootstrap confidence interval, lift và kết quả theo từng weekday pair.
- Chạy shadow độc lập tối thiểu 8-12 tuần; chỉ promote khi lift dương ổn định và permutation result quay về baseline.

## 8. Rollout boundary

V1 chỉ ghi shadow predictions và evidence, không tham gia trọng số production ensemble. Learned embedding, neural set model và score calibration là phase sau, phụ thuộc evidence của V1.

