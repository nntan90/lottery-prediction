from __future__ import annotations

import asyncio
from datetime import date, datetime
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from src.scripts import ddt_local_bot


def _settings(**overrides) -> ddt_local_bot.DDTBotSettings:
    values = {
        "bot_token": "test-token",
        "allowed_chat_ids": frozenset({100}),
        "allowed_user_ids": frozenset({200}),
        "prompt_time": ddt_local_bot._parse_prompt_time("21:00"),
        "request_ttl_seconds": 60,
        "subprocess_timeout_seconds": 120,
    }
    values.update(overrides)
    return ddt_local_bot.DDTBotSettings(**values)


def _vn(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int = 0,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        tzinfo=ddt_local_bot.VN_TZ,
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_vn(2026, 7, 27, 20, 59, 59), None),
        (_vn(2026, 7, 27, 21, 0), date(2026, 7, 28)),
        (_vn(2026, 7, 28, 11, 59, 59), date(2026, 7, 28)),
        (_vn(2026, 7, 28, 12, 0), None),
    ],
)
def test_active_approval_target_has_exact_open_close_boundaries(
    now: datetime,
    expected: date | None,
) -> None:
    assert ddt_local_bot.active_approval_target(now) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_vn(2026, 7, 27, 20, 54, 59), None),
        (_vn(2026, 7, 27, 20, 55), date(2026, 7, 28)),
        (_vn(2026, 7, 28, 11, 59, 59), date(2026, 7, 28)),
        (_vn(2026, 7, 28, 12, 0), None),
    ],
)
def test_power_guard_starts_five_minutes_before_prompt(
    now: datetime,
    expected: date | None,
) -> None:
    assert ddt_local_bot.active_power_guard_target(now) == expected


def test_window_helpers_require_aware_time_and_resolve_next_boundaries() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ddt_local_bot.active_approval_target(datetime(2026, 7, 27, 21, 0))

    assert ddt_local_bot.next_prompt_at(_vn(2026, 7, 27, 20, 59)) == _vn(
        2026, 7, 27, 21, 0
    )
    assert ddt_local_bot.next_prompt_at(_vn(2026, 7, 27, 21, 0)) == _vn(
        2026, 7, 28, 21, 0
    )
    assert ddt_local_bot.next_power_guard_at(_vn(2026, 7, 27, 20, 54)) == _vn(
        2026, 7, 27, 20, 55
    )


def test_settings_use_private_chat_as_user_allowlist_fallback(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("DDT_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("DDT_ALLOWED_USER_IDS", raising=False)

    settings = ddt_local_bot.load_settings()

    assert settings.allowed_chat_ids == frozenset({12345})
    assert settings.allowed_user_ids == frozenset({12345})
    assert settings.prompt_time == ddt_local_bot._parse_prompt_time("21:00")


def test_settings_require_explicit_users_for_group_chat(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10012345")
    monkeypatch.delenv("DDT_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("DDT_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ValueError, match="required for Telegram group chats"):
        ddt_local_bot.load_settings()


def test_legacy_ttl_override_cannot_break_fixed_window_startup(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("DDT_LOCAL_REQUEST_TTL_SECONDS", "obsolete-value")
    monkeypatch.delenv("DDT_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("DDT_ALLOWED_USER_IDS", raising=False)

    settings = ddt_local_bot.load_settings()

    assert (
        settings.request_ttl_seconds
        == ddt_local_bot.DEFAULT_REQUEST_TTL_SECONDS
    )


@pytest.mark.parametrize(
    ("status", "returncode"),
    [
        ("success", 0),
        ("uncalibrated", 0),
        ("insufficient_evidence", 2),
        ("error", 3),
    ],
)
def test_cli_contract_accepts_only_expected_status_exit_pairs(
    status: str,
    returncode: int,
) -> None:
    payload = ddt_local_bot.parse_ddt_cli_contract(
        f'{{"status":"{status}","reason":"short reason"}}',
        "",
        returncode,
    )

    assert payload["status"] == status
    assert payload["reason"] == "short reason"


def test_cli_contract_preserves_stderr_and_rejects_false_success() -> None:
    empty = ddt_local_bot.parse_ddt_cli_contract(
        "",
        "Traceback\nValueError: exact schedule mismatch",
        3,
    )
    false_success = ddt_local_bot.parse_ddt_cli_contract(
        '{"status":"success","selected_evidence":[]}',
        "child failed",
        3,
    )

    assert "exact schedule mismatch" in empty["reason"]
    assert false_success["status"] == "error"
    assert "expected=0" in false_success["reason"]


def test_request_requires_both_allowlists_and_callback_is_one_shot() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    now = _vn(2026, 7, 25, 21, 0)
    request = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=now,
    )

    assert controller.claim_request(
        request.token,
        chat_id=999,
        user_id=200,
        now=now,
    )[1] == "unauthorized"
    claimed, outcome = controller.claim_request(
        request.token,
        chat_id=100,
        user_id=200,
        now=now,
    )
    assert claimed is request
    assert outcome == "approved"
    assert controller.claim_request(
        request.token,
        chat_id=100,
        user_id=200,
        now=now,
    )[1] == "already_used"
    assert request.expires_at == _vn(2026, 7, 26, 12, 0)


def test_expired_callback_never_starts() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    now = _vn(2026, 7, 25, 21, 0)
    request = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=now,
    )

    claimed, outcome = controller.claim_request(
        request.token,
        chat_id=100,
        user_id=200,
        now=_vn(2026, 7, 26, 12, 0),
    )

    assert claimed is None
    assert outcome == "expired"


def test_callback_at_last_valid_second_is_approved() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    request = controller.create_request(
        date(2026, 7, 28),
        chat_id=100,
        requested_by=200,
        now=_vn(2026, 7, 27, 21, 0),
    )

    claimed, outcome = controller.claim_request(
        request.token,
        chat_id=100,
        user_id=200,
        now=_vn(2026, 7, 28, 11, 59, 59),
    )

    assert claimed is request
    assert outcome == "approved"


def test_wrong_user_does_not_consume_request_and_second_claim_is_reserved() -> None:
    controller = ddt_local_bot.DDTLocalController(
        object(),
        _settings(allowed_user_ids=frozenset({200, 201})),
    )
    now = _vn(2026, 7, 25, 21, 0)
    first = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=now,
    )
    second = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=201,
        now=now,
    )

    assert controller.claim_request(
        first.token,
        chat_id=100,
        user_id=201,
        now=now,
    )[1] == "wrong_user"
    assert first.used is False
    assert controller.claim_request(
        first.token,
        chat_id=100,
        user_id=200,
        now=now,
    )[1] == "approved"
    assert controller.claim_request(
        second.token,
        chat_id=100,
        user_id=201,
        now=now,
    )[1] == "run_in_progress"


def test_local_schedule_ignores_target_provinces_override(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_PROVINCES", "can-tho,ben-tre")
    controller = ddt_local_bot.DDTLocalController(object(), _settings())

    request = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=_vn(2026, 7, 25, 21, 0),
    )

    assert request.provinces == ("tien-giang", "kien-giang")


def test_execute_persists_once_and_keeps_lock_responsive(monkeypatch) -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    now = _vn(2026, 7, 25, 21, 0)
    request = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=now,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    saved: list[dict] = []

    async def run(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {
            "status": "uncalibrated",
            "model_name": "provincial_digit_transition_v1",
            "score_semantics": "merged_pair_hit_likelihood_uncalibrated",
            "selected_evidence": [
                {"pair": 3, "estimated_likelihood_uncalibrated": 0.3},
                {"pair": 12, "estimated_likelihood_uncalibrated": 0.2},
                {"pair": 25, "estimated_likelihood_uncalibrated": 0.1},
            ],
        }, 1234

    monkeypatch.setattr(ddt_local_bot, "run_ddt_subprocess", run)
    monkeypatch.setattr(ddt_local_bot, "get_shadow_prediction", lambda *_a: None)
    monkeypatch.setattr(
        ddt_local_bot,
        "save_shadow_prediction",
        lambda _db, record: saved.append(record) or True,
    )

    async def scenario() -> str:
        task = asyncio.create_task(controller.execute_request(request))
        await started.wait()
        assert controller.run_lock.locked()
        competing = controller.create_request(
            date(2026, 7, 26),
            chat_id=100,
            requested_by=200,
            now=now,
        )
        assert controller.claim_request(
            competing.token,
            chat_id=100,
            user_id=200,
            now=now,
        )[1] == "run_in_progress"
        release.set()
        return await task

    message = asyncio.run(scenario())

    assert len(saved) == 1
    assert saved[0]["model_name"] == "ddt_shadow"
    assert saved[0]["run_metadata"]["execution_source"] == "local_telegram"
    assert "03</code> | <code>12</code> | <code>25" in message


def test_timeout_kills_child_and_returns_persistable_error(monkeypatch) -> None:
    class FakeProcess:
        returncode = None
        killed = False

        async def communicate(self):
            if self.killed:
                return b"", b""
            await asyncio.sleep(0.05)
            return b"", b""

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = FakeProcess()

    async def create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    payload, _runtime = asyncio.run(
        ddt_local_bot.run_ddt_subprocess(
            date(2026, 7, 26),
            ["tien-giang", "kien-giang"],
            timeout_seconds=0.001,
        )
    )

    assert process.killed is True
    assert payload == {
        "status": "error",
        "reason": "shadow timeout after 0.001s",
    }


def test_spawn_failure_is_persisted_and_reported(monkeypatch) -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    request = controller.create_request(
        date(2026, 7, 26),
        chat_id=100,
        requested_by=200,
        now=_vn(2026, 7, 25, 21, 0),
    )
    saved: list[dict] = []

    async def fail(*_args, **_kwargs):
        raise OSError("python executable unavailable")

    monkeypatch.setattr(ddt_local_bot, "run_ddt_subprocess", fail)
    monkeypatch.setattr(ddt_local_bot, "get_shadow_prediction", lambda *_a: None)
    monkeypatch.setattr(
        ddt_local_bot,
        "save_shadow_prediction",
        lambda _db, record: saved.append(record) or True,
    )

    message = asyncio.run(controller.execute_request(request))

    assert saved[0]["status"] == "error"
    assert saved[0]["error_message"] == "python executable unavailable"
    assert "Lỗi DDT: python executable unavailable" in message


def test_request_rejects_before_open_and_at_close() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())

    with pytest.raises(ValueError, match="Khung tiếp theo"):
        controller.create_request(
            date(2026, 7, 28),
            chat_id=100,
            requested_by=200,
            now=_vn(2026, 7, 27, 20, 59, 59),
        )
    with pytest.raises(ValueError, match="Khung tiếp theo"):
        controller.create_request(
            date(2026, 7, 28),
            chat_id=100,
            requested_by=200,
            now=_vn(2026, 7, 28, 12, 0),
        )
    with pytest.raises(ValueError, match="supported range"):
        controller.create_request(
            date.min,
            chat_id=100,
            requested_by=200,
            now=_vn(2026, 7, 27, 21, 0),
        )


def test_request_text_uses_fixed_absolute_close_time() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    request = controller.create_request(
        date(2026, 7, 28),
        chat_id=100,
        requested_by=200,
        now=_vn(2026, 7, 27, 21, 0),
    )

    assert "12:00 28/07/2026" in ddt_local_bot._request_text(request)


class _FakeCaffeinate:
    def __init__(self, *, stop_on_terminate: bool = True) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.stop_on_terminate = stop_on_terminate

    def terminate(self) -> None:
        self.terminated = True
        if self.stop_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            await asyncio.sleep(10)
        return int(self.returncode or 0)


def test_awake_leases_do_not_duplicate_and_run_lease_crosses_noon(
    monkeypatch,
) -> None:
    process = _FakeCaffeinate()
    calls: list[tuple] = []

    async def create(*args, **kwargs):
        calls.append(args)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def scenario() -> None:
        manager = ddt_local_bot.AwakeLeaseManager()
        await manager.acquire("approval_window")
        await manager.acquire("run:token")
        assert len(calls) == 1
        await manager.release("approval_window")
        assert process.terminated is False
        assert manager.leases == frozenset({"run:token"})
        await manager.release("run:token")
        assert process.terminated is True

    asyncio.run(scenario())

    assert calls == [
        (
            "/usr/bin/caffeinate",
            "-i",
            "-w",
            str(os.getpid()),
        )
    ]


def test_awake_spawn_failure_is_nonfatal(monkeypatch, capsys) -> None:
    process = _FakeCaffeinate()
    attempts = 0

    async def fail_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("caffeinate missing")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_once)

    async def scenario() -> None:
        manager = ddt_local_bot.AwakeLeaseManager()
        await manager.acquire("approval_window")
        assert manager.leases == frozenset({"approval_window"})
        assert manager.process is None
        await manager.acquire("approval_window")
        assert manager.process is process
        await manager.shutdown()

    asyncio.run(scenario())
    assert attempts == 2
    assert "caffeinate unavailable" in capsys.readouterr().out


def test_awake_shutdown_kills_and_reaps_stuck_child(monkeypatch) -> None:
    process = _FakeCaffeinate(stop_on_terminate=False)

    async def create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(ddt_local_bot, "CAFFEINATE_STOP_TIMEOUT_SECONDS", 0.001)

    async def scenario() -> None:
        manager = ddt_local_bot.AwakeLeaseManager()
        await manager.acquire("approval_window")
        await manager.shutdown()

    asyncio.run(scenario())
    assert process.terminated is True
    assert process.killed is True


def test_awake_cleanup_tolerates_child_exit_race(monkeypatch) -> None:
    class ExitedDuringTerminate(_FakeCaffeinate):
        def terminate(self) -> None:
            self.returncode = 0
            raise ProcessLookupError

    process = ExitedDuringTerminate()

    async def create(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)

    async def scenario() -> None:
        manager = ddt_local_bot.AwakeLeaseManager()
        await manager.acquire("approval_window")
        await manager.release("approval_window")

    asyncio.run(scenario())
    assert process.returncode == 0


def test_callback_approved_before_noon_holds_run_lease_until_delivery(
    monkeypatch,
) -> None:
    class FrozenDateTime(datetime):
        current = _vn(2026, 7, 28, 11, 59, 59)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    request = controller.create_request(
        date(2026, 7, 28),
        chat_id=100,
        requested_by=None,
        now=_vn(2026, 7, 27, 21, 0),
    )
    events: list[str] = []

    async def execute(_request):
        events.append("persist")
        FrozenDateTime.current = _vn(2026, 7, 28, 12, 0, 1)
        return "✅ DDT complete"

    controller.execute_request = execute
    success_checks = 0

    async def persisted_success(_controller, _target_date):
        nonlocal success_checks
        success_checks += 1
        return success_checks >= 2

    class FakeLeases:
        async def acquire(self, lease: str) -> None:
            events.append(f"acquire:{lease}")

        async def release(self, lease: str) -> None:
            events.append(f"release:{lease}")

    class FakeQuery:
        data = f"ddt:run:{request.token}"
        message = SimpleNamespace(chat=SimpleNamespace(id=100))
        from_user = SimpleNamespace(id=200)

        async def answer(self, *_args, **_kwargs) -> None:
            events.append("ack")

        async def edit_message_text(self, text, **_kwargs) -> None:
            events.append("running" if "đang chạy" in text else "edit-outcome")

    class FakeBot:
        async def send_message(self, **_kwargs) -> None:
            events.append("telegram")

    class FakeApplication:
        def __init__(self) -> None:
            self.bot_data = {
                "ddt_controller": controller,
                "ddt_awake_leases": FakeLeases(),
            }
            self.tasks: list[asyncio.Task] = []

        def create_task(self, coroutine, *, name):
            task = asyncio.create_task(coroutine, name=name)
            self.tasks.append(task)
            return task

    application = FakeApplication()
    context = SimpleNamespace(application=application, bot=FakeBot())
    update = SimpleNamespace(callback_query=FakeQuery())
    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)
    monkeypatch.setattr(
        ddt_local_bot,
        "_has_persisted_success",
        persisted_success,
    )

    async def scenario() -> None:
        await ddt_local_bot._ddt_callback(update, context)
        await asyncio.gather(*application.tasks)

    asyncio.run(scenario())

    assert events == [
        f"acquire:run:{request.token}",
        "ack",
        "running",
        "persist",
        "telegram",
        "release:approval_window",
        f"release:run:{request.token}",
    ]
    assert controller.reserved_token is None
    assert FrozenDateTime.current == _vn(2026, 7, 28, 12, 0, 1)


@pytest.mark.parametrize(
    ("frozen_now", "expected_targets"),
    [
        (_vn(2026, 7, 28, 9, 0), [date(2026, 7, 28)]),
        (_vn(2026, 7, 28, 13, 0), []),
    ],
)
def test_prompt_loop_catches_up_only_inside_active_window(
    monkeypatch,
    frozen_now: datetime,
    expected_targets: list[date],
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    sent: list[date] = []

    async def send(_application, target_date, _chat_ids=None):
        sent.append(target_date)
        return {100}

    class FakeApplication:
        def __init__(self) -> None:
            controller = ddt_local_bot.DDTLocalController(object(), _settings())
            self.bot_data = {"ddt_controller": controller}

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)
    monkeypatch.setattr(ddt_local_bot, "_send_daily_prompts", send)
    monkeypatch.setattr(asyncio, "sleep", stop_after_first_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ddt_local_bot._daily_prompt_loop(FakeApplication()))

    assert sent == expected_targets


def test_prompt_loop_retries_transient_delivery_failure(monkeypatch) -> None:
    frozen_now = _vn(2026, 7, 28, 9, 0)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    attempts = 0

    async def send(_application, _target_date, _chat_ids=None):
        nonlocal attempts
        attempts += 1
        return set() if attempts == 1 else {100}

    sleep_calls = 0

    async def allow_one_retry(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    application = type(
        "FakeApplication",
        (),
        {"bot_data": {"ddt_controller": controller}},
    )()
    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)
    monkeypatch.setattr(ddt_local_bot, "_send_daily_prompts", send)
    monkeypatch.setattr(asyncio, "sleep", allow_one_retry)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ddt_local_bot._daily_prompt_loop(application))

    assert attempts == 2


@pytest.mark.parametrize(
    ("frozen_now", "expected_calls"),
    [
        (
            _vn(2026, 7, 28, 9, 0),
            [("acquire", "approval_window"), ("release", "approval_window")],
        ),
        (_vn(2026, 7, 28, 13, 0), []),
    ],
)
def test_awake_guard_catches_up_only_inside_power_window(
    monkeypatch,
    frozen_now: datetime,
    expected_calls: list[tuple[str, str]],
) -> None:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    class FakeLeases:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def acquire(self, lease: str) -> None:
            self.calls.append(("acquire", lease))

        async def release(self, lease: str) -> None:
            self.calls.append(("release", lease))

    class FakeApplication:
        def __init__(self, leases) -> None:
            controller = ddt_local_bot.DDTLocalController(object(), _settings())
            self.bot_data = {
                "ddt_awake_leases": leases,
                "ddt_controller": controller,
            }

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    leases = FakeLeases()
    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)

    async def no_success(_controller, _target_date):
        return False

    monkeypatch.setattr(ddt_local_bot, "_has_persisted_success", no_success)
    monkeypatch.setattr(asyncio, "sleep", stop_after_first_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ddt_local_bot._awake_guard_loop(FakeApplication(leases)))

    assert leases.calls == expected_calls


def test_awake_guard_rechecks_caffeinate_health_during_window(
    monkeypatch,
) -> None:
    frozen_now = _vn(2026, 7, 28, 9, 0)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    class FakeLeases:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def acquire(self, lease: str) -> None:
            self.calls.append(("acquire", lease))

        async def release(self, lease: str) -> None:
            self.calls.append(("release", lease))

    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    leases = FakeLeases()
    application = type(
        "FakeApplication",
        (),
        {
            "bot_data": {
                "ddt_awake_leases": leases,
                "ddt_controller": controller,
            }
        },
    )()
    sleep_calls = 0

    async def allow_one_healthcheck(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    async def no_success(_controller, _target_date):
        return False

    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)
    monkeypatch.setattr(ddt_local_bot, "_has_persisted_success", no_success)
    monkeypatch.setattr(asyncio, "sleep", allow_one_healthcheck)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ddt_local_bot._awake_guard_loop(application))

    assert leases.calls == [
        ("acquire", "approval_window"),
        ("acquire", "approval_window"),
        ("release", "approval_window"),
    ]


def test_awake_guard_skips_lease_when_target_already_succeeded(
    monkeypatch,
) -> None:
    frozen_now = _vn(2026, 7, 28, 9, 0)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now

    class FakeLeases:
        calls: list[tuple[str, str]] = []

        async def acquire(self, lease: str) -> None:
            self.calls.append(("acquire", lease))

        async def release(self, lease: str) -> None:
            self.calls.append(("release", lease))

    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    leases = FakeLeases()
    application = type(
        "FakeApplication",
        (),
        {
            "bot_data": {
                "ddt_awake_leases": leases,
                "ddt_controller": controller,
            }
        },
    )()

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(ddt_local_bot, "datetime", FrozenDateTime)

    async def has_success(_controller, _target_date):
        return True

    monkeypatch.setattr(ddt_local_bot, "_has_persisted_success", has_success)
    monkeypatch.setattr(asyncio, "sleep", stop_after_first_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ddt_local_bot._awake_guard_loop(application))

    assert leases.calls == []


def _fake_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_wake_install_refuses_unrelated_repeat_schedule(tmp_path: Path) -> None:
    fake_pmset = tmp_path / "pmset"
    fake_sudo = tmp_path / "sudo"
    _fake_executable(
        fake_pmset,
        'printf "Repeating power events:\\n  wakepoweron at 07:00:00 every day\\n'
        'Scheduled power events:\\n"\n',
    )
    _fake_executable(fake_sudo, 'printf "sudo must not run\\n" >&2\nexit 99\n')
    script = Path(__file__).parents[1] / "scripts/manage_ddt_local_bot.sh"
    env = {
        **os.environ,
        "DDT_PMSET_BIN": str(fake_pmset),
        "DDT_SUDO_BIN": str(fake_sudo),
        "DDT_LOG_DIR": str(tmp_path / "state"),
    }

    result = subprocess.run(
        [str(script), "wake-install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 3
    assert "Refusing to overwrite" in result.stderr
    assert "07:00:00" in result.stderr
    assert "sudo must not run" not in result.stderr


def test_wake_install_uses_exact_schedule_when_repeat_is_empty(
    tmp_path: Path,
) -> None:
    fake_pmset = tmp_path / "pmset"
    fake_sudo = tmp_path / "sudo"
    state_file = tmp_path / "installed"
    pmset_log = tmp_path / "pmset-call"
    _fake_executable(
        fake_pmset,
        f'if [ "${{1:-}}" = "-g" ]; then\n'
        '  printf "Repeating power events:\\n"\n'
        f'  if [ -f "{state_file}" ]; then\n'
        '    printf "  wakepoweron at 8:55PM every day\\n"\n'
        '  else\n'
        '    printf "  None\\n"\n'
        '  fi\n'
        '  printf "Scheduled power events:\\n"\n'
        'elif [ "${1:-}" = "repeat" ]; then\n'
        f'  : > "{state_file}"\n'
        f'  printf "%s\\n" "$*" > "{pmset_log}"\n'
        "fi\n",
    )
    _fake_executable(
        fake_sudo,
        'exec "$@"\n',
    )
    script = Path(__file__).parents[1] / "scripts/manage_ddt_local_bot.sh"
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "DDT_PMSET_BIN": str(fake_pmset),
        "DDT_SUDO_BIN": str(fake_sudo),
        "DDT_LOG_DIR": str(state_dir),
    }

    result = subprocess.run(
        [str(script), "wake-install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert pmset_log.read_text(encoding="utf-8").strip() == (
        "repeat wakeorpoweron MTWRFSU 20:55:00"
    )
    assert (state_dir / "pmset-repeat-wake-20-55").exists()


def test_wake_install_adopts_exact_12_hour_daily_ddt_schedule(
    tmp_path: Path,
) -> None:
    fake_pmset = tmp_path / "pmset"
    fake_sudo = tmp_path / "sudo"
    _fake_executable(
        fake_pmset,
        'printf "Repeating power events:\\n'
        '  wakepoweron at 8:55PM every day\\nScheduled power events:\\n"\n',
    )
    _fake_executable(fake_sudo, 'printf "sudo must not run\\n" >&2\nexit 99\n')
    script = Path(__file__).parents[1] / "scripts/manage_ddt_local_bot.sh"
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "DDT_PMSET_BIN": str(fake_pmset),
        "DDT_SUDO_BIN": str(fake_sudo),
        "DDT_LOG_DIR": str(state_dir),
    }

    result = subprocess.run(
        [str(script), "wake-install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Adopted" in result.stdout
    assert (state_dir / "pmset-repeat-wake-20-55").exists()
    assert "sudo must not run" not in result.stderr


def test_wake_install_rejects_ddt_wake_mixed_with_another_event(
    tmp_path: Path,
) -> None:
    fake_pmset = tmp_path / "pmset"
    fake_sudo = tmp_path / "sudo"
    _fake_executable(
        fake_pmset,
        'printf "Repeating power events:\\n'
        '  wakepoweron at 8:55PM every day\\n'
        '  shutdown at 11:00PM every day\\n'
        'Scheduled power events:\\n"\n',
    )
    _fake_executable(fake_sudo, 'printf "sudo must not run\\n" >&2\nexit 99\n')
    script = Path(__file__).parents[1] / "scripts/manage_ddt_local_bot.sh"
    env = {
        **os.environ,
        "DDT_PMSET_BIN": str(fake_pmset),
        "DDT_SUDO_BIN": str(fake_sudo),
        "DDT_LOG_DIR": str(tmp_path / "state"),
    }

    result = subprocess.run(
        [str(script), "wake-install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 3
    assert "Refusing to overwrite" in result.stderr
    assert "sudo must not run" not in result.stderr


def test_wake_install_verifies_postcondition_before_marking_owned(
    tmp_path: Path,
) -> None:
    fake_pmset = tmp_path / "pmset"
    fake_sudo = tmp_path / "sudo"
    _fake_executable(
        fake_pmset,
        'printf "Repeating power events:\\n  None\\nScheduled power events:\\n"\n',
    )
    _fake_executable(fake_sudo, 'exit 0\n')
    script = Path(__file__).parents[1] / "scripts/manage_ddt_local_bot.sh"
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "DDT_PMSET_BIN": str(fake_pmset),
        "DDT_SUDO_BIN": str(fake_sudo),
        "DDT_LOG_DIR": str(state_dir),
    }

    result = subprocess.run(
        [str(script), "wake-install"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 4
    assert "exact DDT schedule was not found" in result.stderr
    assert not (state_dir / "pmset-repeat-wake-20-55").exists()
