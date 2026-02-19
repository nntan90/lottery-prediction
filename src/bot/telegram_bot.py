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
    
    async def send_prediction(self, prediction_data: Dict) -> bool:
        """
        Gửi dự đoán qua Telegram
        
        Args:
            prediction_data: Dictionary từ database
                {
                    'prediction_date': '2024-01-15',
                    'region': 'XSMB',
                    'predicted_numbers': {...},
                    'confidence_score': 0.3,
                    ...
                }
        
        Returns:
            True nếu gửi thành công, False nếu failed
        """
        if not self.bot:
            print(f"[MOCK] Sending Prediction: {prediction_data}")
            return True

        try:
            message = self._format_prediction_message(prediction_data)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
            print(f"✅ Prediction sent to Telegram")
            return True
            
        except TelegramError as e:
            print(f"❌ Telegram error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error sending prediction: {e}")
            return False
    
    async def send_message(self, message: str, parse_mode: str = 'HTML') -> bool:
        """
        Gửi custom message qua Telegram
        
        Args:
            message: Nội dung message (hỗ trợ HTML hoặc Markdown)
            parse_mode: 'HTML' hoặc 'Markdown'
        
        Returns:
            True nếu gửi thành công
        """
        if not self.bot:
            print(f"[MOCK] Sending Message: {message[:100]}...")
            return True

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=parse_mode
            )
            
            return True
            
        except TelegramError as e:
            print(f"❌ Telegram error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error sending message: {e}")
            return False
    
    
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
    
    def _format_prediction_message(self, data: Dict) -> str:
        """
        Format message đẹp cho prediction
        
        Returns:
            Formatted markdown string
        """
        # Extract data
        pred_date = data.get('prediction_date', 'N/A')
        region = data.get('region', 'N/A')
        province = data.get('province')
        pred_numbers = data.get('predicted_numbers', {})
        confidence = data.get('confidence_score', 0)
        
        # Extract predicted number
        predicted_num = pred_numbers.get('predicted_number', 'N/A')
        hot_numbers = pred_numbers.get('hot_numbers', [])
        
        # Build message
        msg = f"🎯 *Dự Đoán {region}*\n"
        msg += f"📅 Ngày: `{pred_date}`\n"
        if province:
            msg += f"📍 Đài: `{province}`\n"
        msg += "\n"
        
        msg += f"🔢 *Số Dự Đoán*\n"
        msg += f"Giải Đặc Biệt: `{predicted_num}`\n\n"
        
        if hot_numbers:
            msg += f"🔥 *Số Nóng (2 chữ số cuối)*\n"
            msg += f"{', '.join([f'`{n}`' for n in hot_numbers])}\n\n"
        
        msg += f"📊 Độ tin cậy: {confidence*100:.0f}%\n\n"
        
        # Disclaimer
        msg += f"⚠️ _Lưu ý: Dự đoán chỉ mang tính giải trí!_\n"
        msg += f"_Xổ số là ngẫu nhiên và không thể dự đoán chính xác._\n"
        msg += f"_Không nên dựa vào dự đoán này để đầu tư._"
        
        return msg
    


async def test_bot():
    """Test Telegram bot"""
    print(f"\n{'='*60}")
    print(f"Testing Telegram Bot")
    print(f"{'='*60}\n")
    
    # Sample prediction data
    sample_prediction = {
        'prediction_date': '2024-01-15',
        'region': 'XSMB',
        'predicted_numbers': {
            'predicted_number': '12345',
            'hot_numbers': ['12', '34', '56']
        },
        'confidence_score': 0.28
    }
    


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    asyncio.run(test_bot())
