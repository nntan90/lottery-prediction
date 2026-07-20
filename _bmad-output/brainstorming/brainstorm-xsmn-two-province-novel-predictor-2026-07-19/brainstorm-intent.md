# Intent: Coupled Motif Retrieval cho XSMN/all

## Mục tiêu

Tạo một predictor XSMN mới, chạy shadow độc lập với ensemble hiện tại và có cơ chế suy luận khác biệt: xem hai tỉnh target là một hệ ghép, truy hồi các trạng thái quan hệ lịch sử tương tự, rồi dự báo tail 2 cho kỳ tiếp theo của đúng từng tỉnh.

KPI promotion chính là tỷ lệ `hit >= 2/3` trên tail-set merge của đúng hai tỉnh target trong ngày.

## Ý tưởng cốt lõi

Với target date `D` và hai tỉnh `A`, `B`:

1. Lấy kỳ quay gần nhất trước `D` của từng tỉnh, không bắt buộc cùng thứ.
2. Ghép hai anchor thành một asynchronous coupled snapshot. TP.HCM thứ Hai dùng kỳ TP.HCM thứ Bảy gần nhất.
3. Trong từng mã giải, ghép all-to-all các tail của tỉnh A với tỉnh B; không dùng vị trí ordinal.
4. Mỗi mã giải có tổng ảnh hưởng bằng nhau vì mục tiêu chỉ là tail 2 xuất hiện hay không.
5. Tạo relational fingerprint từ exact match, delta modulo, cùng đầu, cùng đuôi, đảo và bù số.
6. Tìm historical snapshots có fingerprint tương tự, nhưng label luôn là kỳ tiếp theo của đúng tỉnh: `A_anchor -> A_next`, `B_anchor -> B_next`.
7. Direct overlap của toàn tail-set hai anchor luôn được đưa vào candidate pool, không cần cùng mã giải.
8. Historical motifs bổ sung candidate khi overlap không đủ.
9. Ước lượng merged hit likelihood bằng similarity-weighted next-draw hits với Bayesian shrinkage.
10. Chọn Top 3 có tổng estimated hit likelihood cao nhất, với tối đa một direct-overlap candidate.

## Ràng buộc bắt buộc

- Không dùng dữ liệu tại hoặc sau target date để tạo anchor, fingerprint, neighbor hay tuning decision.
- Không dùng raw frequency, gap, Markov transition hoặc output ensemble hiện tại làm input cho CMR V1.
- Không gọi score là xác suất nếu chưa có calibration out-of-time.
- Không promote trước khi thắng random baseline và strongest existing single model trên KPI `>=2/3`.
- Permutation test phải làm hiệu quả CMR sụp về baseline; nếu không, cần kiểm tra leakage.
- V1 phải giải thích được mỗi candidate bằng overlap source, nearest historical cases và weighted hit evidence.

## Phạm vi V1

CMR V1 dùng interpretable similarity retrieval. Learned embedding, Set Transformer, neural retrieval và việc trộn CMR vào production ensemble được hoãn đến khi shadow evaluation chứng minh đủ lift và sample size.

