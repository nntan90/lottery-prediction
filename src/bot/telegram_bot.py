"""
Telegram Bot - Gửi notifications về predictions
"""

import os
import asyncio
from telegram import Bot
from telegram.error import TelegramError
from typing import Dict, Optional
from datetime import date


class LotteryNotifier:
    """Telegram bot để gửi thông báo dự đoán"""
    
    def __init__(self):
        """Initialize bot với token từ environment"""
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not bot_token or not self.chat_id:
            print("⚠️ Missing Telegram credentials. Notifications will be disabled (Mock Mode).")
            self.bot = None
            return
            
        self.bot = Bot(token=bot_token)
        print(f"✅ Telegram bot initialized")
    
    async def send_error_alert(self, error_message: str) -> bool:
        """
        Gửi thông báo lỗi
        
        Args:
            error_message: Nội dung lỗi
        
        Returns:
            True nếu gửi thành công
        """
        try:
            message = f"⚠️ *System Alert*\n\n{error_message}"
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
            return True
            
        except Exception as e:
            print(f"❌ Error sending alert: {e}")
            return False


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    pass
