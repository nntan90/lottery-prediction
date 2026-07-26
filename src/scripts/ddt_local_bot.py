"""Local Telegram long-polling controller for the XSMN DDT shadow model."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as clock_time, timedelta
from html import escape
import json
import math
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database.prediction_repo import (
    SHADOW_SUCCESS_STATUSES,
    get_shadow_prediction,
    normalize_shadow_prediction,
    save_shadow_prediction,
)
from src.database.supabase_client import LotteryDB
from src.utils.operational_date import resolve_operational_date
from src.xsmn_digit_transition.config import DigitTransitionConfig
from src.xsmn_ensemble.resolve_provinces import XSMN_ENSEMBLE_SCHEDULE


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DDT_SUBPROCESS_TIMEOUT_SECONDS = 120
DEFAULT_PROMPT_TIME = "06:30"
DEFAULT_REQUEST_TTL_SECONDS = 15 * 60
VALID_EXIT_CODES = {
    "success": 0,
    "uncalibrated": 0,
    "insufficient_evidence": 2,
    "error": 3,
}


def _safe_reason(value: object, limit: int = 240) -> str:
    """Return a compact redacted reason suitable for persistence/Telegram."""
    text = " ".join(str(value).split()) or "ddt_execution_failed"
    text = re.sub(r"https?://\S+", "[redacted-url]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\b(bearer|token|authorization|apikey|api_key)\s*[:=]?\s*\S+",
        r"\1=[redacted]",
        text,
    )
    for key in (
        "SUPABASE_SERVICE_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SUPABASE_DB_URL",
        "SUPABASE_DB_PASSWORD",
    ):
        secret = os.getenv(key, "")
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:limit]


def _parse_id_set(value: str, *, name: str) -> frozenset[int]:
    """Parse a required comma-separated Telegram identifier allowlist."""
    identifiers: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            identifiers.add(int(item))
        except ValueError as exc:
            raise ValueError(f"{name} must contain integer Telegram IDs") from exc
    if not identifiers:
        raise ValueError(f"{name} must not be empty")
    return frozenset(identifiers)


def _parse_prompt_time(value: str) -> clock_time:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("DDT_LOCAL_PROMPT_TIME must use HH:MM") from exc
    return clock_time(parsed.hour, parsed.minute, tzinfo=VN_TZ)


@dataclass(frozen=True)
class DDTBotSettings:
    """Environment-owned settings for the local polling process."""

    bot_token: str
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    prompt_time: clock_time
    request_ttl_seconds: int = DEFAULT_REQUEST_TTL_SECONDS
    subprocess_timeout_seconds: int = DDT_SUBPROCESS_TIMEOUT_SECONDS


def load_settings() -> DDTBotSettings:
    """Load credentials and allowlists without embedding them in launchd."""
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    chat_ids = os.getenv("DDT_ALLOWED_CHAT_IDS", "").strip()
    if not chat_ids:
        chat_ids = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    user_ids = os.getenv("DDT_ALLOWED_USER_IDS", "").strip()
    parsed_chat_ids = _parse_id_set(chat_ids, name="DDT_ALLOWED_CHAT_IDS")
    if not user_ids:
        if any(chat_id <= 0 for chat_id in parsed_chat_ids):
            raise ValueError(
                "DDT_ALLOWED_USER_IDS is required for Telegram group chats"
            )
        user_ids = ",".join(str(chat_id) for chat_id in sorted(parsed_chat_ids))
    ttl = int(os.getenv("DDT_LOCAL_REQUEST_TTL_SECONDS", DEFAULT_REQUEST_TTL_SECONDS))
    timeout = int(
        os.getenv(
            "DDT_LOCAL_TIMEOUT_SECONDS",
            DDT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    )
    if ttl < 30:
        raise ValueError("DDT_LOCAL_REQUEST_TTL_SECONDS must be at least 30")
    if timeout < 1:
        raise ValueError("DDT_LOCAL_TIMEOUT_SECONDS must be positive")
    return DDTBotSettings(
        bot_token=token,
        allowed_chat_ids=parsed_chat_ids,
        allowed_user_ids=_parse_id_set(user_ids, name="DDT_ALLOWED_USER_IDS"),
        prompt_time=_parse_prompt_time(
            os.getenv("DDT_LOCAL_PROMPT_TIME", DEFAULT_PROMPT_TIME).strip()
            or DEFAULT_PROMPT_TIME
        ),
        request_ttl_seconds=ttl,
        subprocess_timeout_seconds=timeout,
    )


@dataclass
class PendingDDTRequest:
    """One authenticated, expiring, one-shot approval request."""

    token: str
    target_date: date
    provinces: tuple[str, str]
    chat_id: int
    requested_by: Optional[int]
    expires_at: datetime
    used: bool = False


def _last_stderr_line(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return _safe_reason(lines[-1]) if lines else ""


def parse_ddt_cli_contract(stdout: str, stderr: str, returncode: int) -> dict:
    """Validate the DDT CLI JSON/status/exit-code contract."""
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        reason = _last_stderr_line(stderr) or f"invalid JSON output: {exc}"
        return {
            "status": "error",
            "reason": _safe_reason(f"DDT rc={returncode}: {reason}"),
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        return {
            "status": "error",
            "reason": _safe_reason(f"DDT rc={returncode}: invalid JSON payload"),
        }
    status = payload["status"]
    expected = VALID_EXIT_CODES.get(status)
    if expected is None:
        return {
            "status": "error",
            "reason": _safe_reason(
                f"DDT rc={returncode}: unsupported status {status}"
            ),
        }
    if returncode != expected:
        reason = (
            _last_stderr_line(stderr)
            or _safe_reason(payload.get("reason"))
            or "exit/status mismatch"
        )
        return {
            "status": "error",
            "reason": _safe_reason(
                f"DDT rc={returncode}, expected={expected}: {reason}"
            ),
        }
    if payload.get("reason"):
        payload["reason"] = _safe_reason(payload["reason"])
    return payload


async def run_ddt_subprocess(
    target_date: date,
    provinces: Sequence[str],
    *,
    timeout_seconds: int = DDT_SUBPROCESS_TIMEOUT_SECONDS,
) -> tuple[dict, int]:
    """Execute the isolated DDT CLI and return its normalized payload/runtime."""
    script = Path(__file__).with_name("predict_xsmn_digit_transition.py")
    command = (
        sys.executable,
        str(script),
        "--date",
        target_date.isoformat(),
        "--provinces",
        ",".join(provinces),
    )
    started = time.perf_counter()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        runtime_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": "error",
            "reason": f"shadow timeout after {timeout_seconds}s",
        }, runtime_ms
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise
    runtime_ms = int((time.perf_counter() - started) * 1000)
    payload = parse_ddt_cli_contract(
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        int(process.returncode or 0),
    )
    return payload, runtime_ms


class DDTLocalController:
    """Own request authorization, one-shot callbacks, locking and persistence."""

    def __init__(
        self,
        db: LotteryDB,
        settings: DDTBotSettings,
    ) -> None:
        self.db = db
        self.settings = settings
        self.requests: dict[str, PendingDDTRequest] = {}
        self.run_lock = asyncio.Lock()
        self.reserved_token: Optional[str] = None

    def is_authorized(self, chat_id: int, user_id: int) -> bool:
        return (
            chat_id in self.settings.allowed_chat_ids
            and user_id in self.settings.allowed_user_ids
        )

    def create_request(
        self,
        target_date: date,
        *,
        chat_id: int,
        requested_by: Optional[int],
        now: Optional[datetime] = None,
    ) -> PendingDDTRequest:
        """Create an expiring approval after resolving exactly two provinces."""
        created_at = now or datetime.now(VN_TZ)
        self.requests = {
            token: request
            for token, request in self.requests.items()
            if not request.used and request.expires_at > created_at
        }
        provinces = tuple(XSMN_ENSEMBLE_SCHEDULE.get(target_date.weekday(), ()))
        if len(provinces) != 2 or len(set(provinces)) != 2:
            raise ValueError("DDT requires exactly two scheduled provinces")
        token = secrets.token_urlsafe(8)
        request = PendingDDTRequest(
            token=token,
            target_date=target_date,
            provinces=(provinces[0], provinces[1]),
            chat_id=chat_id,
            requested_by=requested_by,
            expires_at=created_at
            + timedelta(seconds=self.settings.request_ttl_seconds),
        )
        self.requests[token] = request
        return request

    def claim_request(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
        now: Optional[datetime] = None,
    ) -> tuple[Optional[PendingDDTRequest], str]:
        """Atomically consume one approval token without starting a second run."""
        if not self.is_authorized(chat_id, user_id):
            return None, "unauthorized"
        request = self.requests.get(token)
        if request is None:
            return None, "unknown_request"
        if request.used:
            return None, "already_used"
        current = now or datetime.now(VN_TZ)
        if request.chat_id != chat_id:
            return None, "wrong_chat"
        if request.requested_by is not None and request.requested_by != user_id:
            return None, "wrong_user"
        if current >= request.expires_at:
            return None, "expired"
        if self.reserved_token is not None or self.run_lock.locked():
            return None, "run_in_progress"
        request.used = True
        self.reserved_token = token
        return request, "approved"

    def release_request(self, token: str) -> None:
        """Release the atomic run reservation after its task finishes."""
        if self.reserved_token == token:
            self.reserved_token = None

    def cancel_request(
        self,
        token: str,
        *,
        chat_id: int,
        user_id: int,
    ) -> str:
        if not self.is_authorized(chat_id, user_id):
            return "unauthorized"
        request = self.requests.get(token)
        if request is None:
            return "unknown_request"
        if request.used:
            return "already_used"
        if request.chat_id != chat_id:
            return "wrong_chat"
        if request.requested_by is not None and request.requested_by != user_id:
            return "wrong_user"
        request.used = True
        return "cancelled"

    async def execute_request(self, request: PendingDDTRequest) -> str:
        """Run, persist and format one approved DDT result without leaking errors."""
        async with self.run_lock:
            started = time.perf_counter()
            try:
                payload, runtime_ms = await run_ddt_subprocess(
                    request.target_date,
                    request.provinces,
                    timeout_seconds=self.settings.subprocess_timeout_seconds,
                )
            except Exception as exc:
                runtime_ms = int((time.perf_counter() - started) * 1000)
                payload = {
                    "status": "error",
                    "reason": _safe_reason(exc),
                }
            config = DigitTransitionConfig()
            try:
                record = normalize_shadow_prediction(
                    payload,
                    model_name="ddt_shadow",
                    target_date=request.target_date,
                    provinces=request.provinces,
                    execution_source="local_telegram",
                    runtime_ms=runtime_ms,
                    config_metadata=asdict(config),
                )
            except Exception as exc:
                payload = {
                    "status": "error",
                    "reason": _safe_reason(f"normalization failed: {exc}"),
                }
                record = normalize_shadow_prediction(
                    payload,
                    model_name="ddt_shadow",
                    target_date=request.target_date,
                    provinces=request.provinces,
                    execution_source="local_telegram",
                    runtime_ms=runtime_ms,
                    config_metadata=asdict(config),
                )
            if record["status"] in SHADOW_SUCCESS_STATUSES:
                outcome_payload = {
                    "status": record["status"],
                    "score_semantics": record.get("score_semantics"),
                    "selected_evidence": [
                        {"pair": record[f"pair_{index}"]}
                        for index in range(1, 4)
                    ],
                }
            else:
                outcome_payload = {
                    "status": record["status"],
                    "reason": record.get("error_message") or "invalid_shadow_top_3",
                }
            preserved_success = False
            persistence_error: Optional[str] = None
            try:
                existing = get_shadow_prediction(
                    self.db,
                    "ddt_shadow",
                    request.target_date,
                )
                saved = save_shadow_prediction(self.db, record)
                preserved_success = bool(
                    not saved
                    and existing
                    and existing.get("status") in SHADOW_SUCCESS_STATUSES
                    and record.get("status") not in SHADOW_SUCCESS_STATUSES
                )
                if not saved and not preserved_success:
                    persistence_error = "không lưu được model_predictions"
            except Exception as exc:
                persistence_error = _safe_reason(exc, 160)
            return format_outcome_message(
                outcome_payload,
                request.target_date,
                request.provinces,
                runtime_ms,
                preserved_success=preserved_success,
                persistence_error=persistence_error,
            )


def format_outcome_message(
    payload: dict,
    target_date: date,
    provinces: Sequence[str],
    runtime_ms: int,
    *,
    preserved_success: bool = False,
    persistence_error: Optional[str] = None,
) -> str:
    """Build one compact outcome without raw traceback or credentials."""
    status = str(payload.get("status") or "error")
    lines = [
        f"🧪 <b>DDT local — {target_date:%d/%m/%Y}</b>",
        f"📍 <code>{escape(' + '.join(provinces))}</code>",
        f"⏱ {runtime_ms / 1000:.1f}s",
    ]
    if status in SHADOW_SUCCESS_STATUSES:
        pairs = [
            int(item["pair"])
            for item in payload.get("selected_evidence", [])
            if isinstance(item, dict) and item.get("pair") is not None
        ][:3]
        semantics = escape(str(payload.get("score_semantics") or "uncalibrated"))
        lines.append(
            "✅ Top 3: " + " | ".join(f"<code>{pair:02d}</code>" for pair in pairs)
        )
        lines.append(f"📐 {semantics}")
    elif status == "insufficient_evidence":
        lines.append(
            f"⏳ Chưa đủ dữ liệu: {escape(_safe_reason(payload.get('reason')))}"
        )
    else:
        lines.append(f"⚠️ Lỗi DDT: {escape(_safe_reason(payload.get('reason')))}")
    if preserved_success:
        lines.append("ℹ️ Giữ nguyên kết quả success đã lưu trước đó.")
    elif persistence_error:
        lines.append(f"⚠️ Lưu DB thất bại: {escape(persistence_error)}")
    return "\n".join(lines)


def _request_keyboard(request: PendingDDTRequest) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Đồng ý chạy",
                    callback_data=f"ddt:run:{request.token}",
                ),
                InlineKeyboardButton(
                    "Hủy",
                    callback_data=f"ddt:cancel:{request.token}",
                ),
            ]
        ]
    )


def _request_text(request: PendingDDTRequest) -> str:
    provinces = " + ".join(request.provinces)
    return (
        f"🧪 Chạy DDT local cho <b>{request.target_date:%d/%m/%Y}</b>?\n"
        f"📍 <code>{escape(provinces)}</code>\n"
        "⏳ Xác nhận trong "
        f"{math.ceil(max(0.0, (request.expires_at - datetime.now(VN_TZ)).total_seconds()) / 60)} phút."
    )


async def _ddt_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    controller: DDTLocalController = context.application.bot_data["ddt_controller"]
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if not controller.is_authorized(chat.id, user.id):
        await message.reply_text("Không được phép.")
        return
    if controller.reserved_token is not None or controller.run_lock.locked():
        await message.reply_text("DDT đang chạy; hãy thử lại sau.")
        return
    try:
        if len(context.args) > 1:
            raise ValueError("Dùng /ddt hoặc /ddt YYYY-MM-DD")
        target_date = (
            date.fromisoformat(context.args[0])
            if context.args
            else resolve_operational_date(datetime.now(VN_TZ))
        )
        request = controller.create_request(
            target_date,
            chat_id=chat.id,
            requested_by=user.id,
        )
    except ValueError as exc:
        await message.reply_text(_safe_reason(exc))
        return
    await message.reply_text(
        _request_text(request),
        parse_mode="HTML",
        reply_markup=_request_keyboard(request),
    )


async def _ddt_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if query is None or query.message is None or query.from_user is None:
        return
    controller: DDTLocalController = context.application.bot_data["ddt_controller"]
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "ddt":
        await query.answer("Callback không hợp lệ.", show_alert=True)
        return
    action, token = parts[1], parts[2]
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    if action == "cancel":
        outcome = controller.cancel_request(
            token,
            chat_id=chat_id,
            user_id=user_id,
        )
        await query.answer("Đã hủy." if outcome == "cancelled" else outcome)
        if outcome == "cancelled":
            await query.edit_message_text("Đã hủy yêu cầu DDT.")
        return
    if action != "run":
        await query.answer("Callback không hợp lệ.", show_alert=True)
        return
    request, outcome = controller.claim_request(
        token,
        chat_id=chat_id,
        user_id=user_id,
    )
    if request is None:
        await query.answer(outcome, show_alert=True)
        return

    async def run_and_notify() -> None:
        result_message = await controller.execute_request(request)
        try:
            await context.bot.send_message(
                chat_id=request.chat_id,
                text=result_message,
                parse_mode="HTML",
            )
        except Exception:
            try:
                await query.edit_message_text(
                    result_message,
                    parse_mode="HTML",
                )
            except Exception as exc:
                print(f"⚠️ DDT outcome delivery failed: {_safe_reason(exc)}")

    task = context.application.create_task(
        run_and_notify(),
        name=f"ddt-{request.target_date.isoformat()}",
    )
    task.add_done_callback(lambda _task: controller.release_request(request.token))
    await query.answer("Đã nhận; DDT đang chạy nền.")
    await query.edit_message_text(
        f"⏳ DDT {request.target_date:%d/%m/%Y} đang chạy…"
    )


async def _send_daily_prompts(
    application: Application,
    target_date: date,
) -> None:
    """Send one prompt per configured chat while keeping scheduler failures local."""
    controller: DDTLocalController = application.bot_data["ddt_controller"]
    try:
        existing = get_shadow_prediction(controller.db, "ddt_shadow", target_date)
    except Exception:
        existing = None
    if existing and existing.get("status") in SHADOW_SUCCESS_STATUSES:
        return
    for chat_id in sorted(controller.settings.allowed_chat_ids):
        if controller.reserved_token is not None or controller.run_lock.locked():
            continue
        try:
            request = controller.create_request(
                target_date,
                chat_id=chat_id,
                requested_by=None,
            )
            await application.bot.send_message(
                chat_id=chat_id,
                text=_request_text(request),
                parse_mode="HTML",
                reply_markup=_request_keyboard(request),
            )
        except Exception as exc:
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ Không tạo được prompt DDT: "
                        f"{escape(_safe_reason(exc))}"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                continue


async def _daily_prompt_loop(application: Application) -> None:
    controller: DDTLocalController = application.bot_data["ddt_controller"]
    prompt_time = controller.settings.prompt_time
    now = datetime.now(VN_TZ)
    scheduled_today = datetime.combine(now.date(), prompt_time, tzinfo=VN_TZ)
    if now >= scheduled_today:
        await _send_daily_prompts(
            application,
            resolve_operational_date(now),
        )
    while True:
        now = datetime.now(VN_TZ)
        scheduled = datetime.combine(now.date(), prompt_time, tzinfo=VN_TZ)
        if scheduled <= now:
            scheduled += timedelta(days=1)
        await asyncio.sleep(max(1.0, (scheduled - now).total_seconds()))
        await _send_daily_prompts(
            application,
            resolve_operational_date(datetime.now(VN_TZ)),
        )


async def _post_init(application: Application) -> None:
    application.bot_data["ddt_prompt_task"] = asyncio.create_task(
        _daily_prompt_loop(application),
        name="ddt-daily-prompt",
    )


async def _post_shutdown(application: Application) -> None:
    task = application.bot_data.get("ddt_prompt_task")
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def build_application(
    db: LotteryDB,
    settings: DDTBotSettings,
) -> Application:
    """Build the long-polling app without opening a webhook or public port."""
    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["ddt_controller"] = DDTLocalController(db, settings)
    application.add_handler(CommandHandler("ddt", _ddt_command))
    application.add_handler(CallbackQueryHandler(_ddt_callback, pattern=r"^ddt:"))
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Telegram DDT controller")
    parser.parse_args()
    settings = load_settings()
    application = build_application(LotteryDB(), settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
