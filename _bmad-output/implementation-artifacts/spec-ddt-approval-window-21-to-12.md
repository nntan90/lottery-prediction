---
title: 'DDT approval window từ 21h hôm trước đến 12h hôm sau'
type: 'feature'
created: '2026-07-27'
status: 'done'
baseline_commit: '4430ca7a852e36057aae28c122a92436959330f9'
review_loop_iteration: 0
context:
  - '{project-root}/docs/project-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-local-ddt-telegram-shadow-lifecycle.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Prompt DDT mặc định 06:30 và TTL ngắn; MacBook thường sleep nên bot có thể không nhận approval.

**Approach:** macOS wake lúc 20:55 (nắp mở), bot giữ system awake và gửi approval 21:00 cho kỳ XSMN ngày D. Approval hợp lệ trong `[D-1 21:00, D 12:00)` giờ Việt Nam và trigger DDT local cho D. Run được chấp nhận trước 12:00 vẫn hoàn tất và gửi Telegram sau 12:00.

## Boundaries & Constraints

**Always:** Dùng datetime aware `Asia/Ho_Chi_Minh`; start inclusive, end exclusive; target/provinces theo D; `/ddt` suy D từ active window, `/ddt D` chỉ hợp lệ trong window của D; wake/restart trong window gửi catch-up nếu DDT chưa success; giữ allowlist, one-shot, single-run reservation; awake assertion được reap khi không còn approval/run lease hoặc bot shutdown.

**Ask First:** Ghi đè `pmset repeat` không thuộc DDT; đổi mốc 20:55/21:00/12:00; cho chạy ngoài window; đổi model, persistence hoặc verifier.

**Never:** `sudo` trong bot; âm thầm thay power schedule; dùng rollover 06:00 suy target; callback từ 12:00; để lỗi scheduler/caffeinate làm chết polling; đổi production Top 3/shadow KPI.

## I/O & Edge-Case Matrix

| Scenario | Expected |
|---|---|
| 21:00 D-1 | Prompt target D, expires 12:00 D |
| 20:59:59 / 21:00 / 11:59:59 / 12:00 | reject / accept / accept / reject |
| Wake 20:55–11:59:59, chưa success | Giữ awake; catch-up đúng D |
| Approve 11:59:59, run qua 12:00 | Giữ awake đến khi persist và Telegram xong |
| DDT failure trước 12:00 | Báo lỗi, giữ awake để cho phép retry |
| Repeat schedule khác tồn tại | Installer dừng, hiển thị schedule, không overwrite |
| `/ddt` ngoài window | Báo giờ mở kế tiếp; không tạo request/subprocess |

</frozen-after-approval>

## Code Map

- `src/scripts/ddt_local_bot.py` — window/target helpers, fixed expiry, scheduler, catch-up, awake leases.
- `scripts/manage_ddt_local_bot.sh` — inspect/install `pmset repeat wakeorpoweron` 20:55.
- `tests/test_ddt_local_bot.py` — time boundaries, callback, scheduler, caffeinate lifecycle.
- `README.md` — contract vận hành, mở nắp/cắm sạc.
- `deploy/launchd/com.vietlottai.ddt-local-bot.plist.template`, `.github/workflows/` — impact check; không đổi lifecycle/production cron.

## Tasks & Acceptance

**Execution:**
- [x] Thêm pure helpers cho approval `[21:00,12:00)`, power guard `[20:55,12:00)`, active D và next prompt.
- [x] Thay TTL bằng close time của D; prompt 21:00 cho D+1; command/callback reject rõ ràng ngoài window.
- [x] Quản lý `/usr/bin/caffeinate -i -w <bot-pid>` bằng approval/run leases: không duplicate/orphan; terminate/await rồi kill fallback; spawn error chỉ log.
- [x] Giữ run lease qua 12:00 đến khi DDT persist và gửi kết quả; failure trước noon trả Telegram nhưng giữ approval lease để retry.
- [x] Thêm manager command inspect/install wake; không overwrite repeat schedule khác; không gọi sudo trong daemon.
- [x] Test fixed timestamps, wake/restart, subprocess args, cleanup, spawn failure, run qua noon và fake `pmset`.
- [x] Cập nhật README; kiểm tra plist/workflows không cần đổi.

**Acceptance Criteria:**
- Given 21:00 27/07, when scheduler runs, then request targets 28/07 and closes exactly 12:00 28/07.
- Given valid user approves at 11:59:59, when DDT finishes after noon, then result persists/sends and awake guard releases afterward; approval at 12:00 rejects.
- Given bot resumes 09:00 with no success, when initialized, then catch-up D and awake guard start; at 13:00 neither starts.
- Given an unrelated repeat schedule, when wake install runs, then it exits without mutation and displays the conflict.

## Spec Change Log

## Design Notes

`pmset repeat` is machine-wide and needs an explicit admin install. LaunchAgent stays logged-in-user scoped. Two leases prevent sleep while waiting for approval and while a run crosses noon. Open lid is confirmed; power adapter is recommended.

## Verification

- `python3 -m pytest -q tests/test_ddt_local_bot.py && python3 -m pytest -q`
- `python3 -m compileall -q src/scripts/ddt_local_bot.py`
- `zsh -n scripts/manage_ddt_local_bot.sh`
- `plutil -lint deploy/launchd/com.vietlottai.ddt-local-bot.plist.template`
- `pmset -g sched`; `git diff --check`
- Restart LaunchAgent; verify running state, clean log, correct Telegram target/close time.

## Suggested Review Order

**Telegram approval control plane**

- Callback acquires awake lease before background execution and releases only after delivery.
  [`ddt_local_bot.py:758`](../../src/scripts/ddt_local_bot.py#L758)

- Prompt scheduler retries transient Telegram failures without duplicating delivered chats.
  [`ddt_local_bot.py:908`](../../src/scripts/ddt_local_bot.py#L908)

- Database success checks leave the Telegram event loop responsive.
  [`ddt_local_bot.py:622`](../../src/scripts/ddt_local_bot.py#L622)

**Time and power lifecycle**

- Pure Vietnam-time helpers define every approval and wake boundary.
  [`ddt_local_bot.py:115`](../../src/scripts/ddt_local_bot.py#L115)

- Shared lease manager owns one monitored, non-orphaning caffeinate process.
  [`ddt_local_bot.py:257`](../../src/scripts/ddt_local_bot.py#L257)

- Power guard rechecks assertion health until success, noon, or shutdown.
  [`ddt_local_bot.py:949`](../../src/scripts/ddt_local_bot.py#L949)

**macOS wake installation**

- Exact schedule matcher handles real pmset output and rejects mixed events.
  [`manage_ddt_local_bot.sh:89`](../../scripts/manage_ddt_local_bot.sh#L89)

- Privileged helper rechecks conflicts immediately before machine-wide mutation.
  [`manage_ddt_local_bot.sh:100`](../../scripts/manage_ddt_local_bot.sh#L100)

- Installer verifies the postcondition before recording schedule ownership.
  [`manage_ddt_local_bot.sh:111`](../../scripts/manage_ddt_local_bot.sh#L111)

**Regression and operations**

- Cross-noon callback test locks persist, delivery, and lease-release order.
  [`test_ddt_local_bot.py:591`](../../tests/test_ddt_local_bot.py#L591)

- Retry and assertion-health tests cover wake-network instability.
  [`test_ddt_local_bot.py:728`](../../tests/test_ddt_local_bot.py#L728)

- Stateful pmset tests validate conflict refusal, install, adoption, and verification.
  [`test_ddt_local_bot.py:965`](../../tests/test_ddt_local_bot.py#L965)

- Operator guide documents wake installation and laptop constraints.
  [`README.md:97`](../../README.md#L97)

- Crash-resume persistence is explicitly deferred for separate approval.
  [`deferred-work.md:47`](deferred-work.md#L47)
