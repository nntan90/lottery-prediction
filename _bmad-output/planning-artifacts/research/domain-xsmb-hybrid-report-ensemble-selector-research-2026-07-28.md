---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: []
workflowType: 'research'
lastStep: 6
research_type: 'domain'
research_topic: 'XSMB hybrid statistical-report and ensemble combo selector'
research_goals: 'Kết hợp báo cáo lô tô và logic ensemble để tối ưu KPI trúng ít nhất 2/3 số bằng walk-forward validation, không data leakage hoặc double-count.'
user_name: 'tannguyen'
date: '2026-07-28'
web_research_enabled: true
source_verification: true
---

# Từ “Top 3” đến bằng chứng ngoài mẫu: Nghiên cứu toàn diện XSMB Hybrid Statistical Report và Combo Selector

**Date:** 2026-07-28
**Author:** tannguyen
**Research Type:** Domain

---

## Executive Summary

Nghiên cứu kết luận rằng không nên “kết hợp báo cáo vào ensemble” bằng cách cộng thêm một vote. `Top 3 VIP` của báo cáo đã được production sử dụng qua wrapper `loto_statistical`; cộng lại sẽ double-count cùng nguồn frequency/gap. Vấn đề cốt lõi của v5.1 không phải thiếu model mà là **objective mismatch**: production cộng marginal score, consensus, history modifier và unit-digit diversity, trong khi tiêu chí thành công thực tế là cả bộ ba phải khớp ít nhất hai số.

Giải pháp được đề xuất là **Hybrid Combo Selector v6** chạy challenger/shadow: các sub-model xuất full vector 100 số, được nhóm theo `source_family`, calibration bằng walk-forward OOF, sau đó ước lượng joint probability có điều kiện và chấm toàn bộ `C(100,3)=161.700` bộ theo `P(≥2)=P(ab)+P(ac)+P(bc)-2P(abc)`. Không selector nào được thay production nếu chưa vượt random, report-only, v5.1 và current combo shadow trên frozen holdout với confidence interval và kiểm soát multiple testing.

Sản phẩm phải được định vị là thống kê thử nghiệm có audit trail, không phải lời hứa trúng. Telegram cần tách thống kê mô tả khỏi bộ ba thử nghiệm; score chưa calibration phải ghi “điểm mô hình — không phải xác suất”. Prediction phải được freeze trước kỳ quay cùng cutoff, model/config/code hash và được đánh giá đầy đủ `0/3–3/3`.

**Key findings:**

- XSMB được xác định pháp lý là sự kiện có kết quả ngẫu nhiên; model chỉ có giá trị nếu chứng minh lift ngoài mẫu.
- Baseline iid tham khảo cho một bộ ba cố định đạt ít nhất `2/3` trên 27 lượt đuôi là khoảng `14,005%`; baseline production phải dùng random/permutation bảo toàn cấu trúc draw thực tế.
- Report Top 3 và `loto_statistical` là cùng một scorer, không phải hai bằng chứng độc lập.
- Frequency, CDM, ChiGOF và LotoStat có overlap lớn; raw vote count đang phóng đại consensus.
- Combo shadow tối ưu đúng utility hơn nhưng dùng joint history vô điều kiện và mặc định không nhận credibility weights production.
- Có các correctness defect phải sửa trước khi cải tiến model: medal/score ordering, Markov numeric-sort bias và ChiGOF sum-cardinality bias.

**Strategic recommendations:**

1. Sửa correctness và xây immutable prediction ledger trước.
2. Mở combo selector ở shadow, lưu/evaluate hằng ngày, không ảnh hưởng function cũ.
3. Bổ sung full score vector và `source_family` theo hướng additive compatibility.
4. Tạo walk-forward OOF warehouse, calibration và conditional joint challenger.
5. Chỉ promotion khi frozen holdout chứng minh lift ổn định; nếu không, giữ v5.1 và công bố “chưa có bằng chứng edge”.

## Table of Contents

1. [Research Overview](#research-overview)
2. [Domain Research Scope Confirmation](#domain-research-scope-confirmation)
3. [Industry Analysis](#industry-analysis)
4. [Competitive Landscape](#competitive-landscape)
5. [Regulatory Requirements](#regulatory-requirements)
6. [Technical Trends and Innovation](#technical-trends-and-innovation)
7. [Recommendations](#recommendations)
8. [Research Synthesis and Strategic Decision](#research-synthesis-and-strategic-decision)
9. [Implementation and Risk Framework](#implementation-and-risk-framework)
10. [Methodology, Sources and Limitations](#methodology-sources-and-limitations)
11. [Research Conclusion](#research-conclusion)

## Research Overview

Nghiên cứu xem xét toàn bộ chuỗi XSMB từ dữ liệu chính thức, báo cáo thống kê, sub-model, ensemble, combo selector, evaluation, database ledger đến Telegram. Mục tiêu không phải tìm thêm một heuristic “chốt số”, mà xác định cách tối ưu đúng KPI `hit_count ≥ 2` mà không leakage, double-count hoặc biến heuristic score thành probability.

Phương pháp kết hợp nguồn pháp lý/chính thức, nghiên cứu thống kê và machine learning, khảo sát sản phẩm cạnh tranh và audit code production. Mọi kết luận được tách thành evidence, inference và hypothesis cần backtest. Các nguồn động đã được xác minh tại thời điểm 28/07/2026; kết luận kỹ thuật được đối chiếu trực tiếp với implementation hiện tại.

Kết luận điều hành và quyết định kiến trúc nằm trong [Research Synthesis and Strategic Decision](#research-synthesis-and-strategic-decision); các bằng chứng chi tiết được giữ trong từng phần phía dưới để phục vụ code review, PRD và implementation.

---

<!-- Content will be appended sequentially through research workflow steps -->

## Domain Research Scope Confirmation

**Research Topic:** XSMB hybrid statistical-report and ensemble combo selector
**Research Goals:** Kết hợp báo cáo lô tô và logic ensemble để tối ưu KPI trúng ít nhất 2/3 số bằng walk-forward validation, không data leakage hoặc double-count.

**Domain Research Scope:**

- Cơ chế và quy định xổ số — cấu trúc kỳ quay, không gian kết quả và điều kiện kiểm chứng.
- Nền tảng thống kê — baseline ngẫu nhiên, multiple testing, calibration và độ ổn định.
- Technology patterns — ensemble, joint/combo scoring, feature overlap và consensus.
- Economic/risk factors — cách diễn giải score và giới hạn của dự đoán trò chơi may rủi.
- Data pipeline — cutoff, walk-forward, model registry và quan hệ report–generator–verification.

**Research Methodology:**

- Các claim nền tảng được xác minh bằng nguồn công khai hiện hành.
- Kết luận về selector được kiểm chứng bằng dữ liệu và code production nội bộ.
- Tách rõ evidence, inference và giả thuyết cần backtest.
- KPI chính là `hit_count >= 2` trên bộ ba canonical.

**Scope Confirmed:** 2026-07-28

## Industry Analysis

### Market Size and Valuation

Không tìm thấy số liệu công khai đủ tin cậy để tách riêng quy mô doanh thu XSMB khỏi toàn bộ hoạt động xổ số truyền thống miền Bắc; vì vậy nghiên cứu không sử dụng một con số “market size” suy diễn. Về cấu trúc kinh tế, đây là thị trường được cấp phép và kiểm soát bởi Nhà nước, không phải thị trường dự báo mở: doanh nghiệp xổ số phải được cấp giấy chứng nhận, là công ty TNHH MTV do Nhà nước sở hữu 100%, phân phối trực tiếp hoặc qua đại lý. Nguồn pháp lý hiện hành xác định kinh doanh xổ số dựa trên một sự kiện có kết quả ngẫu nhiên và phải bảo đảm minh bạch, khách quan, trung thực.

_Total Market Size:_ Chưa có dữ liệu chính thức tách riêng XSMB; confidence thấp nếu ước lượng.
_Market Segments:_ đơn vị phát hành liên kết miền Bắc; đại lý/phân phối; người mua vé; nhà cung cấp dữ liệu kết quả; dịch vụ phân tích không thuộc khâu quay số.
_Economic Impact:_ nguồn thu xổ số thuộc cơ chế tài chính Nhà nước; sản phẩm phân tích không có quyền truy cập ưu tiên vào quá trình quay.
_Sources:_ [Nghị định 30/2007/NĐ-CP](https://vbpl.vn/botaichinh/Pages/vbpq-toanvan.aspx?ItemID=14346), [Thông tư 75/2013/TT-BTC và tình trạng hiệu lực](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=71274&Keyword=)

### Market Dynamics and Growth

XSMB phát hành hằng ngày và kết quả truyền thống tạo 27 số giải mỗi kỳ; với bài toán lô tô hai chữ số, pipeline quan sát 27 đuôi trong không gian `00–99`, trong đó một số có thể lặp lại. Nguồn dữ liệu lớn dần theo ngày nhưng tín hiệu dự báo không tự tăng tương ứng: nếu cơ chế quay giữ tính ngẫu nhiên và integrity, thêm lịch sử chủ yếu làm giảm sai số ước lượng của baseline và giúp phát hiện model overfit.

_Growth Drivers:_ số kỳ tích lũy, tự động hóa crawl/verification, nhu cầu theo dõi thống kê.
_Growth Barriers:_ kết quả được thiết kế ngẫu nhiên; mọi “edge” phải vượt baseline ngoài mẫu và sau điều chỉnh data snooping.
_Cyclical Patterns:_ lịch quay hằng ngày tạo weekday labels, nhưng weekday chỉ là feature ứng viên, không mặc nhiên có predictive power.
_Market Maturity:_ hoạt động xổ số truyền thống trưởng thành và quản lý chặt; ML/prediction là lớp phân tích ngoại vi.
_Sources:_ [Giới thiệu xổ số truyền thống – Xổ số Thủ đô](https://xosothudo.com.vn/tin/tin-tuc/7157/gioi-thieu-xo-so-truyen-thong.html), [Giới thiệu vận hành Xổ số Thủ đô](https://xosothudo.com.vn/tin/tin-tuc/7194/gioi-thieu-khai-quat-ve-cong-ty-tnhh-mot-thanh-vien-xo-so-kien-thiet-thu-do.html)

### Market Structure and Segmentation

Miền Bắc áp dụng cơ chế liên kết phát hành: các công ty trong khu vực cùng phát hành bộ vé, thống nhất cơ cấu giải và quay chung. Do đó XSMB nên được mô hình hóa như một chuỗi daily thống nhất, khác XSMN phải tách `province + weekday`. Ground truth phải đến từ kết quả công bố chính thức; website thống kê bên thứ ba chỉ nên là nguồn dự phòng/đối soát.

_Primary Segments:_ phát hành/giám sát; phân phối vé; công bố kết quả; phân tích dữ liệu.
_Geographic Distribution:_ liên kết khu vực miền Bắc, không phải nhiều draw độc lập theo tỉnh trong cùng ngày.
_Vertical Integration:_ đơn vị được cấp phép kiểm soát phát hành và công bố; hệ thống dự đoán chỉ sử dụng dữ liệu hậu kiểm công khai.
_Source:_ [Quy định liên kết phát hành khu vực](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=33596&Keyword=)

### Industry Trends and Evolution

Xu hướng đáng tin cậy không phải “AI dự đoán chắc hơn”, mà là tăng auditability: cutoff rõ ràng, registry version, walk-forward, calibration, báo cáo responsible-gaming và phân biệt thống kê mô tả với dự báo. Thông tư 22/2021/TT-BTC, được sửa năm 2025, yêu cầu Hội đồng giám sát kiểm tra lồng cầu, bóng, thiết bị, cân, niêm phong, camera, chọn ngẫu nhiên bộ bóng và có quyền đình chỉ khi không bảo đảm khách quan/trung thực. Chuẩn WLA-SCS 2024 cũng coi integrity và audit trail là nền tảng; đây là benchmark quốc tế, không phải bằng chứng XSMB được WLA chứng nhận.

_Emerging Trends:_ automated verification, model monitoring, calibration và responsible messaging.
_Technology Integration:_ ML chỉ xếp hạng tín hiệu từ lịch sử; không can thiệp hoặc quan sát entropy của draw.
_Future Outlook:_ selector đáng tin cậy phải chuyển từ Top-3 marginal sang set-level utility và công bố uncertainty.
_Sources:_ [Thông tư 22/2021/TT-BTC](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=150990), [Thông tư 38/2025/TT-BTC](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=178473), [WLA-SCS 2024](https://publications.world-lotteries.org/security-and-risk-management/wla-scs-2024-code-of-practice)

### Competitive Dynamics

Trong khâu phát hành, mức cạnh tranh thấp vì cấu trúc cấp phép và liên kết vùng. Trong lớp phân tích, cạnh tranh chủ yếu là khả năng trình bày thống kê và thu hút người dùng; không có bằng chứng công khai rằng một nhà cung cấp có informational advantage hợp pháp đối với kết quả quay. Rào cản kỹ thuật thực sự là chứng minh lift ngoài mẫu, không phải số lượng model.

_Market Concentration:_ tập trung ở các doanh nghiệp xổ số Nhà nước và Hội đồng liên kết.
_Competitive Intensity:_ cao ở lớp nội dung/thống kê, thấp ở quyền tổ chức draw.
_Barriers to Entry:_ pháp lý cao cho kinh doanh xổ số; kỹ thuật vừa cho analytics nhưng rất cao để chứng minh predictive edge.
_Innovation Pressure:_ minh bạch score, chống overfit và cảnh báo có trách nhiệm quan trọng hơn thêm feature.
_Sources:_ [Nghị định 30/2007/NĐ-CP](https://vbpl.vn/botaichinh/Pages/vbpq-toanvan.aspx?ItemID=14346), [WLA Responsible Gaming Principles](https://world-lotteries.org/services/industry-standards/responsible-gaming/principles)

### Implications for the XSMB Selector

- “Nóng 43,3%” phải được diễn giải là “xuất hiện trong 13/30 kỳ”, không phải xác suất kỳ tới.
- Các score `0.103`, `0.138`, `0.081` chỉ là model scores nếu chưa calibration ngoài mẫu.
- “Đồng thuận 5 model” không tương đương năm bằng chứng độc lập vì các model dùng chung lịch sử và feature.
- Telegram nên tách “Thống kê mô tả” khỏi “Bộ 3 thử nghiệm”, ghi cutoff/nguồn và điều kiện đánh giá `≥2/3`.
- Không dùng wording “chắc”, “chuẩn” hoặc “VIP” như một bảo đảm tài chính; Nghị định 30 cấm quảng cáo trúng thưởng như kết quả đương nhiên hoặc gợi ý chơi xổ số cải thiện tài chính.

## Competitive Landscape

### Key Players and Market Leaders

Thị trường công khai gồm hai lớp không nên trộn lẫn. Xổ số Thủ đô là nguồn phát hành/kết quả chính thức và không cung cấp “số nên chọn”. Các cổng thống kê như Xoso.me và SXMB.com cung cấp nóng/lạnh, gan, đầu/đuôi, lô rơi và nội dung “chốt số”. Nhóm công cụ quốc tế thường chia thành hai chiến lược: marketing dự báo bằng historical patterns, hoặc định vị thận trọng là analytics/generator không thay đổi odds.

_Official Ground Truth:_ [Xổ số Thủ đô](https://xosothudo.com.vn/).
_Statistics Portals:_ [Xoso.me – lô gan](https://xoso.me/thong-ke-lo-gan-xo-so-mien-bac-xsmb.html), [Xoso.me – tần suất](https://xoso.me/thong-ke-tan-suat-lo-to-xo-so-mien-bac-xsmb.html), [SXMB.com](https://sxmb.com/).
_Transparent Analytics Positioning:_ [Lotto Strategy Lab](https://lottostrategylab.com/) và [Drawtronic](https://drawtronic.com/) đều nói rõ draw ngẫu nhiên và công cụ không làm tăng odds/predict chắc chắn.
_Marketing-Led Forecasting:_ [Brainlotto](https://brainlotto.com/) và nhiều trang “AI” nhấn mạnh hot/cold/overdue nhưng không công khai kiểm toán predictive edge.

### Market Share and Competitive Positioning

Không có dữ liệu thị phần đáng tin cậy cho dịch vụ thống kê/dự đoán XSMB. Trong mẫu khảo sát, khác biệt chủ yếu nằm ở tốc độ dữ liệu, số lượng công cụ, ngôn ngữ “AI/VIP”, khả năng cá nhân hóa và mức minh bạch backtest. Không tìm thấy dịch vụ nào công bố hiệu suất XSMB được kiểm toán độc lập với prediction freeze, denominator đầy đủ, baseline, confidence interval và kiểm soát multiple testing.

_Positioning 1 — Result/SEO:_ miễn phí, nhiều trang thống kê và nội dung hằng ngày.
_Positioning 2 — “AI/VIP”:_ bán cảm giác chắc chắn nhưng model/version/calibration thường không công khai.
_Positioning 3 — Strategy Lab:_ cho phép backtest và lựa chọn constraint, nhưng dễ data-snooping nếu người dùng thử nhiều cấu hình.
_Positioning Opportunity:_ prediction ledger đóng dấu trước kỳ, walk-forward, random baseline và KPI `≥2/3`.
_Source sample:_ [Rada Số backtest](https://radarso.vn/backtest), [Rada Số pricing](https://radarso.vn/pricing).

### Internal Competitive Map

Hệ thống hiện có ba selector thực sự cạnh tranh:

1. **Top 3 VIP/report:** rule-based trên gap, đầu/đuôi/chạm, hot-30 và lô rơi.
2. **Production v5.1:** fusion Top 5 của Frequency, Markov², ChiGOF, CDM và LotoStat, sau đó consensus/history/recency và unit-digit diversity.
3. **Combo shadow:** selector duy nhất tối ưu trực tiếp utility `hit_count ≥ 2`, nhưng đang mặc định `off`/`shadow` và chưa thay production.

Top 3 VIP không phải nguồn thứ sáu độc lập: `loto_statistical` gọi lại đúng `suggest_top_3_dan_so()`. Vì vậy “kết hợp báo cáo vào ensemble” đã xảy ra; cộng thêm bonus report lần nữa sẽ double-count cùng scorer. [Loto analyzer](../../../src/xsmb_ensemble/xsmb_loto_analyzer.py#L322), [LotoStat wrapper](../../../src/xsmb_ensemble/model_loto_statistical.py#L46), [combo shadow call](../../../src/scripts/predict_ensemble.py#L827).

### Competitive Strategies and Differentiation

**Production v5.1** khác biệt bằng credibility weights 30 kỳ, proportional fusion và consensus. Tuy nhiên Frequency, CDM, ChiGOF và LotoStat đều tái dùng frequency; Frequency, ChiGOF và LotoStat cùng dùng gap. Consensus hiện đếm chúng như model độc lập nên có thể khuếch đại feature overlap thay vì diversity thật.

**Combo selector** có lợi thế đúng objective: nó chấm cả bộ ba bằng pair/triple evidence. Nhược điểm hiện tại là shadow không nhận credibility weights production, nên comparison chưa apples-to-apples.

**External tools** khác biệt bằng nhiều filter và UX; nhưng số lượng “lens/model” không chứng minh hiệu suất nếu không có OOS ledger. Một sản phẩm nghiêm túc nên cạnh tranh bằng auditability, không bằng số lượng thuật toán.

_Internal sources:_ [v5.1 aggregation](../../../src/xsmb_ensemble/ensemble_engine.py#L292), [consensus amplifier](../../../src/xsmb_ensemble/ensemble_engine.py#L351), [credibility weights](../../../src/scripts/predict_ensemble.py#L764).

### Business Models and Value Propositions

Các mô hình bên ngoài gồm free/SEO, quảng cáo trong app, freemium/subscription và nội dung “chuyên gia”. Giá trị công khai thường là tiện lợi, visualization và structured picks, không phải hiệu suất được kiểm toán. Hệ thống nội bộ nên định vị là experimental statistical monitoring: dữ liệu chính thức, reproducible selector, ledger và evaluation `0/3–3/3`.

_Primary Business Models:_ free traffic, ad-supported app, paid strategy lab/subscription.
_Customer Relationship:_ daily notification, saved strategies và historical results.
_Responsible Value Proposition:_ giải thích, audit và kiểm soát rủi ro thay vì bảo đảm trúng.

### Competitive Dynamics and Entry Barriers

Rào cản tạo một trang “dự đoán” thấp; rào cản chứng minh edge thật rất cao. Competitor có thể cherry-pick ngày thắng, đổi logic không version hoặc backtest sau khi xem kết quả. Lợi thế bền vững của hệ thống phải là freeze-before-draw, immutable version, walk-forward và public denominator.

_Barriers to Entry:_ thấp cho content; cao cho verified performance.
_Switching Costs:_ thấp nếu chỉ cung cấp con số; cao hơn nếu có ledger, explainability và API.
_Competitive Intensity:_ cao về marketing, thấp về independent scientific validation.

### Ecosystem and Partnership Analysis

Ground truth do đơn vị xổ số chính thức kiểm soát; hệ thống phụ thuộc crawler, Supabase, model registry, GitHub Actions và Telegram. Không thành phần nào trong analytics được có quyền sửa kết quả hậu kiểm. Telegram là distribution channel, không phải evidence store; prediction cần được lưu/hash trước giờ quay để chống sửa hậu nghiệm.

_Ecosystem Control:_ official operator controls draw/result; analytics controls only transformation and presentation.
_Technology Dependencies:_ source ingestion, cutoff enforcement, model artifacts, immutable logs và verification.

### Critical Product Defects Exposed by the Comparison

- **Ranking/score mismatch:** production áp unit-digit diversity rồi giữ thứ tự theo nhóm đuôi, không sort lại theo score. Formatter gắn 🥇🥈🥉 theo thứ tự nhận vào; vì thế `60 (0.103)` đứng trên `83 (0.138)`. [Diversity selection](../../../src/xsmb_ensemble/ensemble_engine.py#L427), [Telegram medals](../../../src/bot/ensemble_messages.py#L88).
- **Documentation drift:** header v5.1 nói “Pure Top 3, no artificial diversity”, nhưng code vẫn ép unit-digit diversity. [v5.1 contract](../../../src/xsmb_ensemble/ensemble_engine.py#L13).
- **Misnamed probabilities:** heuristic final scores được lưu trong trường `prob_*` dù chưa calibrated.
- **Markov candidate bias:** code mô tả “top frequent pairs” nhưng dùng `sorted(... )[:15]`, tức ưu tiên giá trị số nhỏ thay vì frequency.
- **ChiGOF sum bias:** 19 nhóm tổng không có cùng số lượng cặp; dùng expected count bằng nhau khiến tổng 8/9/10 dễ trông “mạnh” do combinatorics.
- **Objective mismatch:** production tối ưu marginal ranking và diversity hình thức; combo selector tối ưu `≥2/3` nhưng chưa production.

## Regulatory Requirements

### Applicable Regulations

**Nghị định 30/2007/NĐ-CP** là khung pháp lý nền tảng và đang còn hiệu lực. Nghị định định nghĩa kinh doanh xổ số là hoạt động dựa trên sự kiện có kết quả ngẫu nhiên; chỉ doanh nghiệp được cấp giấy chứng nhận đủ điều kiện mới được tổ chức kinh doanh xổ số. Điều 6 cấm tổ chức kinh doanh xổ số trái phép và cấm sử dụng kết quả xổ số để tổ chức chương trình dự thưởng. Điều 18 yêu cầu thông tin xổ số chính xác, kịp thời và từ nguồn có thẩm quyền. Đặc biệt, Điều 21 cấm quảng cáo việc trúng thưởng là kết quả đương nhiên hoặc việc tham gia sẽ cải thiện tình hình tài chính; Điều 22 cấm khuyến mại về xổ số dưới mọi hình thức. [Nguồn chính thức](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=14346).

**Thông tư 22/2021/TT-BTC**, có hiệu lực từ 15/05/2021 và được sửa đổi bởi **Thông tư 38/2025/TT-BTC** từ 01/07/2025, quy định cơ chế Hội đồng giám sát. Các kiểm soát gồm kiểm tra lồng cầu, bóng, cân và thiết bị; niêm phong; camera; chọn ngẫu nhiên bộ bóng; lập biên bản và đình chỉ khi không bảo đảm khách quan, trung thực. Điều này củng cố giả định nghiệp vụ rằng analytics chỉ quan sát lịch sử công khai, không có informational advantage đối với entropy của kỳ quay. [Thông tư 22](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=150990), [Thông tư 38](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=178473).

**Ranh giới áp dụng cho hệ thống:** bot chỉ nên cung cấp thống kê và bộ số thử nghiệm, không bán vé, nhận tiền cược, trả thưởng hoặc tổ chức chương trình dự thưởng dựa trên kết quả. Nếu sản phẩm mở rộng sang thu tiền theo kết quả, affiliate bán vé, khuyến mại hoặc chức năng đặt cược, cần legal review riêng trước khi triển khai. Đây là suy luận tuân thủ từ phạm vi và các hành vi bị cấm của Nghị định 30, không phải ý kiến pháp lý chính thức.

### Industry Standards and Best Practices

[WLA Responsible Gaming Principles](https://world-lotteries.org/services/industry-standards/responsible-gaming/principles) yêu cầu thông tin chính xác, cân bằng để người dùng đưa ra lựa chọn có hiểu biết; chỉ khuyến khích hoạt động hợp pháp/có trách nhiệm; theo dõi, thử nghiệm, điều chỉnh và công bố kết quả. [WLA Responsible Gaming Standard for Associate Members](https://publications.world-lotteries.org/responsible-gaming/wla-responsible-gaming-standards-for-associate-members) mở rộng thực hành này cho nhà cung cấp công nghệ: đánh giá rủi ro sản phẩm, kiểm soát dữ liệu, responsible marketing, client awareness và measurement/reporting.

Đây là **benchmark tự nguyện**, không thay thế pháp luật Việt Nam và không phải bằng chứng hệ thống hoặc XSMB đã được WLA chứng nhận. Tương tự, [WLA-SCS 2024](https://publications.world-lotteries.org/security-and-risk-management/wla-scs-2024-code-of-practice) chỉ nên được dùng làm tham chiếu cho integrity, access control và audit trail.

### Compliance Frameworks

Nên áp dụng một compliance gate nhẹ nhưng bắt buộc cho mỗi phiên bản selector:

1. **Data gate:** ground truth từ nguồn chính thức hoặc được đối soát; cutoff trước kỳ quay; không sử dụng dữ liệu tương lai.
2. **Model gate:** walk-forward OOF, baseline đầy đủ, confidence interval, calibration và frozen holdout.
3. **Message gate:** không dùng “chắc”, “chuẩn”, “cam kết”, “cơ hội đổi đời”; không gọi heuristic score là xác suất.
4. **Audit gate:** lưu timestamp, data cutoff, model/config version, source hash và bộ ba đã phát hành trước giờ quay.
5. **Responsible-gaming gate:** nêu kết quả ngẫu nhiên, nội dung chỉ để tham khảo/thử nghiệm và đánh giá theo denominator đầy đủ.
6. **Change gate:** mọi promotion từ shadow sang production phải có code review, legal-copy review và báo cáo walk-forward chứng minh không suy giảm.

### Data Protection and Privacy

Từ 01/01/2026, **Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15** có hiệu lực và đang còn hiệu lực. Cùng ngày, **Nghị định 356/2025/NĐ-CP** có hiệu lực và thay thế Nghị định 13/2023/NĐ-CP. Nghị định 356 liệt kê số điện thoại và thông tin tài khoản số là dữ liệu cá nhân cơ bản, đồng thời quy định các biện pháp triển khai, nhân sự, đánh giá tuân thủ, bảo mật và ứng cứu sự cố. [Luật 91/2025/QH15](https://vbpl.vn/bocongan/Pages/vbpq-thuoctinh.aspx?ItemID=179252&Keyword=), [Nghị định 356/2025/NĐ-CP](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187276).

Trong phạm vi hiện tại, `Telegram chat_id`, username/tài khoản số, lịch sử chấp thuận trigger và notification logs có thể gắn với một cá nhân. Hệ thống nên:

- chỉ lưu `chat_id` và metadata thực sự cần cho gửi thông báo/ủy quyền;
- công bố mục đích xử lý, thời hạn lưu và cách người dùng dừng/xóa đăng ký;
- tách secrets (`TELEGRAM_BOT_TOKEN`, `SUPABASE_SERVICE_KEY`) khỏi code, giới hạn quyền và rotate khi lộ;
- mã hóa khi truyền, áp dụng row-level/access control, audit truy cập và retention/deletion job;
- không đưa chat ID, token hoặc nội dung cá nhân vào model features, log công khai hay Telegram error trace;
- đánh giá lại nghĩa vụ hồ sơ tác động/chuyển dữ liệu khi quy mô, loại dữ liệu hoặc nhà cung cấp xuyên biên giới thay đổi.

### Licensing and Certification

Giấy chứng nhận đủ điều kiện kinh doanh xổ số theo Nghị định 30 áp dụng cho chủ thể tổ chức kinh doanh xổ số; hệ thống analytics hiện không nên tự nhận là đơn vị xổ số, đại lý hoặc bên được cấp phép. Không tìm thấy yêu cầu chứng nhận ML riêng cho công cụ thống kê XSMB trong các nguồn đã kiểm tra. Tuy nhiên, việc không cần giấy phép tổ chức xổ số cho analytics thuần túy không loại bỏ nghĩa vụ về nội dung, dữ liệu cá nhân, an ninh và ranh giới chống kinh doanh xổ số trái phép.

Không được dùng logo/cụm từ “WLA certified”, “official XSMB prediction” hoặc ngụ ý liên kết với Xổ số Thủ đô nếu chưa có ủy quyền/chứng nhận thực tế. Nếu muốn WLA Responsible Gaming certification, tiêu chuẩn yêu cầu đánh giá độc lập định kỳ; nghiên cứu này không xác nhận hệ thống đủ điều kiện hoặc đã đăng ký.

### Implementation Considerations

**Telegram copy đề xuất:**

- `📊 THỐNG KÊ MÔ TẢ XSMB` cho nóng/gan/rơi/đầu/đuôi; hiển thị `13/30 kỳ`, không viết `43,3% cơ hội kỳ tới`.
- `🧪 BỘ 3 THỬ NGHIỆM` cho output selector; ghi rõ `Điều kiện đánh giá: xuất hiện ít nhất 2/3 số`.
- Thay `0.138` bằng `Điểm mô hình: 0.138 (không phải xác suất)` cho đến khi score được calibration OOF và định nghĩa probability target rõ ràng.
- Thay `Top 3 VIP` bằng `Top 3 thống kê`; thay `DỰ ĐOÁN CHÍNH` bằng `Bộ 3 thử nghiệm`.
- Footer tối thiểu: `Nguồn kết quả`, `data cutoff`, `selector/version`, `phát hành lúc`, `kết quả có tính ngẫu nhiên`.

**Evidence ledger:** prediction phải được lưu trước kỳ quay với canonical sorted set, score components, config/model hashes và Telegram message ID. Evaluation phải ghi cả `0/3`, `1/3`, `2/3`, `3/3`; không chỉ phát ngày thắng. Một message formatter không được tự đổi thứ tự medal nếu score giảm dần là contract.

### Risk Assessment

| Rủi ro | Mức độ | Kiểm soát bắt buộc |
|---|---:|---|
| Wording khiến người dùng hiểu score là xác suất/lời hứa trúng | Cao | Message gate, disclaimer, cấm “chuẩn/chắc/VIP”, legal-copy review |
| Data leakage hoặc prediction được sửa sau giờ quay | Cao | Cutoff enforcement, immutable ledger, timestamp/hash trước kỳ |
| Double-count Top 3 report/LotoStat và consensus giả do feature overlap | Cao | Source-family attribution, diversity-aware weights, ablation |
| Khuyến khích hành vi chơi có hại hoặc kỳ vọng cải thiện tài chính | Cao | Accurate/balanced copy, responsible-gaming footer, không financial claims |
| Vượt ranh giới analytics sang nhận cược/dự thưởng trái phép | Cao | Không thu tiền theo kết quả/không trả thưởng; legal review trước mở rộng |
| Lộ Telegram chat ID, bot token hoặc Supabase service key | Cao | Data minimization, secret manager/env, least privilege, rotation, retention |
| Tuyên bố chứng nhận/quan hệ chính thức không có thật | Trung bình | Chỉ dùng nhãn đã được chứng minh và có hồ sơ |
| Backtest lift do thử quá nhiều cấu hình | Cao | Frozen holdout, multiple-testing control, preregister promotion criteria |

**Regulatory confidence:** cao đối với tình trạng/điều khoản được trích từ CSDL quốc gia về VBPL và WLA; trung bình đối với ranh giới giấy phép của một sản phẩm analytics cụ thể, vì còn phụ thuộc mô hình thương mại, luồng tiền và wording thực tế. Trước khi thương mại hóa, cần tư vấn pháp lý tại Việt Nam.

## Technical Trends and Innovation

### Emerging Technologies

**Set-valued và decision-focused prediction** là hướng phù hợp nhất. Thay vì chọn ba marginal score cao nhất, hệ thống tối ưu trực tiếp utility của tập:

`U({a,b,c},Y) = 1[|{a,b,c} ∩ Y| ≥ 2]`.

Nghiên cứu set-valued prediction xem output là một tập và tối ưu loss/utility của cả tập, thay vì chỉ tối ưu từng nhãn độc lập. Nghiên cứu top-k gần đây cũng nhấn mạnh việc objective phải phản ánh cardinality của output. Với XSMB, cardinality đã cố định bằng ba; phần cần học là joint utility của từng tổ hợp. [Efficient Set-Valued Prediction](https://arxiv.org/abs/1906.08129), [Cardinality-Aware Set Prediction and Top-k Classification](https://arxiv.org/abs/2407.07140).

**Probability calibration ngoài mẫu** có giá trị cao hơn việc thêm một model mới. Scikit-learn định nghĩa một output `0.8` chỉ có nghĩa xác suất khi khoảng 80% mẫu tương ứng thực sự dương tính; calibrator phải được fit trên dữ liệu độc lập với dữ liệu huấn luyện model để tránh optimistic bias. Với XSMB, mọi mapping từ score sang xác suất phải dùng walk-forward OOF theo ngày. [Scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html).

**Empirical Bayes/shrinkage cho joint events** phù hợp với dữ liệu hiếm. Cặp xuất hiện đồng thời đã thưa; bộ ba đồng thời còn thưa hơn. Estimator hiện tại đã shrink pair/triple frequency về random-draw prior, nhưng objective vẫn là joint frequency vô điều kiện. Bước tiến tiếp theo là dùng joint history như prior/lift, sau đó kết hợp với tín hiệu hiện tại đã calibration.

**Conformal prediction và online uncertainty** là xu hướng nghiên cứu 2024–2026, gồm conformal set, adaptive/temporal calibration và false-coverage control sau selection. Tuy nhiên, các bảo đảm phụ thuộc assumption/calibration protocol và thường tạo tập có kích thước biến đổi; chúng không tự tạo edge và không trực tiếp bảo đảm một bộ cố định ba số sẽ trúng `≥2/3`. Trong hệ thống này, conformal phù hợp với confidence/abstention monitoring ở giai đoạn sau, không phải selector v1. [ICML 2024 robust conformal sets](https://proceedings.mlr.press/v235/h-zargarbashi24a.html), [ICML 2025 relational conformal prediction](https://proceedings.mlr.press/v267/cini25a.html), [JMLR 2025 online selective conformal prediction](https://www.jmlr.org/papers/v26/24-0452.html).

**Time-series foundation models** có thể cung cấp zero-shot forecast và dành thêm dữ liệu cho calibration trong một số miền ít dữ liệu, nhưng chưa có cơ sở để ưu tiên cho XSMB. Draw được thiết kế ngẫu nhiên, effective sample theo ngày nhỏ và model phức tạp làm tăng search space/overfit. Foundation model chỉ nên được thử như challenger sau khi selector, baseline và ledger đã đúng. [Foundation models for time-series conformal prediction](https://arxiv.org/abs/2507.08858).

### Digital Transformation

Hướng chuyển đổi quan trọng là từ “daily number generator” sang **auditable forecasting pipeline**:

1. Snapshot dữ liệu theo `target_date/as_of`.
2. Chạy model và lưu đủ vector 100 số.
3. Calibrate/fuse bằng artifact đã version.
4. Tối ưu bộ ba theo utility.
5. Freeze prediction trước kỳ quay.
6. Gửi Telegram từ chính record đã freeze.
7. Sau kỳ quay, ghi evaluation vào cùng ledger.

Model registry hiện đại dùng version, alias/tag, source run và creation timestamp để tổ chức champion/challenger, validation status và rollout. Hệ thống hiện đã có `model_registry`, nên có thể áp dụng pattern mà không nhất thiết thêm MLflow ngay: `champion`, `challenger`, `shadow`, `validation_status`, data/code/config hash. [MLflow Model Registry workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow).

GitHub Actions có artifact attestation để gắn artifact với repository, commit SHA, workflow và trigger event. Đây là lựa chọn tăng provenance cho model/config build về sau; prediction ledger trong Supabase vẫn là nguồn audit nghiệp vụ chính. [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations).

### Innovation Patterns

**Objective-first:** mọi tầng phải phục vụ `hit_count ≥ 2`, không dùng Hit@3 marginal làm proxy duy nhất.

**Family-aware ensemble:** model chỉ được coi là bằng chứng độc lập khi OOF residual đủ khác nhau. Frequency, ChiGOF, CDM và LotoStat cùng dùng frequency/gap phải được gắn vào các `source_family`, rồi giảm weight theo error correlation hoặc fuse bên trong family trước. “5/5 model hoạt động” là health metric, không phải evidence strength.

**Report-as-features, not report-as-vote:** nóng, gan, rơi, đầu/đuôi, tổng/chạm và Top 3 thống kê trở thành explainable features. `loto_statistical` đã bọc đúng Top 3 report, nên report không được cộng thêm như model thứ sáu.

**Calibrate-then-combine:** yêu cầu mỗi family phát full 100-vector; score/rank được map thành marginal appearance probability OOF. Candidate vắng trong Top 5 không được mặc định là probability `0`, vì adapter hiện tại đang zero-fill phần không xuất hiện.

**Joint-aware exhaustive search:** không gian chỉ có `C(100,3)=161.700`, đủ nhỏ để chấm toàn bộ. Candidate pool Top 10 hiện chỉ cần như một optimization shortcut, nhưng nó có thể loại sớm một số tạo combo tốt. Full enumeration giúp contract đơn giản và test được.

**Champion/challenger + abstention:** challenger luôn shadow; nếu estimated lift không vượt baseline với uncertainty chấp nhận được thì message ghi “chưa có bằng chứng lift”, thay vì tăng mức tự tin bằng wording.

### Future Outlook

Trong 6–12 tháng, lợi thế kỹ thuật không đến từ việc tăng 5 lên 7 hoặc 10 model, mà từ:

- OOF score warehouse theo `draw_date × pair × model`;
- calibration và source-family covariance;
- joint probability có điều kiện theo tín hiệu hiện tại;
- immutable prediction/evaluation ledger;
- automated promotion/rollback.

Sau khi có đủ ledger, có thể thử **dynamic ensemble weights** theo rolling performance, nhưng weight phải shrink về equal/family prior khi sample nhỏ. Conformal risk control có thể dùng để monitor mức tin cậy của estimated lift hoặc tạo “evidence grade”; không nên dùng để biến bộ ba thành lời hứa coverage.

Deep learning, LSTM/GRU, Bayesian network, FFT và foundation models chỉ được thêm khi challenger OOF vượt các baseline đơn giản trên cùng cutoff. Negative result cũng là output hợp lệ; trong một cơ chế ngẫu nhiên, hệ thống tốt có thể kết luận “không tìm thấy predictive edge ổn định”.

### Implementation Opportunities

#### Proposed Hybrid Combo Selector v6

**Layer 1 — Snapshot contract**

- Input gồm lịch sử `draw_date < target_date`, official-source status, target date và immutable `as_of`.
- Lưu `data_hash`, `code_commit`, `config_hash`, model artifact versions.
- Crawler → feature build → predict/evaluate vẫn tách bước và idempotent.

**Layer 2 — Full evidence plane**

- Mỗi sub-model trả đủ `score[00..99]`, status, confidence metadata và `source_family`.
- Report analyzer trả component features cho 100 số; `Top 3 thống kê` chỉ là view của cùng feature plane.
- Legacy `top_pairs` tiếp tục được giữ để không ảnh hưởng function cũ; full vector là field bổ sung.

**Layer 3 — Walk-forward calibration**

- Với mỗi target day lịch sử, chạy đúng pipeline chỉ dùng dữ liệu trước ngày đó.
- Fit sigmoid calibration trước vì sample effective theo ngày nhỏ; chỉ thử isotonic khi OOF evidence cho thấy ổn định.
- Split và bootstrap theo `draw_date`, không coi 100 pair-row trong cùng ngày là 100 quan sát độc lập. `TimeSeriesSplit` được thiết kế để train trên các fold quá khứ và test fold tương lai, tránh train tương lai/test quá khứ. [Scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

**Layer 4 — Conditional joint estimator**

- `p_i(t) = P(i xuất hiện | evidence trước kỳ t)` từ calibrated meta-score.
- Pair history tạo shrinkage lift quanh independence, thay vì tự quyết định objective vô điều kiện.
- Một formulation challenger có thể dùng:
  - `raw_q_ij = p_i p_j × shrunk_lift_ij`;
  - `raw_q_abc = p_a p_b p_c × joint_lift_abc`;
  - calibrate lại `q_ij`, `q_abc` bằng OOF;
  - project về coherent bounds: `0 ≤ q_abc ≤ min(q_ab,q_ac,q_bc)`.
- Đây là hypothesis phải backtest; không mặc định tốt hơn current estimator.

**Layer 5 — Set utility optimizer**

Chấm toàn bộ 161.700 bộ:

`P(≥2 | {a,b,c}) = q_ab + q_ac + q_bc - 2q_abc`.

Tie-break theo thứ tự:

1. lower confidence bound của estimated lift;
2. calibrated objective;
3. tổng marginal probability;
4. lexical order để deterministic.

Không ép khác đuôi, không hard-filter nóng/lạnh/7 ngày trừ khi ablation OOF chứng minh tăng KPI. Sau khi chọn set, sắp xếp 🥇🥈🥉 theo marginal probability giảm dần để score và medal không mâu thuẫn.

**Layer 6 — Evidence gate**

- So sánh với random exact conditional theo số lượng tail unique mỗi ngày, random permutation, hot-only, Top 3 report, production v5.1 và combo shadow hiện tại.
- Primary metric: `P(hit_count ≥ 2)`.
- Secondary: distribution `0/3–3/3`, expected winning circles, Brier/log loss cho marginal/joint calibration.
- Confidence interval/bootstrap phải resample theo ngày; promotion phải dùng frozen holdout và điều chỉnh multiple testing.

**Layer 7 — Ledger and messaging**

- Lưu canonical sorted triple, display order, calibrated/uncalibrated score type, component evidence, source families và expected baseline.
- Telegram đọc đúng record đã freeze:
  - `📊 Thống kê mô tả`;
  - `🧪 Bộ 3 thử nghiệm`;
  - `Điểm mô hình — không phải xác suất` hoặc `Xác suất đã calibration OOF` khi contract đạt;
  - `Điều kiện: ≥2/3`;
  - `cutoff/version/evidence grade`.

### Challenges and Risks

- **Fundamental randomness:** pipeline tốt không bảo đảm có edge; random baseline có thể thắng trong cửa sổ ngắn.
- **Small effective sample:** 100 pair-row/ngày phụ thuộc nhau; effective `n` là số kỳ quay, không phải số dòng.
- **Rare joint labels:** triple positive thưa, khiến calibrator và lift dễ overfit.
- **Feature/model overlap:** consensus bonus làm confidence giả nếu không đo OOF error correlation.
- **Selection bias:** thử nhiều lookback, weight, filter và objective làm backtest tốt giả.
- **Calibration drift:** nguồn dữ liệu, schema hoặc model output thay đổi có thể phá mapping score→probability.
- **Operational leakage:** backfill/update kết quả sai cutoff làm cả backtest và prediction không còn hợp lệ.
- **Communication risk:** metric kỹ thuật chính xác vẫn có thể bị Telegram diễn giải sai như lời hứa.

## Recommendations

### Technology Adoption Strategy

| Năng lực | Ưu tiên | Quyết định |
|---|---:|---|
| Prediction ledger + cutoff/hash | P0 | Làm trước mọi thay đổi model |
| Full 100-vector và source-family metadata | P0 | Bổ sung song song, giữ API cũ |
| Walk-forward OOF + exact random baseline | P0 | Điều kiện bắt buộc để đánh giá |
| Full triple enumeration theo `≥2/3` | P0 | Thay artificial digit diversity trong challenger |
| Sigmoid calibration marginal/joint | P1 | Chỉ fit từ OOF |
| Conditional EB joint estimator | P1 | Shadow challenger, ablation bắt buộc |
| Dynamic weights | P2 | Chỉ sau khi có ledger đủ dài |
| Conformal/evidence grade | P2 | Dùng uncertainty, không claim guarantee |
| LSTM/foundation model mới | P3 | Không ưu tiên nếu chưa vượt baseline |

### Innovation Roadmap

**Phase 0 — Correctness:** sửa display ranking contract, lỗi Markov candidate sorting và ChiGOF sum-cardinality; không thay output production ngoài các bug có test.

**Phase 1 — Observe:** mở XSMB combo selector ở `shadow`, truyền đúng production credibility weights, lưu prediction/evaluation đầy đủ và chạy backfill walk-forward.

**Phase 2 — Calibrate:** thêm full vectors/source families, tạo OOF score warehouse, fit marginal/joint calibrators và chạy ablation:

- production v5.1;
- v5.1 bỏ digit diversity;
- unconditional combo shadow;
- conditional hybrid combo v6;
- report-only và random/permutation.

**Phase 3 — Challenge:** freeze một holdout chưa dùng để tune; chạy champion/challenger song song. Chỉ promotion khi lift không đến từ một cửa sổ hoặc một config riêng lẻ.

**Phase 4 — Promote/rollback:** đổi alias/config để v6 thành champion, giữ v5.1 làm fallback; rollback tự động khi data integrity, calibration hoặc KPI guard vi phạm.

### Risk Mitigation

- Pre-register primary KPI, lookback candidates và promotion threshold trước khi đọc holdout.
- Tính confidence interval theo block/day và báo toàn bộ denominator.
- Dùng non-negative, regularized family weights; cap mức thay đổi weight giữa hai lần tune.
- Fail closed khi thiếu official data, artifact hoặc cutoff metadata.
- Prediction record là append-only; correction tạo revision record, không overwrite lịch sử.
- Không nâng nhãn `probability` nếu calibration/reliability report chưa được lưu.
- Telegram formatter có contract test: score order, `≥2/3`, score type, cutoff và disclaimer.

**Technical conclusion:** kiến trúc tốt nhất không phải cộng báo cáo vào ensemble lần nữa. Báo cáo phải trở thành explainable feature plane; các model được calibration và fuse theo source family; selector chấm joint utility cho toàn bộ bộ ba; mọi lift phải được chứng minh bằng walk-forward ledger trước khi thay production.

## Research Synthesis and Strategic Decision

### 1. Research Significance and Methodology

XSMB là một trường hợp điển hình nơi một hệ thống có thể trở nên phức tạp hơn nhưng không chính xác hơn. Thêm model, feature và consensus bonus dễ làm score trông thuyết phục, trong khi cơ chế quay vẫn được tổ chức như một sự kiện ngẫu nhiên, công khai và có giám sát. Vì điều kiện người dùng đặt ra là trúng ít nhất hai trong ba số, hệ thống phải được đánh giá như một **set decision problem**, không phải ba bài toán ranking độc lập.

Nghiên cứu sử dụng bốn lớp bằng chứng:

- văn bản pháp luật và nguồn kết quả chính thức để xác định cơ chế, integrity và ranh giới nội dung;
- nghiên cứu/official documentation về calibration, time-series validation, set prediction và ensemble;
- khảo sát sản phẩm thống kê/dự đoán bên ngoài để nhận diện positioning và khoảng trống auditability;
- audit code, schema và workflow nội bộ để phát hiện objective mismatch, duplication và correctness defects.

Nguồn pháp lý xác nhận kinh doanh xổ số dựa trên sự kiện có kết quả ngẫu nhiên, thông tin phải chính xác và quảng cáo không được mô tả trúng thưởng như kết quả đương nhiên hoặc cách cải thiện tài chính. [Nghị định 30/2007/NĐ-CP](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=14346). Validation chuỗi thời gian phải train quá khứ/test tương lai; calibration phải dùng output độc lập ngoài mẫu. [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html), [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html).

### 2. Industry and Market Synthesis

Không có market-size chính thức đủ tin cậy để tách riêng XSMB analytics; nghiên cứu không suy diễn CAGR hay valuation. Giá trị chain có thể xác định gồm: đơn vị phát hành/giám sát, đại lý, nguồn công bố kết quả, data providers và lớp analytics. Quyền kiểm soát draw và ground truth thuộc lớp chính thức; sản phẩm trong repository chỉ kiểm soát việc biến đổi dữ liệu công khai thành thống kê và output thử nghiệm.

Lợi thế cạnh tranh khả thi không phải “AI biết số trước”, mà là:

- dữ liệu đúng cutoff;
- prediction freeze trước draw;
- reproducible version;
- denominator đầy đủ;
- baseline và uncertainty;
- copy chính xác, có trách nhiệm.

Trong mẫu đối thủ đã xem xét, không tìm thấy bằng chứng công khai về XSMB performance được kiểm toán độc lập với prediction freeze, baseline, confidence interval và denominator đầy đủ. Đây là khoảng trống sản phẩm rõ nhất.

### 3. Technology and Innovation Synthesis

Các công nghệ được đánh giá theo mức phù hợp:

| Hướng | Giá trị cho XSMB | Kết luận |
|---|---|---|
| Set/joint utility optimization | Trực tiếp khớp KPI `≥2/3` | Áp dụng ngay cho challenger |
| Walk-forward OOF | Ngăn future leakage | Bắt buộc |
| Probability calibration | Phân biệt score với probability | Bắt buộc trước probability wording |
| Empirical Bayes joint shrinkage | Giảm overfit pair/triple hiếm | Áp dụng có kiểm chứng |
| Source-family ensemble | Giảm consensus giả do overlap | Áp dụng |
| Conformal risk/uncertainty | Hỗ trợ evidence grade/abstention | Thử sau khi có ledger |
| Dynamic rolling weights | Thích ứng performance | Chỉ dùng khi sample đủ |
| LSTM/TS foundation models | Tăng complexity/search space | Không ưu tiên |

Conformal prediction là xu hướng đáng theo dõi nhưng không được diễn giải sai. Standard conformal thường nhằm coverage của prediction set dưới assumption cụ thể và có thể trả set size biến đổi; nó không bảo đảm fixed Top 3 đạt `≥2/3`. [Robust conformal sets, ICML 2024](https://proceedings.mlr.press/v235/h-zargarbashi24a.html), [Online selective conformal prediction, JMLR 2025](https://www.jmlr.org/papers/v26/24-0452.html).

### 4. Regulatory and Responsible-Messaging Synthesis

Hệ thống được giữ ở ranh giới analytics: không bán vé, nhận tiền cược, trả thưởng hoặc tổ chức chương trình dự thưởng dựa trên kết quả. Nếu business model thay đổi, cần legal review riêng. Nghị định 30/2007/NĐ-CP còn là khung áp dụng nhưng đã có sửa đổi/biến động hiệu lực ở một số phần; trước thương mại hóa phải đối chiếu văn bản hợp nhất và tư vấn pháp lý.

Từ 01/01/2026, Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 và Nghị định 356/2025/NĐ-CP là khung hiện hành. `Telegram chat_id`, thông tin tài khoản số, trigger history và notification logs cần data minimization, purpose/retention policy, access control và incident handling. [Luật 91/2025/QH15](https://vbpl.vn/bocongan/Pages/vbpq-thuoctinh.aspx?ItemID=179252&Keyword=), [Nghị định 356/2025/NĐ-CP](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187276).

WLA Responsible Gaming Principles yêu cầu thông tin chính xác, cân bằng, informed choice, monitoring/testing và reporting. Đây là benchmark, không phải chứng nhận của hệ thống. [WLA Responsible Gaming Principles](https://world-lotteries.org/services/industry-standards/responsible-gaming/principles).

### 5. Competitive and Internal-System Synthesis

Ba hệ thống nội bộ đang cạnh tranh:

| Selector | Objective thực tế | Điểm mạnh | Giới hạn |
|---|---|---|---|
| Report Top 3 | Rule-based marginal score | Explainable | Heuristic, không joint |
| Production v5.1 | Weighted marginal fusion + modifiers + diversity | Đang vận hành, fault tolerant | Không tối ưu `≥2/3`, overlap consensus |
| Combo shadow | Empirical joint `P(≥2)` trong candidate pool | Đúng objective hơn | Unconditional joint, default off, thiếu weights |

Quyết định quan trọng nhất: **không tạo model “report” mới**. `loto_statistical` đã gọi `suggest_top_3_dan_so()`, vì vậy report phải được phân rã thành features và attribution, không thêm vote.

### 6. Final Architecture Decision

**Chọn Hybrid Combo Selector v6 làm challenger, không thay production ngay.**

```text
Official history snapshot
        │
        ├── Report feature plane ── hot/gap/fall/head/tail/touch
        │
        └── Model evidence plane ── full 100-vector + source_family
                              │
                      Walk-forward calibration
                              │
                 Marginal + conditional joint estimator
                              │
              Exhaustive 161,700-triple utility optimizer
                              │
          Shadow ledger ── Telegram ── post-draw evaluation
                              │
                 Frozen-holdout promotion decision
```

Selector contract:

1. Input chỉ gồm dữ liệu `draw_date < target_date`.
2. Mọi model cung cấp full vector hoặc status degraded rõ ràng.
3. Report Top 3 không là vote độc lập.
4. Chấm `P(≥2)` trên toàn bộ 161.700 tổ hợp.
5. Không ép khác đuôi/hard-filter nếu chưa có OOF evidence.
6. Output set canonical được sort để lưu; display order theo calibrated marginal score.
7. Record được freeze trước Telegram và không overwrite.

### 7. Promotion Decision Matrix

| Điều kiện | Không đạt | Đạt |
|---|---|---|
| Data cutoff/integrity | Fail closed | Tiếp tục |
| Full prediction ledger | Giữ shadow | Tiếp tục |
| Walk-forward reproducibility | Giữ shadow | Tiếp tục |
| Lift so với random/report/v5.1 | Giữ champion hiện tại | Tiếp tục |
| Confidence interval và multiple-testing gate | Không promotion | Tiếp tục |
| Frozen holdout ổn định | Không promotion | Eligible |
| Message/legal/privacy review | Không deploy | Có thể promote |
| Rollback path đã test | Không deploy | Có thể promote |

“Có thể promote” không đồng nghĩa model dự đoán chắc chắn; chỉ có nghĩa challenger vượt evidence threshold đã đăng ký trước trên dữ liệu ngoài mẫu.

## Implementation and Risk Framework

### Immediate Correctness Work

Trước khi xây v6:

1. Sửa formatter/selector để medal và score cùng thứ tự.
2. Sửa Markov candidate selection đang sort theo giá trị số thay vì frequency.
3. Chuẩn hóa ChiGOF theo cardinality của nhóm tổng.
4. Truyền production credibility weights vào combo shadow.
5. Lưu combo shadow prediction/evaluation vào database.
6. Thêm contract tests cho cutoff, deterministic selection và `≥2/3`.

Các sửa đổi phải additive, giữ function cũ và workflow `Crawl → Feature Build → Predict/Evaluate`. Nếu thay schema, tạo migration mới và cập nhật `database/schema_final.sql`.

### Phased Delivery Plan

**Phase 0 — Correctness and ledger**

- sửa ba defect;
- prediction run metadata;
- immutable combo shadow records;
- Telegram wording guard.

**Phase 1 — Full evidence**

- full score vectors cho 100 số;
- `source_family`;
- report component attribution;
- compatibility adapter cho legacy Top 5.

**Phase 2 — Walk-forward laboratory**

- backfill OOF;
- random/permutation/report/v5.1/current-combo baselines;
- family residual-correlation analysis;
- sigmoid calibration và reliability report.

**Phase 3 — Hybrid v6 challenger**

- conditional joint estimator;
- full enumeration;
- ablation từng component;
- daily shadow prediction và verification.

**Phase 4 — Frozen decision**

- khóa config;
- evaluate holdout chưa tune;
- legal/privacy/message review;
- promote bằng alias/config nếu pass; rollback nếu guard fail.

### Success Metrics

**Primary**

- `combo_hit_rate = mean(hit_count ≥ 2)`.

**Secondary**

- distribution `0/3`, `1/3`, `2/3`, `3/3`;
- expected winning circles;
- lift so với exact/empirical random baseline;
- calibration curve, Brier/log loss cho marginal và joint;
- missing/degraded model rate;
- prediction freeze success và verification completeness.

Mọi metric phải kèm số kỳ đánh giá và uncertainty; 100 pair-row trong một ngày không được dùng để thổi phồng sample size.

### Risk Register

| Risk | Impact | Control |
|---|---:|---|
| Không có predictive edge thực | Cao | Baseline, frozen holdout, chấp nhận negative result |
| Leakage/backfill sau cutoff | Cao | Snapshot validation, as-of/hash, fail closed |
| Overlap tạo consensus giả | Cao | Source-family fusion, OOF residual correlation |
| Pair/triple sparsity | Cao | Shrinkage, simple calibrator, date-level bootstrap |
| Data snooping | Cao | Pre-register grid/KPI, multiple-testing adjustment |
| Message gây hiểu lầm | Cao | Score-type contract, responsible copy, disclaimer |
| Privacy/secret exposure | Cao | Least privilege, env/secrets, retention, redacted logs |
| Production regression | Cao | Shadow/champion-challenger, rollback |

## Future Outlook and Strategic Opportunities

### Near Term: 0–6 Months

Ưu tiên evidence infrastructure và correctness. Giá trị lớn nhất là biết chính xác selector nào thắng/thua trên cùng cutoff, không phải tạo thêm model. Nếu v6 chưa chứng minh lift, sản phẩm vẫn có giá trị nhờ thống kê minh bạch và ledger.

### Medium Term: 6–24 Months

Khi ledger đủ dài, có thể:

- tune family weights có regularization;
- monitor calibration/drift theo rolling window;
- phát hành evidence-grade thay cho confidence marketing;
- public performance dashboard với complete denominator;
- dùng champion/challenger aliases và automated rollback.

### Long Term

Định vị bền vững là **auditable lottery analytics**, không phải “AI đoán chắc”. Nếu nghiên cứu liên tục không bác bỏ random baseline, hệ thống nên chuyển trọng tâm sang descriptive analytics, data quality và responsible monitoring thay vì tăng model complexity.

## Methodology, Sources and Limitations

### Source Documentation

**Primary/authoritative sources**

- [CSDL quốc gia về VBPL — Nghị định 30/2007/NĐ-CP](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=14346)
- [Thông tư 22/2021/TT-BTC](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=150990)
- [Thông tư 38/2025/TT-BTC](https://vbpl.vn/botaichinh/Pages/vbpq-thuoctinh.aspx?ItemID=178473)
- [Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15](https://vbpl.vn/bocongan/Pages/vbpq-thuoctinh.aspx?ItemID=179252&Keyword=)
- [Nghị định 356/2025/NĐ-CP](https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187276)
- [Xổ số Thủ đô](https://xosothudo.com.vn/)
- [WLA Responsible Gaming Principles](https://world-lotteries.org/services/industry-standards/responsible-gaming/principles)
- [WLA-SCS 2024](https://publications.world-lotteries.org/security-and-risk-management/wla-scs-2024-code-of-practice)

**Technical sources**

- [Scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
- [Scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- [Efficient Set-Valued Prediction](https://arxiv.org/abs/1906.08129)
- [Cardinality-Aware Set Prediction](https://arxiv.org/abs/2407.07140)
- [Statistical auditing of lottery draws](https://arxiv.org/abs/0806.4595)
- [White's Reality Check](https://doi.org/10.1111/1468-0262.00152)
- [Nested cross-validation](https://linus.nci.nih.gov/techreport/Varma-Simon-CrossValid.pdf)
- [Benjamini–Hochberg FDR](https://www.dcscience.net/Benjamini-Hochberg-1995-FDR.pdf)
- [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
- [MLflow Model Registry workflow](https://mlflow.org/docs/latest/ml/model-registry/workflow)

**Internal evidence**

- [Loto analyzer](../../../src/xsmb_ensemble/xsmb_loto_analyzer.py#L322)
- [LotoStat wrapper](../../../src/xsmb_ensemble/model_loto_statistical.py#L46)
- [Production v5.1 aggregation](../../../src/xsmb_ensemble/ensemble_engine.py#L292)
- [Combo selector](../../../src/xsmb_combo/selector.py)
- [Combo shadow integration](../../../src/xsmb_combo/shadow.py)
- [Canonical combo metrics](../../../src/xsmb_combo/metrics.py)

### Search Themes

- XSMB official draw structure, linked issuance and supervision;
- Vietnamese lottery advertising/licensing and personal-data law;
- lottery randomness/statistical auditing;
- set-valued prediction and top-k/cardinality-aware learning;
- calibration, time-series validation and conformal uncertainty;
- ensemble diversity, forecast combination and data-snooping control;
- competitor hot/cold/gap/VIP/backtest positioning;
- MLOps registry, provenance and immutable audit patterns.

### Quality Assurance

- Ưu tiên nguồn pháp lý/chính thức và primary research.
- Claim động được web-verify tại thời điểm nghiên cứu.
- Claim code được đối chiếu trực tiếp với repository.
- Suy luận kiến trúc được gắn là proposal/hypothesis và yêu cầu backtest.
- Không sử dụng market-size/CAGR không có nguồn đáng tin cậy.
- Không coi benchmark WLA là chứng nhận.

### Limitations

- Không có independent audited XSMB prediction dataset bên ngoài.
- Chưa chạy backtest v6 vì đây là nghiên cứu/architecture decision, không phải implementation.
- Baseline iid `14,005%` chỉ là sanity check; production baseline phải dùng cấu trúc draw thực.
- Tình trạng văn bản pháp luật có thể tiếp tục thay đổi; cần kiểm tra văn bản hợp nhất trước thương mại hóa.
- Hiệu quả model chỉ có thể kết luận sau walk-forward và frozen holdout.

## Research Conclusion

### Summary of Key Findings

Không có bằng chứng cho thấy thêm report vote hoặc model mới sẽ giải quyết tỷ lệ trúng thấp. Hệ thống hiện có signal duplication, objective mismatch và một số correctness defect. Combo selector là hướng đúng vì chấm utility `≥2/3`, nhưng phải được nâng từ empirical unconditional shadow thành calibrated conditional challenger.

### Strategic Impact Assessment

Nếu triển khai roadmap này, hệ thống sẽ chuyển từ một generator khó kiểm chứng thành một forecasting experiment có thể audit. Kể cả khi không tìm thấy edge ổn định, kết quả vẫn có giá trị: loại bỏ false confidence, ngăn data leakage, bảo vệ người dùng và giảm chi phí duy trì model không hiệu quả.

### Next Steps

1. Tạo implementation spec cho Phase 0–1.
2. Refactor additive, giữ toàn bộ function/API cũ.
3. Viết migration cho prediction ledger nếu schema hiện tại thiếu metadata.
4. Chạy combo v6 ở shadow và backfill walk-forward.
5. Trình checkpoint kết quả trước mọi quyết định promotion.

---

**Research Completion Date:** 2026-07-28
**Research Period:** Comprehensive current-state and historical analysis
**Source Verification:** Official sources, primary research and repository audit
**Confidence Level:** High đối với audit/code/regulatory facts; medium đối với predicted benefit của v6 cho đến khi backtest

_Tài liệu này là cơ sở nghiên cứu và quyết định kỹ thuật; không phải bảo đảm kết quả xổ số hoặc tư vấn pháp lý._
