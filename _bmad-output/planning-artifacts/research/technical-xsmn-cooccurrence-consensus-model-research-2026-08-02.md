---
stepsCompleted: [1, 2, 3, 4, 5, 6]
inputDocuments: []
workflowType: 'research'
lastStep: 6
research_type: 'technical'
research_topic: 'relationship — mô hình XSMN chọn Top-3 bằng đồng thuận Top-5/model và đồng xuất hiện giữa các tỉnh'
research_goals: 'Xác định logic nghiệp vụ, grain dữ liệu, anchor recency guard hai kỳ merged, công thức co-occurrence có shrinkage, cách kết hợp model consensus và lịch sử hai tỉnh, kiến trúc function shadow relationship, cùng phương pháp backtest theo điều kiện trúng ít nhất 2/3.'
user_name: 'tannguyen'
date: '2026-08-02'
web_research_enabled: true
source_verification: true
---

# `relationship`: Nghiên cứu mô hình XSMN đồng thuận và đồng xuất hiện

**Date:** 2026-08-02
**Author:** tannguyen
**Research Type:** technical

---

## Research Overview

Nghiên cứu này chuyển ý tưởng chọn Top-3 XSMN từ Top-5 của nhiều model thành contract kỹ thuật cho phương pháp `relationship`. Phạm vi bao gồm grain `model@province`, model-family voting, lịch sử đồng xuất hiện của đúng hai tỉnh, anchor recency guard hai kỳ merged, shrinkage cho rare events, combo scoring bám KPI `hit_count >= 2`, persistence shadow và walk-forward evaluation.

Kết luận trọng tâm là `relationship` khả thi trên stack hiện tại mà không cần dependency hay hạ tầng mới, nhưng raw co-occurrence rate không đủ làm bằng chứng. Function phải lưu support, marginal rate, shrinkage và direct historical ≥2/3 evidence; mọi tuning phải thực hiện với cutoff thời gian nghiêm ngặt. Anchor vừa xuất hiện trong cả hai matched merged occasions gần nhất bị loại theo business rule, nhưng tác động của rule phải được kiểm chứng bằng ablation `guard_on/off`.

Khuyến nghị cuối là triển khai dưới dạng shadow challenger, so sánh ngoài mẫu với consensus-only và ensemble production, bổ sung weekly `Relationship: x/7`, rồi mới lập đề xuất promotion. Phần **Research Synthesis** cuối tài liệu tập hợp quyết định BA, acceptance criteria, roadmap, hạn chế và nguồn kiểm chứng.

## Table of Contents

1. Technical Research Scope Confirmation
2. Technology Stack Analysis
3. Integration Patterns Analysis
4. Architectural Patterns and Design
5. Implementation Approaches and Technology Adoption
6. Technical Research Recommendations
7. Research Synthesis

---

<!-- Content will be appended sequentially through research workflow steps -->

## Technical Research Scope Confirmation

**Research Topic:** `relationship` — mô hình XSMN chọn Top-3 bằng đồng thuận Top-5/model và đồng xuất hiện giữa các tỉnh
**Research Goals:** Xác định logic nghiệp vụ, grain dữ liệu, anchor recency guard hai kỳ merged, công thức co-occurrence có shrinkage, cách kết hợp model consensus và lịch sử hai tỉnh, kiến trúc function shadow `relationship`, cùng phương pháp backtest theo điều kiện trúng ít nhất 2/3.

**Technical Research Scope:**

- Architecture Analysis - design patterns, frameworks, system architecture
- Implementation Approaches - development methodologies, coding patterns
- Technology Stack - languages, frameworks, tools, platforms
- Integration Patterns - APIs, protocols, interoperability
- Performance Considerations - scalability, optimization, patterns

**Research Methodology:**

- Current web data with rigorous source verification
- Multi-source validation for critical technical claims
- Confidence level framework for uncertain information
- Comprehensive technical coverage with architecture-specific insights

**Scope Confirmed:** 2026-08-02

### Scope Amendment — `relationship` anchor recency guard

Tên nghiệp vụ và identifier của cách dự đoán là `relationship`.

Số đầu tiên (anchor) là quyết định khóa của phương pháp. Trước khi chấp nhận anchor có đồng thuận cao nhất, function phải lấy đúng hai occasion merged gần nhất của cùng target-province set, với `draw_date < target_date`. Nếu anchor xuất hiện trong `merged_tails` của cả hai occasion liên tiếp, anchor bị loại và function kiểm tra ứng viên đồng thuận kế tiếp. Xuất hiện ở bất kỳ tỉnh nào trong scope merged đều tính là hit của occasion; xuất hiện ở cả hai tỉnh trong cùng occasion vẫn chỉ tính một hit. Rule chỉ áp dụng cho anchor, không tự động loại hai companion.

## Technology Stack Analysis

### Programming Languages

Python hiện tại là lựa chọn phù hợp và không cần thêm ngôn ngữ. Bài toán chỉ có 100 cặp `00–99`; toàn bộ ma trận đồng xuất hiện là `100 × 100`, còn số combo ba ứng viên tối đa là `C(50,3)=19.600`, đủ nhỏ để xử lý deterministic trong một job CPU. `itertools.combinations` thuộc standard library và được thiết kế cho vòng lặp hiệu quả, nên không cần graph engine hoặc distributed compute. [Python Standard Library](https://docs.python.org/3/library/index.html)

_Ngôn ngữ production:_ Python `>=3.9`, đồng nhất với crawler, ensemble và verifier hiện tại.
_Đặc tính hiệu năng:_ complexity nhỏ, có thể tính lại theo từng ngày; ưu tiên code thuần Python cho combo và NumPy cho ma trận.
_Độ tin cậy:_ Cao — dựa trên kích thước không gian bài toán và stack đang chạy.

### Development Frameworks and Libraries

- **Pandas** dùng để căn chỉnh các kỳ quay theo `(draw_date, province)`, tạo tập tails mỗi tỉnh và group theo cặp tỉnh/lịch xổ. Tài liệu Pandas khuyến nghị dùng các phép `GroupBy` tích hợp thay vì `apply` Python nhiều tầng khi có thể. [Pandas GroupBy](https://pandas.pydata.org/pandas-docs/stable/user_guide/groupby.html)
- **NumPy** dùng cho ma trận `cohit_count`, `expected_count`, `lift`, conditional rate và shrinkage vectorized. Broadcasting/vectorization đẩy vòng lặp số học xuống implementation tối ưu thay vì Python loop. [NumPy User Guide](https://numpy.org/doc/stable/numpy-user.pdf)
- **scikit-learn** chỉ nên dùng ở pha đánh giá/calibration, không cần tạo classifier mới trong MVP. `TimeSeriesSplit` giữ thứ tự thời gian, tránh train bằng tương lai rồi đánh giá quá khứ; tuy nhiên XSMN không cách đều theo ngày, nên production backtest nên triển khai expanding-window theo **số kỳ quay** thay vì dùng splitter mặc định một cách máy móc. [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
- Score tổng hợp ban đầu phải được gọi là `ranking_score`. Chỉ đổi tên thành probability sau khi calibration out-of-fold chứng minh độ tin cậy; scikit-learn cũng yêu cầu calibrator học trên dữ liệu độc lập với tập fit model. [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html)

_Khuyến nghị dependency:_ Không bổ sung thư viện Apriori, graph database hoặc deep-learning package mới cho MVP.
_Độ tin cậy:_ Cao.

### Database and Storage Technologies

Supabase/PostgreSQL tiếp tục là source of truth. Dữ liệu cần thiết đã nằm ở `model_predictions`, `lottery_draws`/`tails_2d` và metadata ngày/tỉnh; MVP có thể query rồi tính in-memory để tránh migration sớm. Nếu shadow backtest chứng minh có giá trị, có thể thêm bảng snapshot co-occurrence và prediction audit bằng migration riêng.

PostgreSQL hỗ trợ aggregate, ordered aggregate và grouping đủ để chuẩn bị tập kỳ quay; cần lưu ý aggregate như `array_agg` phụ thuộc thứ tự nếu không chỉ định `ORDER BY`. [PostgreSQL aggregate functions](https://www.postgresql.org/docs/current/functions-aggregate.html)

Nếu materialize thống kê, index nên khớp grain truy vấn, dự kiến `(province_a, province_b, draw_date)` và `(prediction_date, model_name, province)`. Supabase khuyến nghị kiểm tra `EXPLAIN` và chỉ thêm index theo query thực tế vì index làm tăng chi phí ghi. [Supabase query optimization](https://supabase.com/docs/guides/database/query-optimization)

_MVP storage:_ Không schema change; output shadow có thể dùng contract audit hiện hữu hoặc file backtest.
_Phase production:_ Migration mới nếu cần bảng `xsmn_pair_cooccurrence_stats` hoặc model prediction riêng.
_Độ tin cậy:_ Cao.

### Development Tools and Platforms

- **pytest** phù hợp cho các case: trùng số giữa model, thiếu model, hai tỉnh không có cùng ngày, denominator bằng 0, tie-break và chống leakage. Parametrization hỗ trợ chạy cùng invariant trên nhiều tổ hợp input. [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)
- **Git + GitHub Actions** giữ pipeline hiện hành. Function mới nên chạy shadow trong `predict_ensemble.py` hoặc script độc lập, có feature flag và không thay output production cho tới khi đạt acceptance gate.
- Backtest phải reproducible với `data_cutoff < prediction_date`, cùng version code/config và cùng danh sách model source.

_Độ tin cậy:_ Cao.

### Cloud Infrastructure and Deployment

Không cần thêm cloud service. GitHub Actions hỗ trợ cả `schedule` và `workflow_dispatch`; shadow job có thể chạy theo lịch production hoặc được trigger thủ công khi backfill. GitHub lưu ý scheduled workflow có thể bị trễ khi tải cao, vì vậy `prediction_date` phải được truyền/resolve theo timezone nghiệp vụ thay vì suy luận từ giờ chạy thực tế. [GitHub Actions events](https://docs.github.com/en/enterprise-cloud%40latest/actions/reference/workflows-and-actions/events-that-trigger-workflows)

_Khuyến nghị:_ Giai đoạn 1 chạy chung runner hiện tại; chỉ tách job nếu backtest lịch sử làm tăng runtime đáng kể.
_Độ tin cậy:_ Cao.

### Technology Adoption Trends

Xu hướng phù hợp với dự án không phải thay stack mà là **giảm độ phức tạp mô hình và tăng auditability**: dùng thống kê co-occurrence có support/shrinkage, ranking deterministic và walk-forward evaluation. Với chỉ 100 số và khoảng vài trăm kỳ mỗi tỉnh, graph database, Spark hay neural network tạo thêm vận hành nhưng không giải quyết thiếu mẫu.

**Kết luận stack:** Python + Pandas + NumPy + PostgreSQL/Supabase + pytest + GitHub Actions đã đủ. MVP không cần dependency mới, không cần dịch vụ mới và có thể tồn tại như một shadow function tách biệt.

## Integration Patterns Analysis

### API Design Patterns

Đây nên là **internal Python API**, không phải REST/microservice mới. Scoring core phải là pure function để backtest và production dùng chung:

```python
def generate_cooccurrence_consensus_shadow(
    model_results: list[dict],
    matched_history: list[dict],
    provinces: tuple[str, ...],
    target_date: date,
    config: CooccurrenceConsensusConfig,
) -> dict:
    ...
```

`model_results` được truyền trực tiếp từ `run_xsmn_models_for_target`; không đọc lại prediction vừa ghi từ database, nhờ đó tránh race condition và giữ fault isolation. Repository layer chịu trách nhiệm query lịch sử; pure function chỉ validate, score và trả evidence.

_REST/GraphQL/gRPC:_ Không cần cho MVP.
_Webhook:_ Không cần; orchestration hiện hữu đã có runtime event.
_Độ tin cậy:_ Cao.

### Communication Protocols

Integration nội bộ dùng Python objects. Chỉ database boundary dùng HTTPS qua Supabase/PostgREST. Supabase Python hỗ trợ `select` kết hợp filter và modifier; cần lọc theo region, province, cutoff và order rõ ràng. [Supabase Python filters](https://supabase.com/docs/reference/python/using-filters)

Supabase REST mặc định giới hạn 1.000 rows, trong khi backtest `6 model × 2 tỉnh × 156 kỳ` có thể vượt ngưỡng; mọi historical reader phải pagination bằng `range()` hoặc query theo cửa sổ. [Supabase Python select](https://supabase.com/docs/reference/python/select)

_WebSocket/message queue:_ Không có nhu cầu real-time; thêm broker sẽ tăng failure surface.
_Độ tin cậy:_ Cao.

### Data Formats and Canonical Contracts

**Input production hiện tại:** hệ thống chạy 6 model families cho mỗi tỉnh: `frequency`, `gap_overdue`, `markov`, `xgboost_core`, `lstm`, `cdm`. Vì vậy yêu cầu phải tổng quát theo `N` model đang active, không hard-code 5.

- Mỗi `model@province` cung cấp tối đa Top-5 sau khi loại duplicate nội bộ.
- Với 6 model và 2 tỉnh: tối đa 60 lượt đề cử; đây là **slots**, không phải 60 số unique.
- Một số được cùng model chọn ở cả hai tỉnh chỉ tạo **một family vote**, nhưng có `province_support=2`. Cách này ngăn cùng thuật toán tự nhân đôi phiếu.
- `vote_ratio = distinct_model_families_voting / active_model_families`; ví dụ cũ `5/5`, còn production đủ model sẽ là `6/6`.
- Rank Top-5 phải được giữ, dự kiến Borda `5,4,3,2,1` hoặc normalized rank score; không coi mọi vị trí ngang nhau.

**Matched history contract:** mỗi item đại diện đúng một occasion mà toàn bộ target provinces có kết quả trước cutoff:

```json
{
  "draw_date": "2026-07-26",
  "tails_by_province": {
    "tien-giang": [/* unique 00-99 */],
    "kien-giang": [/* unique 00-99 */]
  },
  "merged_tails": [/* union */]
}
```

Không ghép kỳ gần nhất của tỉnh A với một ngày khác của tỉnh B. XSMN lookback được đo bằng **số occasion đã match**, không bằng số ngày.

### System Interoperability and Data Flow

Luồng đề xuất:

1. Sáu model tỉnh chạy như hiện tại và giữ nguyên Top-10 nội bộ/Top-5 audit.
2. Orchestrator truyền `all_model_results` vào shadow function; function cắt Top-5, không mutate object gốc.
3. History repository lấy các kỳ `< target_date` có đủ đúng cặp tỉnh.
4. Builder tạo ba lớp thống kê:
   - `merged_joint`: hai số cùng có mặt trong union hai tỉnh;
   - `cross_province_joint`: một số ở tỉnh A và số kia ở tỉnh B, theo cả hai chiều;
   - `combo_2of3_rate`: trong lịch sử, ít nhất hai số của bộ ba cùng hit trên scope merged.
5. Selector kết hợp vote evidence với co-occurrence evidence, enumerate combo ba số và trả Top-3 kèm audit.
6. Persistence ghi một row shadow; production ensemble hiện tại không đọc row này.
7. Verifier đối chiếu đúng `(prediction_date, target_provinces)` và đặt `combo_hit = hit_count >= 2`.

### Persistence and Idempotency

Schema `model_predictions` hiện đã đủ cho MVP:

- `province='all'`
- `model_name='cooccurrence_consensus_shadow'`
- `prediction_mode='shadow'`
- `pair_1..pair_3`, `score_1..score_3`
- `score_semantics='ranking_score_uncalibrated'`
- `run_metadata`: provinces, cutoff, active/skipped families, Top-5 input, vote counts, history sample size, edge evidence, config/version

Unique key `(prediction_date, region, province, model_name)` bảo đảm một logical row/ngày. Writer nên dùng repository save/update hiện hữu hoặc deterministic upsert; PostgreSQL `ON CONFLICT` cung cấp action thay thế khi unique conflict xảy ra. [PostgreSQL INSERT/ON CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html)

MVP chỉ cần bổ sung model name vào whitelist shadow/verification; chưa cần schema migration. Nếu về sau materialize toàn bộ `100 × 100` edge stats, khi đó mới tạo migration riêng.

### Event-Driven Integration and Fault Isolation

Shadow function được gọi **sau khi sub-models hoàn tất** và có thể đặt sau khi production prediction đã save, giống CMR. Mọi exception phải bị bắt tại boundary:

- `success`: đủ model, history và combo hợp lệ;
- `insufficient_active_models`;
- `insufficient_matched_draws`;
- `insufficient_candidate_diversity`;
- `error`: có reason đã redact.

Shadow failure chỉ ghi log/trạng thái, không được làm fail ensemble hoặc chặn Telegram production. Không cần event sourcing, Kafka hay CQRS.

### Integration Security Patterns

Function không nhận credential và không ghi secret vào `run_metadata`. Supabase/Telegram credentials tiếp tục lấy từ environment/GitHub Actions secrets. GitHub mã hóa Actions secrets và tự redaction nhiều loại credential trong logs, nhưng application vẫn phải tránh log structured secret values. [GitHub Actions secrets](https://docs.github.com/en/actions/reference/security/secrets)

### Integration Decision

**Khuyến nghị:** triển khai một shadow producer tách biệt, pure scoring core + repository history adapter + canonical shadow persistence. Không thay đổi production ensemble, public contracts hay workflow trigger trong giai đoạn đầu.

## Architectural Patterns and Design

### System Architecture Pattern

`relationship` nên là một shadow challenger trong modular monolith hiện tại, gồm năm lớp tách biệt:

1. **Input adapter** — nhận runtime `model_results`, chuẩn hóa Top-5 của từng `model@province`.
2. **History adapter** — tải matched occasions của đúng target-province set với cutoff nghiêm ngặt.
3. **Evidence builder** — tạo node vote evidence và ma trận edge co-occurrence.
4. **Combo optimizer** — chọn anchor qua recency guard rồi chấm mọi cặp companion.
5. **Audit/persistence adapter** — lưu canonical shadow row và cung cấp evidence cho Telegram/verifier.

Pure scoring core không biết Supabase, Telegram hay environment variables. Cấu trúc này cho phép cùng một logic chạy production shadow và walk-forward backtest mà không tạo hai cách tính khác nhau.

### Node Evidence and Independent Voting

Với `M` model families hoạt động và số `x`:

```text
family_vote_ratio(x) = distinct_families_selecting_x / M
rank_score(x)        = mean(best_normalized_rank_per_family)
province_coverage(x) = provinces_selecting_x / target_provinces
credibility_vote(x)  = weighted distinct-family vote
```

Cùng model chọn `x` ở hai tỉnh chỉ có một family vote; province coverage giữ lại tín hiệu hai tỉnh. Node score là ranking evidence, không phải probability.

### Anchor Selection and Two-Occasion Recency Guard

Anchor candidates được sort deterministic theo:

1. family vote ratio;
2. credibility-weighted vote;
3. rank score;
4. province coverage;
5. pair number tăng dần để phá hòa.

Function lấy hai matched occasions gần nhất của đúng target-province set, mỗi occasion là union tails của hai tỉnh. Với từng anchor candidate theo thứ tự:

```text
recent_hits(anchor) = count(anchor in merged_tails_t for t in last_2_occasions)

if recent_hits == 2:
    reject anchor with reason = consecutive_merged_hit_2of2
else:
    accept anchor and stop scanning
```

Nếu lịch sử có dưới hai matched occasions, trả `insufficient_recent_history`. Nếu tất cả anchor candidates bị loại, trả `no_eligible_anchor`. Rule chỉ áp dụng cho anchor; companion không bị loại bằng heuristic này.

Đây là business guardrail chống chasing, không phải khẳng định rằng hai lần vừa ra làm xác suất toán học kỳ tiếp theo giảm. Acceptance backtest phải có ablation `guard_on` so với `guard_off`.

### Pairwise Relationship Evidence

Cho `N` matched occasions, `I_t(x)=1` nếu số `x` có trong merged tails ở occasion `t`:

```text
n_x  = Σ I_t(x)
n_xy = Σ I_t(x) I_t(y)
support(x,y)    = n_xy / N
confidence(x→y) = n_xy / n_x
lift(x,y)       = (n_xy × N) / (n_x × n_y)
```

Association-rule support/confidence bắt nguồn từ bài toán tìm quan hệ giữa các itemsets; trong `relationship`, một matched lottery occasion đóng vai trò một transaction. [Agrawal, Imieliński & Swami, SIGMOD 1993](https://doi.org/10.1145/170035.170072)

Raw rate không được dùng đơn độc. Edge posterior được shrink về mức độc lập:

```text
p0_xy             = (n_x / N) × (n_y / N)
joint_shrunk_xy    = (n_xy + prior_strength × p0_xy) / (N + prior_strength)
lift_shrunk_xy     = joint_shrunk_xy / max(p0_xy, epsilon)
```

Audit bắt buộc ghi `n_xy/N`, marginal counts và smoothed value. Với rare event hoặc N nhỏ, normal approximation có thể không chính xác; cần interval phù hợp hoặc ít nhất lower-bound/support guard. [NIST binomial confidence intervals](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm)

### Combo Optimization Aligned to ≥2/3

Sau khi chọn anchor `a`, optimizer enumerate mọi unordered pair `(b,c)` trong candidate pool. Mỗi combo phải kiểm tra cả ba edges `(a,b)`, `(a,c)`, `(b,c)`; không chọn theo chain một chiều.

Direct historical objective:

```text
h_t(a,b,c) = 1 if |{a,b,c} ∩ merged_tails_t| >= 2 else 0
n_2of3     = Σ h_t
rate_2of3  = n_2of3 / N
```

Rate này cũng phải shrink về baseline combo rate. Challenger score ban đầu có dạng:

```text
relationship_score(combo) =
    w_node   × normalized_node_evidence
  + w_edge   × normalized_triangle_edge_evidence
  + w_combo  × normalized_shrunk_2of3_evidence
```

Không khóa trọng số bằng ý kiến chuyên gia. Walk-forward phải so sánh ba nested variants:

- `R-A`: consensus/rank only;
- `R-B`: node + pair relationships;
- `R-C`: node + pair relationships + direct ≥2/3 history.

Final Top-3 kế thừa guardrail ba hàng đơn vị khác nhau. Tie-break: final score, direct support, anchor vote, triangle minimum-edge strength, rồi tuple số tăng dần.

### Selection Bias and Statistical Safety

Trong 100 số có 4.950 unordered edges. Chọn edge cao nhất sau khi quan sát toàn bộ tập dễ tạo discovery giả. Shrinkage, minimum support và walk-forward validation là bắt buộc. Nếu implementation tuyên bố edge “có ý nghĩa thống kê”, phải hiệu chỉnh multiple testing; Benjamini–Hochberg kiểm soát expected false discovery proportion trong họ kiểm định phù hợp. [Benjamini & Hochberg 1995](https://doi.org/10.2307/2346101)

Không gọi score là probability trước calibration out-of-fold. Calibration model phải học trên dữ liệu độc lập với dữ liệu fit score để tránh optimistic bias. [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html)

### Scalability and Performance

Không gian chỉ gồm ma trận `100 × 100`. Candidate pool tối đa 60 slots và không quá 100 số unique; với anchor đã khóa, số cặp companion tối đa `C(99,2)=4.851`. Tính on-demand theo ngày đủ nhẹ. Materialized stats chỉ xem xét sau profiling/backtest.

### Security and Operations

Scoring core không truy cập credential. Orchestrator dùng secrets hiện có; audit metadata không chứa URLs có token hoặc connection strings. Shadow failure bị bắt và không làm fail production ensemble.

### Architecture Decision

Tên nghiệp vụ và stable producer identifier là `relationship`; phiên bản đầu `relationship_v1`, `prediction_mode='shadow'`, `score_semantics='ranking_score_uncalibrated'`. Chỉ promote sau khi vượt acceptance gate ngoài mẫu và không làm giảm reliability của pipeline hiện tại.

## Implementation Approaches and Technology Adoption

### Technology Adoption Strategy

Áp dụng shadow-first, không big-bang. `relationship` dùng cùng runtime inputs với production nhưng không tham gia verdict. Google SRE định nghĩa canary là triển khai một phần có giới hạn thời gian để đánh giá trước khi rollout; shadow challenger còn an toàn hơn vì không thay output người dùng. [Google SRE canarying releases](https://sre.google/workbook/canarying-releases/)

Các phase:

1. Offline replay/backtest.
2. Daily shadow generation và persistence.
3. Weekly performance report.
4. Ablation review và live-shadow observation.
5. Human approval trước mọi promotion.

### Proposed Module Boundaries

```text
src/xsmn_relationship/
├── config.py       # RelationshipConfig
├── domain.py       # typed immutable contracts
├── repository.py   # matched history and historical Top-5 adapters
├── consensus.py    # family votes/rank/province evidence
├── cooccurrence.py # counts, shrinkage and edge evidence
├── selector.py     # anchor guard and combo optimizer
├── service.py      # shadow orchestration entry point
└── backtest.py     # walk-forward evaluation
```

Optional CLI: `src/scripts/predict_xsmn_relationship.py`. Production integration gọi service trực tiếp từ `predict_ensemble.py` sau khi sub-model results đã có.

### Initial Configuration Hypotheses

```yaml
relationship:
  enabled: true
  prediction_mode: shadow
  top_k_per_source: 5
  min_active_model_families: 4
  min_anchor_vote_ratio: 0.50
  recent_anchor_lookback: 2
  reject_anchor_if_hits: 2
  history_lookback_occurrences: 104
  min_history_occurrences: 52
  prior_strength: 20
  min_pair_support_count: 3
  require_distinct_unit_digits: true
  score_semantics: ranking_score_uncalibrated
```

Các giá trị history/support/prior là hypotheses cần walk-forward validation, không phải xác suất đã được chứng minh.

### Development Workflow and Persistence Changes

MVP không cần database migration. Cần cập nhật code adapters để canonical row có:

```text
region          = XSMN
province        = all
model_name      = relationship
model_version   = relationship_v1
prediction_mode = shadow
score_semantics = ranking_score_uncalibrated
```

`run_metadata` lưu target provinces, cutoff, active/skipped families, Top-5 source inputs, anchor audit, two recent merged dates, pair counts/rates, shrinkage config, combo evidence và version. Không lưu secret.

Verifier và weekly report cần nhận diện `relationship` như shadow Top-3 chuẩn, verify đúng merged province scope và dùng `combo_hit = hit_count >= 2`.

### Testing and Quality Assurance

Unit tests tối thiểu:

- Dedupe cùng family ở hai tỉnh nhưng giữ province coverage.
- Denominator model động khi một family lỗi.
- Chỉ lấy Top-5/source, không mutate Top-10 gốc.
- Anchor xuất hiện 2/2 merged occasions bị loại.
- Anchor xuất hiện 0/2 hoặc 1/2 được phép.
- Dưới hai recent occasions thì abstain.
- Cutoff loại mọi row `>= target_date`.
- Pair count, marginal, zero denominator, shrinkage và symmetric edge đúng.
- Combo kiểm tra đủ ba edges và direct ≥2/3.
- Ba unit digits khác nhau.
- Tie-break deterministic.
- Shadow exception không ảnh hưởng production.
- Supabase historical reader pagination vượt 1.000 rows.
- Persistence idempotent và verifier đúng province scope.

pytest parametrization phù hợp để chạy cùng invariant trên nhiều edge cases. [pytest parametrization](https://docs.pytest.org/en/stable/how-to/parametrize.html)

### Leakage-Safe Backtest

Với từng prediction date `t`:

1. Đọc archived model Top-5 của đúng ngày `t`.
2. Chỉ build history/co-occurrence từ draw dates `< t`.
3. Chọn anchor và combo bằng config/version cố định của fold.
4. Verify bằng actual merged tails ngày `t`.
5. Ghi output riêng cho analysis; không overwrite production history.

Feature selection, normalization, shrinkage và threshold phải học chỉ trên training prefix. scikit-learn định nghĩa việc dùng thông tin không có tại prediction time là data leakage và làm performance estimate quá lạc quan. [scikit-learn common pitfalls](https://scikit-learn.org/stable/common_pitfalls.html)

Ablations bắt buộc:

- `R-A`: consensus/rank only;
- `R-B`: node + pair relationship;
- `R-C`: thêm direct historical ≥2/3;
- `R-C guard_off`;
- `R-C guard_on`.

Baseline comparison phải dùng cùng eligible dates và same-day archived production prediction.

### Deployment and Operations

Relationship shadow chạy fault-tolerant sau production save. Status contract:

- `success`;
- `insufficient_active_models`;
- `insufficient_recent_history`;
- `insufficient_matched_draws`;
- `no_eligible_anchor`;
- `insufficient_candidate_diversity`;
- `error`.

Không status nào được raise qua production boundary. GitHub Actions environment/protection rules có thể bổ sung approval khi sau này promotion ảnh hưởng production. [GitHub deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)

### Team, Cost and Resource Requirements

Không cần hạ tầng mới hay dependency mới. Một kỹ sư Python/SQL có thể triển khai; data-science review tập trung vào shrinkage, walk-forward và metric interpretation. Runtime matrix/combo nhỏ; chi phí chính là query lịch sử và backfill, giảm bằng pagination, bounded occurrence lookback và deterministic caching trong mỗi run.

### Risk Assessment and Mitigation

| Risk | Mitigation |
|---|---|
| Rare pair thắng vì 1 hit | support count, shrinkage, interval/audit |
| Same model double-votes across provinces | distinct family vote + separate province coverage |
| Future leakage | strict `< target_date`, replay tests |
| Anchor rule gây gambler's-fallacy harm | guard_on/off ablation |
| Overfit nhiều weights/lookbacks | predeclared small grid + rolling validation |
| Shadow làm fail ensemble | exception boundary and canonical error status |
| Score bị hiểu là probability | explicit `ranking_score_uncalibrated` |
| Query thiếu rows | Supabase pagination and count assertions |

## Technical Research Recommendations

### Implementation Roadmap

1. Implement pure core and unit tests.
2. Build leakage-safe backtest from archived Top-5.
3. Run ablations and select config only on training/validation windows.
4. Integrate canonical shadow persistence and verifier.
5. Add Telegram shadow display and weekly `Relationship: x/7`.
6. Observe live shadow before promotion proposal.

### Success Metrics and Promotion Gate

Primary KPI: `days with hit_count >= 2 / eligible evaluated days`.

Secondary metrics:

- mean hit count;
- any-hit rate;
- abstention/coverage;
- anchor rejection rate and counterfactual effect;
- result by province pair/weekday;
- maximum losing streak;
- paired daily delta versus production ensemble and consensus-only baseline.

Promotion requires deterministic replay, no leakage, adequate coverage, improvement over `R-A` outside sample, `guard_on` not worse than `guard_off`, fault isolation, and a live-shadow observation period. No fixed uplift percentage should be invented before measuring baseline variance.

## Research Synthesis

### Executive Summary

`relationship` là một interpretable XSMN shadow challenger. Nó không huấn luyện classifier mới; thay vào đó, nó tổng hợp Top-5 của mọi model source, chọn số đầu tiên bằng independent model-family consensus, kiểm tra anchor qua hai occasion merged gần nhất, rồi chọn hai companion bằng quan hệ lịch sử và direct combo evidence. Thiết kế này bảo toàn auditability: mỗi quyết định đều truy ngược được về model votes, ranks, target provinces, matched draw dates và co-hit counts.

**Quyết định BA:** Go cho implementation/backtest ở shadow mode; No-Go cho production promotion tại thời điểm nghiên cứu. Chưa có backtest thực tế nên chưa thể khẳng định uplift, chọn trọng số cuối hoặc gọi score là probability.

**Key findings:**

- Production hiện có sáu model families; logic phải dùng denominator động, không hard-code ví dụ 5/5.
- Một model xuất hiện ở hai tỉnh chỉ tạo một independent family vote; province support là feature khác.
- Tỷ lệ `1,15%` có thể chỉ đại diện một hit trên khoảng 87 kỳ, nên count và uncertainty quan trọng hơn số thập phân hiển thị.
- Anchor 2/2 recent merged hits bị loại theo business guardrail; rule này phải có counterfactual backtest.
- Companion selector phải score cả tam giác, không chỉ một chain quan hệ.
- Direct historical `>=2/3` rate gần KPI hơn pairwise rate và phải là một ablation riêng.
- Stack hiện tại đủ; chi phí tính toán nhỏ, rủi ro chính là leakage và overfitting chứ không phải scale.

### Canonical Business Definition

**Tên:** `relationship`
**Version đầu:** `relationship_v1`
**Scope:** `XSMN/all` cho đúng target-province set trong ngày
**Mode ban đầu:** `shadow`
**Primary success condition:** `hit_count >= 2` trên Top-3 unique
**Score semantics:** `ranking_score_uncalibrated`

Input gồm tất cả model results thành công. Mỗi `model@province` đóng góp tối đa Top-5 hợp lệ; Top-10 object gốc không bị sửa. Model failures được loại khỏi denominator nếu vẫn đạt minimum active-family gate.

### End-to-End Decision Flow

```text
Runtime model results
  → validate/dedupe Top-5 per model@province
  → aggregate one vote per model family
  → rank anchor candidates
  → load last 2 matched merged occasions (< target date)
  → reject each anchor with recent_hits == 2
  → lock first eligible anchor
  → build matched-history co-occurrence evidence
  → enumerate companion pairs
  → enforce 3 distinct unit digits
  → score node + triangle edges + direct historical ≥2/3
  → deterministic Top-3 or explicit abstention
  → persist relationship shadow
  → verify and include in weekly report
```

### Data Grain and Leakage Invariants

- Prediction grain: `(prediction_date, region='XSMN', province='all', model_name='relationship')`.
- Source grain: `(prediction_date, province, model_family)`.
- History grain: matched occasion containing all target provinces, not unrelated nearest dates.
- Every historical draw used for prediction date `t` must satisfy `draw_date < t`.
- Lookback is measured in matched draw occurrences, not calendar days.
- Backtest reconstructs same-day archived Top-5; it does not regenerate past model outputs with current model versions.

Rolling-origin evaluation ensures every forecast is built only from earlier observations. [Forecasting: Principles and Practice — time-series cross-validation](https://otexts.com/fpp3/tscv.html)

### Scoring Contract

Node evidence:

```text
family_vote_ratio = distinct voting families / active families
rank_score        = mean best normalized Top-5 rank by family
province_coverage = selecting target provinces / target provinces
credibility_vote  = normalized credibility-weighted family support
```

Pair evidence for numbers `a,b` over `N` matched occasions:

```text
support_ab = n_ab / N
confidence_a_to_b = n_ab / n_a
lift_ab = (n_ab × N) / (n_a × n_b)
joint_shrunk_ab = (n_ab + k × p0_ab) / (N + k)
p0_ab = (n_a/N) × (n_b/N)
```

Combo evidence:

```text
h_t(a,b,c) = 1 when at least two of a,b,c hit merged tails at t
rate_2of3  = sum(h_t) / N
```

Raw support/confidence/lift originate from association-rule analysis, but rare rules require careful support and probabilistic filtering. [Agrawal et al. 1993](https://doi.org/10.1145/170035.170072), [Hahsler & Hornik](https://doi.org/10.3233/IDA-2007-11502)

### Anchor Guard Contract

Given two most recent matched merged occasions `r1,r2`:

- If anchor is in both `r1.merged_tails` and `r2.merged_tails`, reject with `consecutive_merged_hit_2of2`.
- If anchor is in zero or one occasion, it remains eligible.
- A same-occasion hit in both provinces counts once.
- Rule applies only to anchor.
- Fewer than two matched occasions returns `insufficient_recent_history`.
- No remaining eligible anchor returns `no_eligible_anchor`.

This guard is a declared business policy, not a claim of statistical dependence between lottery draws.

### Output and Audit Contract

Successful payload must expose:

- Top-3 pairs and ranking scores;
- selected anchor and full rejected-anchor audit;
- active/skipped model families;
- source Top-5 snapshot;
- two recent merged occasion dates and anchor presence;
- matched history count and cutoff;
- three edge evidence records with raw counts/rates and shrunk values;
- direct historical ≥2/3 count/rate;
- config and model version;
- deterministic tie-break trace.

Non-success payload returns an explicit status and empty Top-3. It must never silently relax anchor or unit-digit constraints.

### Acceptance Criteria

1. **Independent votes:** Given the same family selects `11` in both provinces, when votes are aggregated, then `11` receives one family vote and province coverage two.
2. **Dynamic denominator:** Given one model family fails, when minimum active-family gate still passes, then vote ratio uses only active families and skipped family is audited.
3. **Top-5 boundary:** Given a source returns Top-10, when `relationship` runs, then only ranks 1–5 contribute and the source object remains unchanged.
4. **Anchor exclusion:** Given candidate `11` occurs in both recent matched merged occasions, when anchor selection runs, then `11` is rejected and the next ranked candidate is evaluated.
5. **Anchor acceptance:** Given candidate occurs in at most one of the two occasions, then the recent guard does not reject it.
6. **Fail closed:** Given fewer than two recent occasions or no eligible anchor, then no Top-3 is emitted.
7. **Leakage safety:** Given target date `t`, then no row dated `t` or later contributes to history, normalization or tuning.
8. **Triangle evidence:** Given anchor and two companions, then all three pair edges and direct ≥2/3 history appear in audit.
9. **Diversity:** Given the best raw combo repeats a unit digit, then it is rejected if a valid three-unit combo exists; otherwise the function abstains.
10. **Determinism:** Given identical inputs/config/version, repeated calls return byte-equivalent canonical output.
11. **Fault isolation:** Given relationship throws or returns insufficient evidence, production ensemble, persistence and Telegram remain unaffected.
12. **Verification:** Given a successful relationship Top-3, then verifier uses exact target provinces and sets `combo_hit` only when at least two unique pairs match.

### Evaluation and Promotion Framework

Required ablations:

| Variant | Evidence |
|---|---|
| R-A | consensus + rank |
| R-B | R-A + pair relationships |
| R-C | R-B + direct historical ≥2/3 |
| R-C guard_off | full score without anchor guard |
| R-C guard_on | full method named `relationship` |

Primary report is days won/evaluated under `>=2/3`. Report coverage beside hit rate so abstention cannot inflate performance. Compare variants only on paired eligible dates and break down by target-province set/weekday. Promotion needs an untouched out-of-sample result and live-shadow observation; current research intentionally does not invent a required uplift before baseline variance is known.

### Strategic Roadmap

1. Create an implementation spec from this research.
2. Implement pure core, repositories and focused tests.
3. Backfill archived Top-5 with rolling-origin evaluation.
4. Review evidence and choose a frozen v1 config.
5. Add production shadow persistence, verification, Telegram display and weekly `Relationship: x/7`.
6. Observe and compare with production ensemble.
7. Produce a separate promotion decision; never auto-promote from backtest alone.

Gradual rollout with explicit validation reduces the impact of undetected defects and metric surprises. [Google SRE canarying releases](https://sre.google/workbook/canarying-releases/)

### Risks and Explicit Non-Goals

**Risks:** low-support edges, correlated model families, future leakage, anchor guard harming accuracy, overfit lookbacks/weights, missing archived Top-5, incomplete pagination and score mislabeling.

**Non-goals for v1:** replacing production ensemble, adding a neural network, adding a graph database, claiming calibrated probabilities, changing XSMB, allowing CMR/DDT to vote, or hard-coding exactly five active models.

### Research Methodology, Sources and Limitations

The research combined repository inspection with current official documentation and primary/peer-reviewed work covering Python/Pandas/NumPy/PostgreSQL, Supabase pagination/indexing, time-ordered validation, probability calibration, association rules, small-sample proportions, FDR and staged rollout.

Core references:

- [Python Standard Library](https://docs.python.org/3/library/index.html)
- [Pandas GroupBy](https://pandas.pydata.org/pandas-docs/stable/user_guide/groupby.html)
- [PostgreSQL aggregates](https://www.postgresql.org/docs/current/functions-aggregate.html)
- [Supabase Python select and pagination](https://supabase.com/docs/reference/python/select)
- [scikit-learn data leakage](https://scikit-learn.org/stable/common_pitfalls.html)
- [scikit-learn probability calibration](https://scikit-learn.org/stable/modules/calibration.html)
- [NIST small-sample binomial intervals](https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
- [Google SRE staged launch guidance](https://sre.google/sre-book/reliable-product-launches/)

Limitations:

- Không query/backtest dữ liệu production trong research workflow này.
- Các config defaults là hypotheses, chưa được fit hoặc validate.
- Chất lượng archived model Top-5 và matched province history chưa được audit.
- Không có bằng chứng hiện tại rằng relationship hoặc anchor guard tăng hit rate.
- Việc random lottery tạo signal rất yếu là khả năng phải được chấp nhận; abstention hoặc không promote là kết quả hợp lệ.

### Technical Research Conclusion

`relationship` là một thiết kế challenger hợp lý vì diễn giải được, tận dụng dữ liệu hiện hữu và tối ưu trực tiếp bộ ba thay vì từng số rời rạc. Giá trị của nó sẽ không đến từ tên “relationship” hay một tỷ lệ co-occurrence đẹp, mà từ kỷ luật phân biệt independent votes, support thật, shrinkage, temporal cutoff và paired out-of-sample evaluation.

Hành động tiếp theo phù hợp là tạo implementation spec và backtest shadow. Production behavior hiện tại phải được giữ nguyên cho tới khi dữ liệu ngoài mẫu chứng minh lợi ích.

**Technical Research Completion Date:** 2026-08-02
**Source Verification:** Current official and primary sources cited throughout
**Technical Confidence:** High về feasibility/architecture; chưa xác định về predictive uplift
