---
title: 'Khôi phục và giám sát scheduler DDT Telegram'
type: 'bugfix'
created: '2026-07-29'
status: 'done'
review_loop_iteration: 0
baseline_commit: '5626cee8b906b436041cf2e5e85d0c4a169b0454'
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-ddt-approval-window-21-to-12.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** LaunchAgent DDT vẫn sống nhưng prompt 21:00 và awake guard không còn hoạt động; vì background task không được supervise nên launchd không phát hiện. Lỗi Telegram delivery hiện bị nuốt, khiến log chỉ còn lỗi polling và không chứng minh được prompt đã gửi hay thất bại.

**Approach:** Chạy prompt/awake worker dưới supervisor tự khởi động lại, dừng application để launchd phục hồi nếu supervisor chết, và ghi log timestamp/redacted cho mọi lần delivery thất bại. Restart worker sau triển khai để gửi catch-up trong cửa sổ approval đang mở.

## Boundaries & Constraints

**Always:** Giữ nguyên cửa sổ `[21:00 D-1, 12:00 D)`, wake 20:55, target/province và allowlist; worker exception phải được log và retry có giới hạn nhịp; shutdown chủ động phải cancel/reap task và caffeinate sạch; mọi log phải redacted, không chứa token/key.

**Ask First:** Đổi giờ/cửa sổ approval, Telegram bot/chat, DDT model, persistence, wake schedule hoặc LaunchAgent scope.

**Never:** Để lỗi scheduler làm chết polling; để supervisor chết mà main process tiếp tục giả-running; gửi prompt trùng cho chat đã delivery trong cùng process; gọi subprocess DDT khi chưa có approval; hardcode credential.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Telegram lỗi tạm thời | `send_message` raise | Log timestamp/redacted, giữ chat chưa delivered và retry | Không làm chết worker/polling |
| Worker raise/return | Prompt hoặc awake worker kết thúc ngoài shutdown | Supervisor log và khởi động worker mới | Nhịp restart bounded |
| Supervisor chết | Task supervisor hoàn tất bất thường | Dừng application để LaunchAgent restart process | Không áp dụng trong shutdown hợp lệ |
| Restart trong window | Chưa có `ddt_shadow` success | Giữ awake và gửi prompt catch-up đúng D | Nếu send lỗi thì tiếp tục retry |

</frozen-after-approval>

## Code Map

- `src/scripts/ddt_local_bot.py` -- delivery logging, worker supervisor, fatal supervisor callback và shutdown lifecycle.
- `tests/test_ddt_local_bot.py` -- delivery failure, worker restart, supervisor death và expected shutdown regression.
- `deploy/launchd/com.vietlottai.ddt-local-bot.plist.template` -- impact check: `KeepAlive` phải tiếp tục restart process sau application stop.
- `scripts/manage_ddt_local_bot.sh` -- operational restart/status/log verification; không đổi wake schedule.

## Tasks & Acceptance

**Execution:**
- [x] `src/scripts/ddt_local_bot.py` -- thêm structured timestamped logger và không nuốt prompt delivery failure.
- [x] `src/scripts/ddt_local_bot.py` -- supervise prompt/awake workers; restart sau ordinary failure/return và stop application nếu supervisor kết thúc bất thường.
- [x] `src/scripts/ddt_local_bot.py` -- đánh dấu shutdown trước cancel để callback không tạo false restart; reap mọi task/caffeinate.
- [x] `tests/test_ddt_local_bot.py` -- khóa transient failure, restart, fatal callback và clean shutdown.
- [x] LaunchAgent/runtime -- restart bot, xác nhận process mới, awake lease và prompt catch-up.

**Acceptance Criteria:**
- Given Telegram delivery raise, when scheduler chạy trong active window, then failure được log redacted và cùng chat được retry mà polling vẫn sống.
- Given một worker raise, when supervisor quan sát, then worker mới được tạo sau bounded delay và không gửi trùng chat đã delivery.
- Given supervisor kết thúc ngoài shutdown, when callback chạy, then application dừng để launchd tạo process mới.
- Given bot restart từ 21:00 đến trước 12:00 và chưa có success, when post-init hoàn tất, then prompt đúng target được gửi và caffeinate tồn tại.

## Spec Change Log

## Design Notes

Supervisor là task dài hạn thuộc `Application`; worker có thể restart nhiều lần nhưng state delivered của prompt phải sống ở supervisor scope. Fatal callback chỉ là hàng rào cuối: ordinary worker exception được supervisor tự phục hồi, còn supervisor termination buộc main process thoát để `KeepAlive` làm đúng nhiệm vụ.

## Verification

**Commands:**
- `python3 -m pytest -q tests/test_ddt_local_bot.py`
- `python3 -m pytest -q`
- `python3 -m compileall -q src/scripts/ddt_local_bot.py`
- `plutil -lint deploy/launchd/com.vietlottai.ddt-local-bot.plist.template`
- `zsh -n scripts/manage_ddt_local_bot.sh`
- `git diff --check`

**Manual checks:**
- Restart LaunchAgent; xác nhận PID mới, log không traceback scheduler, `caffeinate -i -w <pid>` tồn tại và Telegram nhận prompt catch-up đúng ngày.

**Runtime evidence (2026-07-29):**
- LaunchAgent `com.vietlottai.ddt-local-bot` chạy ổn định với PID `40595`.
- Awake lease tồn tại: `/usr/bin/caffeinate -i -w 40595`.
- Log có event `ddt_prompt_delivered` cho target `2026-07-30` lúc `23:06:16 +07:00`; Telegram API đã chấp nhận prompt catch-up.

**Review resolution:**
- Mở rộng redaction cho authorization bearer, access/refresh token, password, API key và client secret; logger không được phép làm chết scheduler.
- Log rõ prompt/fallback delivery success; fallback chỉ cảnh báo một lần trong cùng target window.
- Xóa request token chưa từng delivery và ghi tiến độ từng chat ngay khi gửi thành công để worker restart không gửi trùng batch đã hoàn tất một phần.
- Supervisor chết trước khi `Application.running` sẽ dừng event loop để LaunchAgent nhìn thấy process exit.

## Suggested Review Order

**Scheduler lifecycle**

- Entry point gắn hai supervisor vào lifecycle và giữ reference để shutdown sạch.
  [`ddt_local_bot.py:1191`](../../src/scripts/ddt_local_bot.py#L1191)

- Supervisor phục hồi worker lỗi theo nhịp cố định mà không làm chết polling.
  [`ddt_local_bot.py:1115`](../../src/scripts/ddt_local_bot.py#L1115)

- Fatal callback buộc process thoát nếu chính supervisor ngừng bất thường.
  [`ddt_local_bot.py:1158`](../../src/scripts/ddt_local_bot.py#L1158)

- Shutdown đánh dấu trước, cancel/reap task rồi mới dọn awake lease.
  [`ddt_local_bot.py:1221`](../../src/scripts/ddt_local_bot.py#L1221)

**Telegram delivery safety**

- Gửi prompt ghi success/failure, xóa token lỗi và tránh fallback spam.
  [`ddt_local_bot.py:941`](../../src/scripts/ddt_local_bot.py#L941)

- State dùng chung giữ tiến độ delivery xuyên qua worker restart.
  [`ddt_local_bot.py:298`](../../src/scripts/ddt_local_bot.py#L298)

- Structured logger redacted và không được phép trở thành lỗi scheduler thứ hai.
  [`ddt_local_bot.py:64`](../../src/scripts/ddt_local_bot.py#L64)

**Regression coverage**

- Delivery tests khóa redaction, success evidence, fallback và partial batch.
  [`test_ddt_local_bot.py:768`](../../tests/test_ddt_local_bot.py#L768)

- Lifecycle tests khóa worker restart, startup fatal stop và clean shutdown.
  [`test_ddt_local_bot.py:1015`](../../tests/test_ddt_local_bot.py#L1015)
