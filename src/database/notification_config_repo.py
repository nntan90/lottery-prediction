"""
notification_config_repo.py — DB-backed notification switches.

Scripts use stable config_key values to decide whether Telegram should send and
which delivery overrides apply. Missing table/config falls back to enabled
defaults so old deployments keep working until migration 07 is applied.
"""

from __future__ import annotations

from typing import Any

from src.database.supabase_client import LotteryDB


DEFAULT_NOTIFICATION_CONFIG: dict[str, Any] = {
    "channel": "telegram",
    "enabled": True,
    "chat_id": None,
    "parse_mode": "HTML",
    "message_prefix": "",
    "message_suffix": "",
}

_warned_missing_table = False


def get_notification_config(
    db: LotteryDB | None,
    config_key: str | None,
) -> dict[str, Any]:
    """Return notification config merged with safe defaults.

    Args:
        db: LotteryDB instance. If None, defaults are returned.
        config_key: Stable notification key such as ``predict_ensemble_xsmb``.
    """
    config = DEFAULT_NOTIFICATION_CONFIG.copy()
    if not config_key:
        return config
    config["config_key"] = config_key

    if db is None:
        return config

    global _warned_missing_table
    try:
        rows = (
            db.supabase.table("notification_configs")
            .select("*")
            .eq("config_key", config_key)
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:
        error = str(exc)
        if (
            not _warned_missing_table
            and ("notification_configs" in error or "PGRST205" in error)
        ):
            print("⚠️ notification_configs table missing; using env/default Telegram config.")
            _warned_missing_table = True
        return config

    if not rows:
        return config

    row = rows[0]
    for key in DEFAULT_NOTIFICATION_CONFIG:
        if row.get(key) is not None:
            config[key] = row[key]

    return config


def apply_message_overrides(message: str, config: dict[str, Any]) -> str:
    """Apply optional prefix/suffix from notification config."""
    prefix = config.get("message_prefix") or ""
    suffix = config.get("message_suffix") or ""
    return f"{prefix}{message}{suffix}"
