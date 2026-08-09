"""Local Telegram long-polling controller for the XSMN DDT shadow model."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as clock_time, timedelta
from html import escape
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time
from typing import Awaitable, Callable, Optional, Sequence
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
from src.xsmn_digit_transition.config import DigitTransitionConfig
from src.xsmn_ensemble.resolve_provinces import XSMN_ENSEMBLE_SCHEDULE


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
DDT_SUBPROCESS_TIMEOUT_SECONDS = 120
# These operational boundaries are human-approved and intentionally fixed.
DEFAULT_PROMPT_TIME = "21:00"
APPROVAL_OPEN_TIME = clock_time(21, 0, tzinfo=VN_TZ)
APPROVAL_CLOSE_TIME = clock_time(12, 0, tzinfo=VN_TZ)
POWER_GUARD_TIME = clock_time(20, 55, tzinfo=VN_TZ)
DEFAULT_REQUEST_TTL_SECONDS = 15 * 60
CAFFEINATE_PATH = "/usr/bin/caffeinate"
CAFFEINATE_STOP_TIMEOUT_SECONDS = 2.0
AWAKE_HEALTHCHECK_SECONDS = 60.0
PROMPT_RETRY_SECONDS = 60.0
WORKER_RESTART_SECONDS = 5.0
SCHEDULER_MAX_SLEEP_SECONDS = 15.0
ASYNC_DB_TIMEOUT_SECONDS = 5.0
VALID_EXIT_CODES = {
    "success": 0,
    "uncalibrated": 0,
    "insufficient_evidence": 2,
    "error": 3,
}


def _log_event(
    level: str,
    event: str,
    message: str,
    **fields: object,
) -> None:
    """Write one timestamped, redacted JSON event to the launchd log."""
    try:
        payload: dict[str, object] = {
            "timestamp": datetime.now(VN_TZ).isoformat(),
            "level": level.upper(),
            "event": event,
            "message": _safe_reason(message),
        }
        for key, value in fields.items():
            if value is None or isinstance(value, (bool, int, float)):
                payload[key] = value
            else:
                payload[key] = _safe_reason(value)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    except Exception:
        # Diagnostics must never become a second scheduler failure.
        return


def _safe_reason(value: object, limit: int = 240) -> str:
    """Return a compact redacted reason suitable for persistence/Telegram."""
    text = " ".join(str(value).split()) or "ddt_execution_failed"
    text = re.sub(r"https?://\S+", "[redacted-url]", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer\s+)?"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        "authorization=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\bbearer\b\s+(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        "bearer [redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\b("
        r"token|access_token|refresh_token|apikey|api_key|x-api-key|"
        r"password|passwd|client_secret|access_key|service_key"
        r")\b\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
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


def _as_vietnam_time(value: datetime) -> datetime:
    """Normalize an aware datetime to the one operational timezone."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("DDT scheduling requires a timezone-aware datetime")
    return value.astimezone(VN_TZ)


def approval_window(target_date: date) -> tuple[datetime, datetime]:
    """Return the approval interval [21:00 D-1, 12:00 D) in Vietnam."""
    if target_date == date.min:
        raise ValueError("DDT target date is out of supported range")
    opens_at = datetime.combine(
        target_date - timedelta(days=1),
        APPROVAL_OPEN_TIME,
        tzinfo=VN_TZ,
    )
    closes_at = datetime.combine(
        target_date,
        APPROVAL_CLOSE_TIME,
        tzinfo=VN_TZ,
    )
    return opens_at, closes_at


def power_guard_window(target_date: date) -> tuple[datetime, datetime]:
    """Return the awake interval [20:55 D-1, 12:00 D) in Vietnam."""
    starts_at = datetime.combine(
        target_date - timedelta(days=1),
        POWER_GUARD_TIME,
        tzinfo=VN_TZ,
    )
    return starts_at, approval_window(target_date)[1]


def active_approval_target(value: datetime) -> Optional[date]:
    """Resolve D only while ``value`` is inside D's approval window."""
    current = _as_vietnam_time(value)
    if current.timetz().replace(tzinfo=None) < APPROVAL_CLOSE_TIME.replace(
        tzinfo=None
    ):
        candidate = current.date()
    elif current.timetz().replace(tzinfo=None) >= APPROVAL_OPEN_TIME.replace(
        tzinfo=None
    ):
        candidate = current.date() + timedelta(days=1)
    else:
        return None
    opens_at, closes_at = approval_window(candidate)
    return candidate if opens_at <= current < closes_at else None


def active_power_guard_target(value: datetime) -> Optional[date]:
    """Resolve D only while ``value`` is inside D's power guard window."""
    current = _as_vietnam_time(value)
    current_time = current.timetz().replace(tzinfo=None)
    if current_time < APPROVAL_CLOSE_TIME.replace(tzinfo=None):
        candidate = current.date()
    elif current_time >= POWER_GUARD_TIME.replace(tzinfo=None):
        candidate = current.date() + timedelta(days=1)
    else:
        return None
    starts_at, closes_at = power_guard_window(candidate)
    return candidate if starts_at <= current < closes_at else None


def next_prompt_at(value: datetime) -> datetime:
    """Return the next strictly-future 21:00 prompt boundary."""
    current = _as_vietnam_time(value)
    scheduled = datetime.combine(
        current.date(),
        APPROVAL_OPEN_TIME,
        tzinfo=VN_TZ,
    )
    return scheduled if current < scheduled else scheduled + timedelta(days=1)


def next_power_guard_at(value: datetime) -> datetime:
    """Return the next strictly-future 20:55 power-guard boundary."""
    current = _as_vietnam_time(value)
    scheduled = datetime.combine(
        current.date(),
        POWER_GUARD_TIME,
        tzinfo=VN_TZ,
    )
    return scheduled if current < scheduled else scheduled + timedelta(days=1)


async def _wait_until_wall_clock(deadline: datetime) -> None:
    """Wait for one fixed wall-clock deadline via suspend-safe short sleeps.

    Python's macOS asyncio timer is monotonic and can pause while the machine
    sleeps. Re-reading Vietnam wall time at most every 15 seconds catches up
    promptly after resume. The caller computes ``deadline`` once so a logical
    60-second retry or health-check is not restarted on every short sleep.
    """
    fixed_deadline = _as_vietnam_time(deadline)
    while True:
        remaining = (fixed_deadline - datetime.now(VN_TZ)).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(SCHEDULER_MAX_SLEEP_SECONDS, remaining))


@dataclass(frozen=True)
class DDTBotSettings:
    """Environment-owned settings for the local polling process."""

    bot_token: str
    allowed_chat_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    prompt_time: clock_time
    # Retained for constructor compatibility; approvals now close at 12:00 D.
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
    timeout = int(
        os.getenv(
            "DDT_LOCAL_TIMEOUT_SECONDS",
            DDT_SUBPROCESS_TIMEOUT_SECONDS,
        )
    )
    if timeout < 1:
        raise ValueError("DDT_LOCAL_TIMEOUT_SECONDS must be positive")
    return DDTBotSettings(
        bot_token=token,
        allowed_chat_ids=parsed_chat_ids,
        allowed_user_ids=_parse_id_set(user_ids, name="DDT_ALLOWED_USER_IDS"),
        # The approved window is fixed; legacy overrides remain harmless.
        prompt_time=_parse_prompt_time(DEFAULT_PROMPT_TIME),
        request_ttl_seconds=DEFAULT_REQUEST_TTL_SECONDS,
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


@dataclass
class PromptDeliveryState:
    """Remember delivered chats across prompt-worker restarts."""

    target_date: Optional[date] = None
    delivered_chats: set[int] = field(default_factory=set)
    failure_notified_chats: set[int] = field(default_factory=set)


class AwakeLeaseManager:
    """Own one non-orphaning caffeinate process for all active awake leases."""

    def __init__(self) -> None:
        self._leases: set[str] = set()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._lock = asyncio.Lock()

    @property
    def leases(self) -> frozenset[str]:
        return frozenset(self._leases)

    @property
    def process(self) -> Optional[asyncio.subprocess.Process]:
        return self._process

    async def acquire(self, lease: str) -> None:
        """Acquire an awake reason and start caffeinate at most once."""
        async with self._lock:
            self._leases.add(lease)
            if self._process is not None and self._process.returncode is None:
                return
            if self._process is not None:
                await self._process.wait()
            self._process = None
            try:
                self._process = await asyncio.create_subprocess_exec(
                    CAFFEINATE_PATH,
                    "-i",
                    "-w",
                    str(os.getpid()),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except Exception as exc:
                _log_event(
                    "WARNING",
                    "ddt_caffeinate_unavailable",
                    "DDT caffeinate unavailable",
                    error=exc,
                )

    async def release(self, lease: str) -> None:
        """Release one reason and reap caffeinate after the final lease."""
        async with self._lock:
            self._leases.discard(lease)
            if self._leases:
                return
            await self._stop_process()

    async def shutdown(self) -> None:
        """Clear all reasons and reap caffeinate during application shutdown."""
        async with self._lock:
            self._leases.clear()
            await self._stop_process()

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            if process is not None:
                await process.wait()
            return
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=CAFFEINATE_STOP_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


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
        created_at = _as_vietnam_time(now or datetime.now(VN_TZ))
        opens_at, closes_at = approval_window(target_date)
        if not opens_at <= created_at < closes_at:
            raise ValueError(_outside_window_message(created_at))
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
            expires_at=closes_at,
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
        current = _as_vietnam_time(now or datetime.now(VN_TZ))
        if request.chat_id != chat_id:
            return None, "wrong_chat"
        if request.requested_by is not None and request.requested_by != user_id:
            return None, "wrong_user"
        opens_at, closes_at = approval_window(request.target_date)
        if current < opens_at:
            return None, "not_open"
        if current >= closes_at or current >= request.expires_at:
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


async def _has_persisted_success(
    controller: DDTLocalController,
    target_date: date,
) -> bool:
    """Read durable success without blocking Telegram's asyncio event loop."""
    try:
        existing = await asyncio.wait_for(
            asyncio.to_thread(
                get_shadow_prediction,
                controller.db,
                "ddt_shadow",
                target_date,
            ),
            timeout=ASYNC_DB_TIMEOUT_SECONDS,
        )
    except Exception:
        return False
    return bool(existing and existing.get("status") in SHADOW_SUCCESS_STATUSES)


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
    elif status in {"insufficient", "insufficient_evidence"}:
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
        f"⏳ Xác nhận trước <b>{request.expires_at:%H:%M %d/%m/%Y}</b>."
    )


def _outside_window_message(value: datetime) -> str:
    next_open = next_prompt_at(value)
    return (
        "DDT chỉ nhận xác nhận từ 21:00 hôm trước đến trước 12:00 ngày quay. "
        f"Khung tiếp theo mở lúc {next_open:%H:%M %d/%m/%Y}."
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
        now = datetime.now(VN_TZ)
        if len(context.args) > 1:
            raise ValueError("Dùng /ddt hoặc /ddt YYYY-MM-DD")
        if context.args:
            target_date = date.fromisoformat(context.args[0])
        else:
            target_date = active_approval_target(now)
            if target_date is None:
                raise ValueError(_outside_window_message(now))
        request = controller.create_request(
            target_date,
            chat_id=chat.id,
            requested_by=user.id,
            now=now,
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
    request_hint = controller.requests.get(token)
    if (
        controller.is_authorized(chat_id, user_id)
        and request_hint is not None
        and request_hint.chat_id == chat_id
        and (
            request_hint.requested_by is None
            or request_hint.requested_by == user_id
        )
        and await _has_persisted_success(controller, request_hint.target_date)
    ):
        await query.answer(
            "DDT ngày này đã có kết quả thành công.",
            show_alert=True,
        )
        return
    request, outcome = controller.claim_request(
        token,
        chat_id=chat_id,
        user_id=user_id,
    )
    if request is None:
        await query.answer(outcome, show_alert=True)
        return

    awake_leases: AwakeLeaseManager = context.application.bot_data[
        "ddt_awake_leases"
    ]
    run_lease = f"run:{request.token}"
    try:
        await awake_leases.acquire(run_lease)
    except BaseException:
        controller.release_request(request.token)
        await awake_leases.release(run_lease)
        raise

    async def run_and_notify() -> None:
        try:
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
                    _log_event(
                        "ERROR",
                        "ddt_outcome_delivery_failed",
                        "DDT outcome delivery failed",
                        target_date=request.target_date,
                        chat_id=request.chat_id,
                        error=exc,
                    )
        finally:
            controller.release_request(request.token)
            if await _has_persisted_success(controller, request.target_date):
                await awake_leases.release("approval_window")
            await awake_leases.release(run_lease)

    try:
        try:
            await query.answer("Đã nhận; DDT đang chạy nền.")
        except Exception as exc:
            _log_event(
                "WARNING",
                "ddt_callback_acknowledgement_failed",
                "DDT callback acknowledgement failed",
                target_date=request.target_date,
                chat_id=request.chat_id,
                error=exc,
            )
        try:
            await query.edit_message_text(
                f"⏳ DDT {request.target_date:%d/%m/%Y} đang chạy…"
            )
        except Exception as exc:
            _log_event(
                "WARNING",
                "ddt_running_message_failed",
                "DDT running message failed",
                target_date=request.target_date,
                chat_id=request.chat_id,
                error=exc,
            )
        context.application.create_task(
            run_and_notify(),
            name=f"ddt-{request.target_date.isoformat()}",
        )
    except BaseException:
        controller.release_request(request.token)
        await awake_leases.release(run_lease)
        raise


async def _send_daily_prompts(
    application: Application,
    target_date: date,
    chat_ids: Optional[Sequence[int]] = None,
) -> set[int]:
    """Send one prompt per configured chat while keeping scheduler failures local."""
    controller: DDTLocalController = application.bot_data["ddt_controller"]
    recipients = set(chat_ids or controller.settings.allowed_chat_ids)
    if await _has_persisted_success(controller, target_date):
        return recipients
    delivered: set[int] = set()
    for chat_id in sorted(recipients):
        if controller.reserved_token is not None or controller.run_lock.locked():
            continue
        request: Optional[PendingDDTRequest] = None
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
            delivered.add(chat_id)
            state = application.bot_data.get("ddt_prompt_delivery_state")
            if isinstance(state, PromptDeliveryState):
                state.delivered_chats.add(chat_id)
                state.failure_notified_chats.discard(chat_id)
            _log_event(
                "INFO",
                "ddt_prompt_delivered",
                "DDT prompt delivered",
                target_date=target_date,
                chat_id=chat_id,
            )
        except Exception as exc:
            if request is not None:
                controller.release_request(request.token)
                controller.requests.pop(request.token, None)
            _log_event(
                "WARNING",
                "ddt_prompt_delivery_failed",
                "DDT prompt delivery failed",
                target_date=target_date,
                chat_id=chat_id,
                error=exc,
            )
            state = application.bot_data.get("ddt_prompt_delivery_state")
            if (
                isinstance(state, PromptDeliveryState)
                and chat_id in state.failure_notified_chats
            ):
                continue
            try:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ Không tạo được prompt DDT: "
                        f"{escape(_safe_reason(exc))}"
                    ),
                    parse_mode="HTML",
                )
                if isinstance(state, PromptDeliveryState):
                    state.failure_notified_chats.add(chat_id)
                _log_event(
                    "INFO",
                    "ddt_prompt_fallback_delivered",
                    "DDT prompt fallback delivered",
                    target_date=target_date,
                    chat_id=chat_id,
                )
            except Exception as fallback_exc:
                _log_event(
                    "ERROR",
                    "ddt_prompt_fallback_delivery_failed",
                    "DDT prompt fallback delivery failed",
                    target_date=target_date,
                    chat_id=chat_id,
                    error=fallback_exc,
                )
                continue
    return delivered


async def _daily_prompt_loop(application: Application) -> None:
    """Send at 21:00 or once on a wake/restart inside the active window."""
    controller: DDTLocalController = application.bot_data["ddt_controller"]
    state = application.bot_data.setdefault(
        "ddt_prompt_delivery_state",
        PromptDeliveryState(),
    )
    while True:
        now = datetime.now(VN_TZ)
        target_date = active_approval_target(now)
        if target_date is None:
            scheduled = next_prompt_at(now)
            await _wait_until_wall_clock(scheduled)
            continue
        if target_date != state.target_date:
            state.target_date = target_date
            state.delivered_chats.clear()
            state.failure_notified_chats.clear()
        missing = (
            set(controller.settings.allowed_chat_ids) - state.delivered_chats
        )
        if missing:
            state.delivered_chats.update(
                await _send_daily_prompts(
                    application,
                    target_date,
                    sorted(missing),
                )
            )
        if state.delivered_chats >= set(controller.settings.allowed_chat_ids):
            schedule_now = datetime.now(VN_TZ)
            scheduled = next_prompt_at(schedule_now)
            await _wait_until_wall_clock(scheduled)
            continue
        closes_at = approval_window(target_date)[1]
        retry_now = datetime.now(VN_TZ)
        retry_deadline = min(
            closes_at,
            retry_now + timedelta(seconds=PROMPT_RETRY_SECONDS),
        )
        await _wait_until_wall_clock(retry_deadline)


async def _awake_guard_loop(application: Application) -> None:
    """Hold the approval lease from the 20:55 wake through 12:00."""
    awake_leases: AwakeLeaseManager = application.bot_data["ddt_awake_leases"]
    controller: DDTLocalController = application.bot_data["ddt_controller"]
    lease = "approval_window"
    lease_active = False
    try:
        while True:
            now = datetime.now(VN_TZ)
            target_date = active_power_guard_target(now)
            needs_approval = (
                target_date is not None
                and not await _has_persisted_success(controller, target_date)
            )
            if needs_approval:
                await awake_leases.acquire(lease)
                lease_active = True
                assert target_date is not None
                closes_at = power_guard_window(target_date)[1]
                healthcheck_deadline = min(
                    closes_at,
                    now + timedelta(seconds=AWAKE_HEALTHCHECK_SECONDS),
                )
                await _wait_until_wall_clock(healthcheck_deadline)
                continue
            if lease_active:
                await awake_leases.release(lease)
                lease_active = False
            starts_at = next_power_guard_at(now)
            await _wait_until_wall_clock(starts_at)
    finally:
        if lease_active:
            await awake_leases.release(lease)


WorkerFactory = Callable[[Application], Awaitable[None]]


async def _supervise_worker(
    application: Application,
    worker_name: str,
    worker_factory: WorkerFactory,
) -> None:
    """Restart one scheduler worker after bounded delay until shutdown."""
    worker_tasks = application.bot_data.setdefault("ddt_worker_tasks", {})
    while True:
        worker_task = asyncio.create_task(
            worker_factory(application),
            name=f"ddt-worker-{worker_name}",
        )
        worker_tasks[worker_name] = worker_task
        try:
            await asyncio.wait({worker_task})
        except asyncio.CancelledError:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            raise
        finally:
            if worker_tasks.get(worker_name) is worker_task:
                worker_tasks.pop(worker_name, None)

        if worker_task.cancelled():
            detail = "worker cancelled outside application shutdown"
        else:
            exception = worker_task.exception()
            detail = (
                f"worker raised: {_safe_reason(exception)}"
                if exception is not None
                else "worker returned unexpectedly"
            )
        _log_event(
            "ERROR",
            "ddt_scheduler_worker_restarting",
            "DDT scheduler worker stopped; restarting",
            worker=worker_name,
            detail=detail,
            retry_seconds=WORKER_RESTART_SECONDS,
        )
        await asyncio.sleep(WORKER_RESTART_SECONDS)


def _supervisor_done(
    application: Application,
    supervisor_name: str,
    task: asyncio.Task,
) -> None:
    """Stop run_polling when a scheduler supervisor dies unexpectedly."""
    if application.bot_data.get("ddt_shutting_down"):
        return
    if application.bot_data.get("ddt_fatal_stop_requested"):
        return
    if task.cancelled():
        detail = "supervisor cancelled"
    else:
        exception = task.exception()
        detail = (
            f"supervisor raised: {_safe_reason(exception)}"
            if exception is not None
            else "supervisor returned unexpectedly"
        )
    application.bot_data["ddt_fatal_stop_requested"] = True
    _log_event(
        "CRITICAL",
        "ddt_scheduler_supervisor_stopped",
        "DDT scheduler supervisor stopped; terminating application",
        supervisor=supervisor_name,
        detail=detail,
    )
    if getattr(application, "running", False):
        application.stop_running()
    else:
        asyncio.get_running_loop().stop()


async def _post_init(application: Application) -> None:
    application.bot_data["ddt_shutting_down"] = False
    application.bot_data["ddt_fatal_stop_requested"] = False
    application.bot_data.setdefault(
        "ddt_prompt_delivery_state",
        PromptDeliveryState(),
    )
    supervisors = (
        ("prompt", "ddt_prompt_task", _daily_prompt_loop),
        ("awake", "ddt_awake_task", _awake_guard_loop),
    )
    for supervisor_name, storage_key, worker_factory in supervisors:
        task = asyncio.create_task(
            _supervise_worker(
                application,
                supervisor_name,
                worker_factory,
            ),
            name=f"ddt-supervisor-{supervisor_name}",
        )
        task.add_done_callback(
            lambda completed, name=supervisor_name: _supervisor_done(
                application,
                name,
                completed,
            )
        )
        application.bot_data[storage_key] = task


async def _post_shutdown(application: Application) -> None:
    application.bot_data["ddt_shutting_down"] = True
    tasks = [
        application.bot_data.get("ddt_prompt_task"),
        application.bot_data.get("ddt_awake_task"),
    ]
    active_tasks = [task for task in tasks if task is not None]
    for task in active_tasks:
        task.cancel()
    if active_tasks:
        await asyncio.gather(*active_tasks, return_exceptions=True)
    worker_tasks = list(
        application.bot_data.get("ddt_worker_tasks", {}).values()
    )
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    awake_leases: Optional[AwakeLeaseManager] = application.bot_data.get(
        "ddt_awake_leases"
    )
    if awake_leases is not None:
        await awake_leases.shutdown()


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
    application.bot_data["ddt_awake_leases"] = AwakeLeaseManager()
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
