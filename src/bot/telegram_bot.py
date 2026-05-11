"""
Telegram Bot - Gửi notifications về predictions
"""

import os
from telegram import Bot
from telegram.error import TelegramError

from src.database.notification_config_repo import (
    apply_message_overrides,
    get_notification_config,
)


class LotteryNotifier:
    """Telegram bot để gửi thông báo dự đoán"""

    def __init__(self, db=None, default_config_key: str | None = None):
        """Initialize bot với token từ environment"""
        self.db = db
        self.default_config_key = default_config_key
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not bot_token:
            print("⚠️ Missing TELEGRAM_BOT_TOKEN. Notifications will be disabled (Mock Mode).")
            self.bot = None
            return

        self.bot = Bot(token=bot_token)
        if not self.chat_id:
            print("⚠️ Missing TELEGRAM_CHAT_ID. DB notification config may provide chat_id.")
        print(f"✅ Telegram bot initialized")

    async def send_message(
        self,
        message: str,
        parse_mode: str | None = None,
        config_key: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        """
        Gửi custom message qua Telegram
        
        Args:
            message: Nội dung message (hỗ trợ HTML hoặc Markdown)
            parse_mode: 'HTML' hoặc 'Markdown'
        
        Returns:
            True nếu gửi thành công
        """
        cfg_key = config_key or self.default_config_key
        config = get_notification_config(self.db, cfg_key)
        if not config.get("enabled", True):
            print(f"🔕 Telegram skipped by notification_configs: {cfg_key or 'default'}")
            return True

        final_message = apply_message_overrides(message, config)
        final_parse_mode = parse_mode or config.get("parse_mode") or "HTML"
        final_chat_id = chat_id or config.get("chat_id") or self.chat_id

        if not self.bot or not final_chat_id:
            print(f"[MOCK] Sending Message: {message[:100]}...")
            return True

        try:
            await self.bot.send_message(
                chat_id=final_chat_id,
                text=final_message,
                parse_mode=final_parse_mode
            )
            
            return True
            
        except TelegramError as e:
            print(f"❌ Telegram error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error sending message: {e}")
            return False
    
    async def send_error_alert(self, error_message: str, config_key: str | None = None) -> bool:
        """
        Gửi thông báo lỗi
        
        Args:
            error_message: Nội dung lỗi
        
        Returns:
            True nếu gửi thành công
        """
        message = f"⚠️ *System Alert*\n\n{error_message}"
        return await self.send_message(
            message,
            parse_mode='Markdown',
            config_key=config_key or self.default_config_key or "system_error_alert",
        )


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    pass
