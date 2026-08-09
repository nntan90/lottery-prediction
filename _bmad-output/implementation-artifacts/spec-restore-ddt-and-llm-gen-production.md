---
title: 'Khôi phục DDT sleep/resume và LLM_Gen AgentRouter production'
type: 'bugfix'
created: '2026-08-09'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'f043c27'
context:
  - 'docs/project-context.md'
  - '_bmad-output/implementation-artifacts/spec-ddt-approval-window-21-to-12.md'
  - '_bmad-output/implementation-artifacts/spec-fix-ddt-scheduler-watchdog.md'
  - '_bmad-output/implementation-artifacts/spec-support-agentrouter-openai-compatible-llm-gen.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** DDT LaunchAgent vẫn chạy nhưng các `asyncio.sleep()` dài bị đóng băng khi Mac sleep, nên lần wake 20:55 không dẫn tới `caffeinate` hoặc prompt 21:00. LLM_Gen đi đúng AgentRouter nhưng secret GitHub vẫn có timestamp trước hai lần chạy bị 401.

**Approach:** Cho scheduler DDT đọc lại giờ Việt Nam qua các khoảng chờ ngắn rồi restart bot. Với LLM_Gen, xác nhận secret đổi trên GitHub, chạy smoke trước daily; chỉ sửa payload nếu lỗi sau xác thực chứng minh incompatibility.

## Boundaries & Constraints

**Always:** Giữ timezone `Asia/Ho_Chi_Minh`, wake 20:55, prompt 21:00, cửa sổ `[21:00 D-1, 12:00 D)` và bounded wall-clock recheck. Giữ target/province, Telegram allowlist, one-shot, run reservation, persisted-success, model DDT và awake lease. LLM_Gen giữ `openai/agentrouter/chat_completions`, model `gpt-5.6-sol`, strict Top 3/diversity, audit DB/Telegram không lộ secret/body thô.

**Ask First:** Đổi giờ/wake/model/persistence DDT; đổi model/provider/backend LLM, bỏ preflight/nới validator; rerun daily nếu tạo Telegram trùng trong ngày.

**Never:** Thêm cloud cron, busy-loop, nhiều `caffeinate`, prompt trùng hoặc tự chạy DDT; hardcode/log API key; fallback provider/model; coi daily `success` là LLM_Gen thành công khi shadow row lỗi.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Wake trước prompt | Sleep qua timer, wake sau 20:55 | Recheck trong một nhịp; acquire một lease | Lỗi lease không làm chết polling |
| Wake trong cửa sổ | Wake/restart sau 21:00, chưa success | Một prompt catch-up đúng target | Retry; không duplicate |
| Đã hoàn tất/hết cửa sổ | Có persisted success hoặc giờ từ 12:00 | Không gửi prompt cũ; release lease | Tiếp tục chờ mốc hợp lệ kế tiếp |
| Secret cũ/401/403 | Timestamp chưa mới hoặc credential bị từ chối | Không chạy daily/đổi model | Báo blocker, không lộ key |
| Model/payload lỗi | Preflight thiếu model hoặc smoke 400/422 | Dừng trước daily | Thiếu model phải hỏi; payload chỉ sửa theo evidence |
| Smoke thành công | Đúng backend/model/wire, Top 3 hợp lệ | Cho phép rerun daily; row LLM_Gen thành công và Telegram đúng nhãn | Xác minh DB/log trước khi kết luận |

</frozen-after-approval>

## Code Map

- `src/scripts/ddt_local_bot.py` -- scheduler prompt/awake và quản lý `caffeinate`.
- `tests/test_ddt_local_bot.py` -- boundary, resume, dedupe, supervisor và lifecycle tests.
- `scripts/manage_ddt_local_bot.sh` -- restart/status LaunchAgent.
- `src/xsmn_llm_gen/` và `tests/test_xsmn_llm_gen.py` -- chỉ sửa khi smoke đã xác thực trả lỗi wire/payload có bằng chứng.
- `.github/workflows/12-test-llm-gen-openai.yml` -- smoke không ghi DB/Telegram.
- `.github/workflows/02-predict-ensemble.yml` -- daily production và Telegram; chỉ rerun sau smoke pass.

## Tasks & Acceptance

**Execution:**
- [x] `src/scripts/ddt_local_bot.py` -- dùng một helper wait-until wall clock có mỗi sleep tối đa 15 giây cho cả năm nhánh: chờ prompt, chờ ngày kế tiếp, retry prompt, health-check lease và chờ power guard; giữ deadline logic 60 giây cho retry/health-check.
- [x] `src/scripts/ddt_local_bot.py` -- không xóa delivery state chỉ vì đồng hồ tạm lùi ra ngoài cửa sổ; chỉ reset khi target hợp lệ đổi -- tránh gửi lại cùng target khi recross 21:00.
- [x] `tests/test_ddt_local_bot.py` -- mô phỏng jump qua 20:55/21:00/12:00, suspend trong retry/health-check và clock rollback; kiểm tra cadence, lease, target và dedupe.
- [x] `scripts/manage_ddt_local_bot.sh` / LaunchAgent -- kiểm tra impact; giữ nguyên manager/plist và ghi nhận rollout restart sau review/push.
- [x] GitHub Actions/AgentRouter -- xác nhận timestamp secret hiện vẫn trước lần 401; khóa smoke/daily theo matrix thay vì gọi key cũ.
- [x] `src/xsmn_llm_gen/` -- không patch vì lỗi dừng ở auth 401; điều kiện incompatibility sau preflight chưa xảy ra.
- [x] `.github/workflows/02-predict-ensemble.yml` -- kiểm tra impact; giữ nguyên workflow và gate rerun sau smoke pass/duyệt duplicate.

**Acceptance Criteria:**
- Given Mac sleep qua mốc, when wake trong cửa sổ, then DDT recheck trong khoảng 15 giây, một lease và tối đa một prompt đúng target.
- Given GitHub secret chưa có timestamp mới hoặc smoke không hợp lệ, when rollout LLM được đánh giá, then daily không bị dispatch và code/model không bị đổi đoán mò.
- Given smoke AgentRouter thành công, when daily chạy, then `XSMN/all/llm_gen` có `status=success`, ba số hợp lệ, `error_message` rỗng, metadata đúng và Telegram hiển thị `LLM_Gen [AgentRouter · GPT-5.6 Sol]`.

## Spec Change Log

- Iteration 1: Review phát hiện hai timer 60 giây vẫn không suspend-safe, bounded loop có thể xóa state khi clock lùi, và lệnh rollout không đủ bằng chứng. Tasks được mở rộng tới cả năm wall-clock waits, giữ deadline retry/health-check 60 giây nhưng chia sleep ≤15 giây, thêm rollback dedupe test và tách rõ các lệnh YAML/plist/process/log. Tránh bản lỗi chỉ sửa ba timer hoặc reset deadline sau mỗi recheck. KEEP: lịch/timezone, helper 15 giây, target/lease/dedupe tests, 546-test baseline và gate AgentRouter an toàn.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_ddt_local_bot.py` -- DDT regression pass.
- `python3 -m pytest -q tests/test_xsmn_llm_gen.py tests/test_llm_gen_openai_smoke.py` -- LLM contracts pass.
- `python3 -m pytest -q` -- full suite pass.
- `python3 -m compileall -q src tests && zsh -n scripts/manage_ddt_local_bot.sh && git diff --check` -- source/shell/diff sạch.
- `.venv/bin/python -c "from pathlib import Path; import yaml; [yaml.safe_load(p.read_text()) for p in Path('.github/workflows').glob('*.yml')]"` và `plutil -lint /Users/tannguyen/Library/LaunchAgents/com.vietlottai.ddt-local-bot.plist` -- YAML/plist parse được.
- `scripts/manage_ddt_local_bot.sh status`, `ps` và kiểm tra `.local/ddt-bot/stdout.log` -- PID mới/running, đúng một `caffeinate`, một prompt catch-up đúng target.
- `gh secret list --repo nntan90/lottery-prediction` và workflow 12 summary -- timestamp mới, smoke `ok=true`, đúng model/backend/wire.

**Manual checks (if no CLI):**
- Sau daily, kiểm tra Supabase row LLM_Gen và Telegram đúng nội dung; không có key, raw response hoặc lỗi 401.

## Suggested Review Order

**Suspend-safe scheduler**

- Entry point giữ deadline cố định nhưng chia mọi timer thành nhịp tối đa 15 giây.
  [`ddt_local_bot.py:236`](../../src/scripts/ddt_local_bot.py#L236)

- Prompt loop dùng helper cho boundary, retry và giữ state khi clock rollback.
  [`ddt_local_bot.py:1046`](../../src/scripts/ddt_local_bot.py#L1046)

- Awake guard dùng cùng cơ chế cho power guard, health-check và noon release.
  [`ddt_local_bot.py:1089`](../../src/scripts/ddt_local_bot.py#L1089)

**Boundary regressions**

- Helper test khóa cadence 60 giây thành bốn sleep 15 giây.
  [`test_ddt_local_bot.py:95`](../../tests/test_ddt_local_bot.py#L95)

- Prompt tests khóa suspend retry, target 21:00 và rollback dedupe.
  [`test_ddt_local_bot.py:757`](../../tests/test_ddt_local_bot.py#L757)

- Awake tests khóa 20:55, 12:00, lease và health-check sau resume.
  [`test_ddt_local_bot.py:1371`](../../tests/test_ddt_local_bot.py#L1371)
