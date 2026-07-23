---
stepsCompleted:
  - 1
  - 2
inputDocuments:
  - docs/system_architecture.md
  - docs/project-context.md
  - _bmad-output/implementation-artifacts/spec-xsmn-ensemble-production-hardening.md
  - _bmad-output/implementation-artifacts/spec-xsmn-coupled-motif-retrieval-shadow.md
  - _bmad-output/brainstorming/brainstorm-xsmn-provincial-digit-affinity-predictor-2026-07-21/.memlog.md
---

# Analysis Lottery - XSMN Provincial Digit Transition Epic Breakdown

## Overview

Tài liệu này cung cấp inventory yêu cầu cho function/model XSMN Provincial Digit
Affinity + Dynamic Digit Transition (PDA/DDT). Feature mới chạy shadow theo từng
tỉnh, merge xác suất sau khi dự đoán độc lập và được orchestration trong cùng job
generate prediction hiện tại mà không thay đổi kết quả production cũ.

## Requirements Inventory

### Functional Requirements

FR1: Hệ thống phải cung cấp một predictor PDA/DDT mới chỉ áp dụng cho region XSMN và không được gọi từ bất kỳ luồng XSMB nào.

FR2: Predictor phải nhận đúng danh sách tỉnh của target date từ province resolver hiện tại; không được duy trì một lịch tỉnh riêng hoặc trộn lịch sử giữa các tỉnh trước suy luận.

FR3: Predictor phải coi một kỳ quay hoàn chỉnh của một tỉnh là một quan sát và chỉ nhận kỳ có đủ 18 prize tails theo cấu trúc DB, 1, 2, 3, 4, 5, 6, 7, 8.

FR4: Mọi context, prior, historical neighbor, calibration sample và label phải có draw date nhỏ hơn target date; label phải là kỳ kế tiếp của cùng tỉnh.

FR5: Với mỗi kỳ và mỗi tỉnh, predictor phải tạo histogram 10-bin riêng cho hàng chục và hàng đơn vị, giữ cả multiplicity chuẩn hóa trên 18 giải và binary coverage.

FR6: Dynamic state phải chứa tối thiểu full histogram hiện tại, dominant digit, dominant count, margin so với runner-up, entropy, các dominant state gần nhất và transition route.

FR7: Static provincial affinity phải đóng vai trò prior; estimate tỉnh phải được hierarchical Bayesian shrink về route/weekday và XSMN-wide prior khi support địa phương thấp.

FR8: Predictor phải xử lý TP.HCM theo transition route Thứ Hai -> Thứ Bảy và Thứ Bảy -> Thứ Hai, đồng thời chia sẻ province-level prior giữa hai route.

FR9: Predictor phải xuất cho từng tỉnh `unit_share[0..9]` có tổng bằng 1, biểu diễn expected share của 18 hàng đơn vị kỳ sau; đồng thời xuất leader probability, confidence/entropy và effective sample support.

FR10: Predictor phải dự đoán và chấm đủ 100 ordered pairs `00..99` từ unit share, head-conditional evidence và residual pair interaction đã loại ảnh hưởng marginal; `27` và `72` phải là hai candidate khác nhau.

FR11: Pair interaction phải được shrink mạnh hơn digit marginals và không được dùng raw pair frequency hoặc one-observation lift như bằng chứng đủ để chi phối ranking.

FR12: Mỗi tỉnh phải tạo pair likelihood độc lập trước khi merge; output phải giữ attribution và score/probability của từng tỉnh.

FR13: Merged unit share của hai tỉnh phải là trung bình có trọng số theo số prize observations hợp lệ; khi cả hai kỳ đều đủ 18 giải thì đây là trung bình cộng của hai unit-share vectors.

FR14: Merged exact-pair probability phải dùng `P(A union B) = P(A) + P(B) - P(A intersect B)`; baseline intersection là `P(A)*P(B)` và được hiệu chỉnh bằng Bayesian coupling lift.

FR15: Coupling lift phải fallback theo hierarchy `exact pair -> unit digit -> ordered province pair global -> XSMN prior`, shrink theo effective support và clamp intersection vào Fréchet bounds `[max(0, P(A)+P(B)-1), min(P(A),P(B))]`.

FR16: Hệ thống không được nhân merged unit share lần nữa vào pair probability đã chứa digit evidence; merged unit share chỉ điều khiển candidate eligibility và slot allocation.

FR17: Khi evidence đủ, selector phải trả đúng ba distinct exact pairs, chỉ lấy từ các unit digits ưu tiên, có ít nhất một pair thuộc unit hạng 1 và không quá hai pairs cùng một hàng đơn vị.

FR18: Selector phải duyệt và so sánh tối thiểu hai allocation pattern `2+1` và `1+1+1`, rồi chọn tổ hợp có tổng portfolio utility cao nhất dưới các constraint.

FR19: Khi support hoặc calibration không đạt gate, predictor phải trả trạng thái `insufficient_evidence`/`uncalibrated` có reason và không được pad hoặc trình bày false precision như probability.

FR20: Output shadow phải giữ Top 3, Top 10 candidate pool, full Top-100 audit và decomposition gồm province probabilities/scores, unit shares, interaction, coupling lift, intersection, merged result, confidence và data cutoff.

FR21: Predictor phải deterministic: cùng input rows, target date, province order, config và model/calibration versions phải tạo cùng distribution, candidate ordering và Top 3.

FR22: Predictor mới phải được gọi trong XSMN section của `src/scripts/predict_ensemble.py`, dùng cùng target date và resolved provinces, sau khi production XSMN ensemble đã tạo candidate, và được bọc fault boundary riêng.

FR23: Feature phải chạy trong workflow `.github/workflows/02-predict-ensemble.yml` hiện tại tại cron `14 0 * * *` (07:14 Việt Nam); không tạo workflow hoặc cron thứ hai.

FR24: Lỗi, timeout hoặc insufficient evidence của PDA/DDT không được thay đổi, chặn lưu hoặc làm fail Top 3 production XSMN hiện tại; lỗi phải được ghi log rõ ràng.

FR25: V1 phải chạy shadow: không tham gia sáu model weights, Borda/CombSUM, consensus, credibility score, production selector hoặc `prediction_results` Top 3 contract.

FR26: Dry-run hiện tại phải chạy được PDA/DDT nhưng không tạo side effect lưu DB hoặc gửi Telegram ngoài hành vi dry-run đã có.

FR27: Hệ thống phải cung cấp expanding walk-forward evaluation theo `(prediction_date, province, weekday/route, model_name)` với Hit@3, mean hits, phân phối 0/1/2/3 hits, Brier/calibration, confidence buckets và lift so với marginal-only, frequency, strongest production model và ensemble.

FR28: Probability fields dùng cho union merge phải được fit/đánh giá out-of-fold theo từng tỉnh hoặc hierarchical calibration; trước khi gate đạt, output phải dùng tên `estimated_likelihood_uncalibrated` thay vì `probability`.

FR29: Backtest phải có permutation controls cho province labels, draw order và head-unit association để phát hiện lift giả.

FR30: V1 không được auto-promote; mọi thay đổi để PDA/DDT tác động production ensemble, Telegram contract, schema hoặc model weights cần approval riêng sau khi có đủ shadow evidence.

### NonFunctional Requirements

NFR1: Backward compatibility - toàn bộ public function signatures, storage contracts, six-model XSMN output, XSMB behavior và production Top 3 phải giữ nguyên.

NFR2: Isolation - code mới phải nằm trong namespace/module riêng; các model Frequency, Gap, Markov, XGBoost, LSTM, CDM và ensemble engine hiện tại không được import hoặc phụ thuộc ngược vào PDA/DDT.

NFR3: Fault tolerance - mọi lỗi PDA/DDT phải được catch ở orchestration boundary; production XSMN prediction và notification tiếp tục theo hành vi hiện tại.

NFR4: Leakage safety - mọi DB query và transform phải thực thi strict pre-target cutoff và same-province next-label grain.

NFR5: Data integrity - incomplete draws không được tham gia state, prior, transition, coupling hoặc calibration; pagination phải tải đầy đủ history vượt giới hạn 1.000 rows của PostgREST.

NFR6: Statistical honesty - chỉ gọi giá trị là probability khi calibration out-of-time được đo; mọi score chưa calibration phải có tên và metadata rõ ràng.

NFR7: Reproducibility - kết quả phải ổn định theo target date/config/model versions và không được train neural model ngẫu nhiên on-the-fly.

NFR8: Runtime compatibility - feature phải hoàn tất trong cùng GitHub Actions prediction job, dùng tài nguyên bounded và không làm thay đổi cron, dependency-install flow hoặc retry semantics hiện tại.

NFR9: Dependency discipline - V1 chỉ dùng Python standard library và packages đã có trong `requirements.txt`; thêm dependency cần approval riêng.

NFR10: Security - không hardcode Supabase, Telegram hoặc storage credentials; chỉ dùng clients/env conventions hiện tại.

NFR11: Observability - log phải chứa status theo tỉnh, sample support, confidence, cutoff, calibration state, merge/coupling diagnostics, runtime và error reason mà không ghi secrets.

NFR12: Maintainability - public types và probability/calibration/scoring functions phải có type hints và docstrings giải thích semantics; config phải có default deterministic và validation.

NFR13: Verification - phải có focused unit/integration tests cho state, shrinkage, route, calibration, merge bounds, selector constraints, cutoff, incomplete draw, determinism và fault isolation; toàn bộ `python3 -m pytest -q` phải pass.

NFR14: Schema discipline - nếu V1 cần schema mới, phải thêm migration riêng và cập nhật `database/schema_final.sql`; ưu tiên shadow artifact/audit không thay đổi schema trong lát cắt đầu tiên.

NFR15: Promotion safety - không có dynamic weight, production import vào six-model engine, auto-promotion hoặc Telegram claim về calibrated probability trước khi promotion gates được review.

### Additional Requirements

- Tạo bounded context mới đề xuất `src/xsmn_digit_transition/` gồm domain contracts, read-only repository, state builder, hierarchical estimator, calibration/merge service, constrained selector và public shadow service.
- Public service nên nhận `db`, ordered target provinces và `target_date`, trả typed/dict result có status thay vì ném lỗi nghiệp vụ ra production orchestration.
- Tái sử dụng `get_target_provinces(target_date)` làm nguồn scope duy nhất và completeness contract của `src/xsmn_coupled/domain.py` khi phù hợp; không tạo schedule table thứ hai.
- Gắn lời gọi shadow vào `run_xsmn_ensemble` sau khi production `ensemble_output` đã được validate, tương tự fault boundary CMR hiện có, để production result không phụ thuộc feature mới.
- Không sửa cron `14 0 * * *`; workflow chỉ tiếp tục gọi `python src/scripts/predict_ensemble.py`.
- Tách config PDA/DDT khỏi `config/scoring.yaml` production hoặc đặt trong namespace không được ensemble engine đọc, nhằm ngăn tác động ngầm lên weights/selectors hiện tại.
- Repository mới chỉ đọc `tails_2d`, dùng keyset pagination, filter `region=XSMN`, đúng target provinces và `draw_date < target_date`.
- V1 ưu tiên không thêm bảng/cột; shadow audit có thể được log hoặc ghi artifact độc lập. Bất kỳ persistence contract mới nào phải là story/migration riêng và không thay unique key hiện tại.
- Thêm regression guard chứng minh XSMB entrypoints và sáu XSMN model modules không import PDA/DDT; cùng input production phải cho Top 3 giống baseline khi shadow bật, tắt hoặc lỗi.
- Backtest/calibration tooling phải tách khỏi daily inference, fit chỉ trên folds trước target và đóng băng calibration artifact/version trước mỗi prediction fold.
- Test fixture phải bao phủ TP.HCM dual route, sparse province support, tie dominant digits, unit distribution sum-to-one, positive/negative coupling lift và Fréchet bounds.
- Code review phải kiểm tra riêng xác suất union/intersection để tránh double-count unit evidence và xác nhận selector không thể chọn ba pairs cùng suffix.

### UX Design Requirements

Không áp dụng. Feature không tạo UI mới; output quan sát qua logs/audit và notification contract hiện có chỉ được thay đổi khi có approval riêng.

### FR Coverage Map

FR1: Epic 1 - Predictor chỉ áp dụng cho XSMN.
FR2: Epic 1 - Resolve và suy luận province-first.
FR3: Epic 1 - Chỉ dùng complete 18-prize draw.
FR4: Epic 1 - Strict cutoff và same-province next label.
FR5: Epic 1 - Tạo head/unit histograms, multiplicity và coverage.
FR6: Epic 1 - Tạo dynamic state đầy đủ.
FR7: Epic 1 - Static provincial prior và hierarchical shrinkage.
FR8: Epic 1 - TP.HCM route-specific transition.
FR9: Epic 1 - Unit distribution, leader probability và confidence.
FR10: Epic 1 - Chấm đủ 100 ordered pairs.
FR11: Epic 1 - Shrunk residual pair interaction.
FR12: Epic 1 - Province outputs độc lập và có attribution.
FR13: Epic 1 - Merge unit-share vectors.
FR14: Epic 1 - Probability union/intersection merge.
FR15: Epic 1 - Hierarchical coupling lift và Fréchet bounds.
FR16: Epic 1 - Ngăn double-count unit evidence.
FR17: Epic 1 - Top 3 suffix constraints.
FR18: Epic 1 - Tối ưu allocation `2+1` và `1+1+1`.
FR19: Epic 1 - Evidence/calibration gates và abstention.
FR20: Epic 1 - Top 3/10/100 audit decomposition.
FR21: Epic 1 - Deterministic inference.
FR22: Epic 2 - Gọi shadow service trong XSMN orchestration.
FR23: Epic 2 - Chạy cùng workflow 07:14 Việt Nam.
FR24: Epic 2 - Fault isolation khỏi production result.
FR25: Epic 2 - Không tham gia production ensemble.
FR26: Epic 2 - Tôn trọng dry-run semantics.
FR27: Epic 1 - Expanding walk-forward evaluation.
FR28: Epic 1 - Out-of-fold calibration và probability naming.
FR29: Epic 1 - Permutation controls.
FR30: Epic 2 - Không auto-promote hoặc thay production contract.

## Epic List

### Epic 1: Tạo Top 3 XSMN động theo tỉnh có kiểm chứng

Data analyst có thể chạy PDA/DDT độc lập để nhận phân phối digit kỳ sau theo
từng tỉnh, 100 pair likelihoods, merged two-province ranking, constrained Top 3,
full audit và walk-forward evidence mà chưa cần tích hợp production.

**FRs covered:** FR1-FR21, FR27-FR29.

**Implementation boundary:** Epic này sở hữu namespace/module và CLI shadow mới.
Không sửa six-model ensemble, production selector, workflow schedule hoặc Top 3
storage contract. Output chưa đạt calibration gate phải dùng tên likelihood chưa
calibration.

### Epic 2: Vận hành PDA/DDT shadow hằng ngày an toàn

Operator có thể nhận kết quả PDA/DDT trong cùng lần generate prediction XSMN lúc
07:14 Việt Nam, với lỗi shadow được cô lập và Top 3 production giữ nguyên khi
feature bật, tắt, thiếu dữ liệu hoặc thất bại.

**FRs covered:** FR22-FR26, FR30.

**Implementation boundary:** Epic này chỉ tích hợp public service của Epic 1 vào
XSMN orchestration và regression coverage. Không tạo cron/workflow mới, không
tham gia weights/consensus/selector, không auto-promote và không thay hành vi XSMB.
