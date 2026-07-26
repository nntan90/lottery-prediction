from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

import pytest

from src.scripts import ddt_local_bot


def _settings(**overrides) -> ddt_local_bot.DDTBotSettings:
    values = {
        "bot_token": "test-token",
        "allowed_chat_ids": frozenset({100}),
        "allowed_user_ids": frozenset({200}),
        "prompt_time": ddt_local_bot._parse_prompt_time("06:30"),
        "request_ttl_seconds": 60,
        "subprocess_timeout_seconds": 120,
    }
    values.update(overrides)
    return ddt_local_bot.DDTBotSettings(**values)


def test_settings_use_private_chat_as_user_allowlist_fallback(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("DDT_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("DDT_ALLOWED_USER_IDS", raising=False)

    settings = ddt_local_bot.load_settings()

    assert settings.allowed_chat_ids == frozenset({12345})
    assert settings.allowed_user_ids == frozenset({12345})


def test_settings_require_explicit_users_for_group_chat(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-10012345")
    monkeypatch.delenv("DDT_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("DDT_ALLOWED_USER_IDS", raising=False)

    with pytest.raises(ValueError, match="required for Telegram group chats"):
        ddt_local_bot.load_settings()


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
    now = datetime(2026, 7, 26, 6, 30, tzinfo=ddt_local_bot.VN_TZ)
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


def test_expired_callback_never_starts() -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    now = datetime(2026, 7, 26, 6, 30, tzinfo=ddt_local_bot.VN_TZ)
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
        now=now + timedelta(seconds=61),
    )

    assert claimed is None
    assert outcome == "expired"


def test_wrong_user_does_not_consume_request_and_second_claim_is_reserved() -> None:
    controller = ddt_local_bot.DDTLocalController(
        object(),
        _settings(allowed_user_ids=frozenset({200, 201})),
    )
    now = datetime(2026, 7, 26, 6, 30, tzinfo=ddt_local_bot.VN_TZ)
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
    )

    assert request.provinces == ("tien-giang", "kien-giang")


def test_execute_persists_once_and_keeps_lock_responsive(monkeypatch) -> None:
    controller = ddt_local_bot.DDTLocalController(object(), _settings())
    now = datetime(2026, 7, 26, 6, 30, tzinfo=ddt_local_bot.VN_TZ)
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
            date(2026, 7, 27),
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
