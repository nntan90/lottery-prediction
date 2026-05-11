"""
Regression tests for DB-backed Telegram notification config.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.bot.telegram_bot import LotteryNotifier
from src.database.notification_config_repo import get_notification_config


class MockResult:
    def __init__(self, data):
        self.data = data


class MockQuery:
    def __init__(self, data=None, error=None):
        self.data = data or []
        self.error = error

    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self

    def execute(self):
        if self.error:
            raise self.error
        return MockResult(self.data)


class MockSupabase:
    def __init__(self, data=None, error=None):
        self.data = data or []
        self.error = error

    def table(self, name):
        self.table_name = name
        return MockQuery(self.data, self.error)


class MockDB:
    def __init__(self, data=None, error=None):
        self.supabase = MockSupabase(data, error)


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)


class TestNotificationConfigRepo(unittest.TestCase):
    def test_merges_db_config_with_defaults(self):
        db = MockDB([{
            "config_key": "predict_ensemble_xsmb",
            "enabled": False,
            "chat_id": "override-chat",
            "parse_mode": "Markdown",
            "message_prefix": "[P] ",
            "message_suffix": " [S]",
        }])

        config = get_notification_config(db, "predict_ensemble_xsmb")

        self.assertFalse(config["enabled"])
        self.assertEqual(config["chat_id"], "override-chat")
        self.assertEqual(config["parse_mode"], "Markdown")
        self.assertEqual(config["message_prefix"], "[P] ")
        self.assertEqual(config["message_suffix"], " [S]")

    def test_missing_table_falls_back_to_enabled_defaults(self):
        db = MockDB(error=Exception("relation notification_configs does not exist PGRST205"))
        config = get_notification_config(db, "verify_summary")
        self.assertTrue(config["enabled"])
        self.assertEqual(config["parse_mode"], "HTML")


class TestLotteryNotifier(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_config_skips_bot_send(self):
        db = MockDB([{"config_key": "health_digest", "enabled": False}])
        with patch.dict(os.environ, {}, clear=True):
            notifier = LotteryNotifier(db, default_config_key="health_digest")
        notifier.bot = FakeBot()
        notifier.chat_id = "default-chat"

        ok = await notifier.send_message("hello")

        self.assertTrue(ok)
        self.assertEqual(notifier.bot.calls, [])

    async def test_config_overrides_chat_and_message_format(self):
        db = MockDB([{
            "config_key": "weekly_report",
            "enabled": True,
            "chat_id": "weekly-chat",
            "parse_mode": "HTML",
            "message_prefix": "[prefix]",
            "message_suffix": "[suffix]",
        }])
        with patch.dict(os.environ, {}, clear=True):
            notifier = LotteryNotifier(db, default_config_key="weekly_report")
        notifier.bot = FakeBot()
        notifier.chat_id = "default-chat"

        ok = await notifier.send_message("payload")

        self.assertTrue(ok)
        self.assertEqual(notifier.bot.calls[0]["chat_id"], "weekly-chat")
        self.assertEqual(notifier.bot.calls[0]["text"], "[prefix]payload[suffix]")
        self.assertEqual(notifier.bot.calls[0]["parse_mode"], "HTML")

    async def test_explicit_parse_mode_wins_for_error_alerts(self):
        db = MockDB([{
            "config_key": "train_model",
            "enabled": True,
            "chat_id": "train-chat",
            "parse_mode": "HTML",
        }])
        with patch.dict(os.environ, {}, clear=True):
            notifier = LotteryNotifier(db, default_config_key="train_model")
        notifier.bot = FakeBot()

        ok = await notifier.send_error_alert("boom")

        self.assertTrue(ok)
        self.assertEqual(notifier.bot.calls[0]["parse_mode"], "Markdown")


if __name__ == "__main__":
    unittest.main()
