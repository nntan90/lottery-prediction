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
            raise ValueError(
                "Missing Telegram credentials! "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables."
            )
        
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
    
    async def send_evaluation(self, metrics_data: Dict) -> bool:
        """
        Gửi báo cáo đánh giá
        
        Args:
            metrics_data: Dictionary từ database
                {
                    'evaluation_date': '2024-01-15',
                    'region': 'XSMB',
                    'accuracy_rate': 0.4,
                    'correct_predictions': 2,
                    'total_predictions': 5,
                    ...
                }
        
        Returns:
            True nếu gửi thành công
        """
        try:
            message = self._format_evaluation_message(metrics_data)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
            print(f"✅ Evaluation sent to Telegram")
            return True
            
        except TelegramError as e:
            print(f"❌ Telegram error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error sending evaluation: {e}")
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
        pred_numbers = data.get('predicted_numbers', {})
        confidence = data.get('confidence_score', 0)
        
        # Extract predicted number
        predicted_num = pred_numbers.get('predicted_number', 'N/A')
        hot_numbers = pred_numbers.get('hot_numbers', [])
        
        # Build message
        msg = f"🎯 *Dự Đoán {region}*\n"
        msg += f"📅 Ngày: `{pred_date}`\n\n"
        
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
    
    def _format_evaluation_message(self, data: Dict) -> str:
        """
        Format message đẹp cho evaluation
        
        Returns:
            Formatted markdown string
        """
        eval_date = data.get('evaluation_date', 'N/A')
        region = data.get('region', 'N/A')
        accuracy = data.get('accuracy_rate', 0)
        correct = data.get('correct_predictions', 0)
        total = data.get('total_predictions', 5)
        
        msg = f"📊 *Báo Cáo Đánh Giá {region}*\n"
        msg += f"📅 Ngày: `{eval_date}`\n\n"
        
        msg += f"✅ Số chữ số đúng: {correct}/{total}\n"
        msg += f"📈 Tỷ lệ chính xác: {accuracy*100:.1f}%\n\n"
        
        # Emoji dựa trên accuracy
        if accuracy >= 0.6:
            emoji = "🎉"
            comment = "Tuyệt vời!"
        elif accuracy >= 0.4:
            emoji = "👍"
            comment = "Khá tốt!"
        else:
            emoji = "📝"
            comment = "Cần cải thiện"
        
        msg += f"{emoji} _{comment}_"
        
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
    
    # Sample evaluation data
    sample_evaluation = {
        'evaluation_date': '2024-01-14',
        'region': 'XSMB',
        'accuracy_rate': 0.4,
        'correct_predictions': 2,
        'total_predictions': 5
    }
    
    try:
        notifier = LotteryNotifier()
        
        # Test prediction
        print("Sending test prediction...")
        success = await notifier.send_prediction(sample_prediction)
        
        if success:
            print("✅ Prediction sent successfully!")
        else:
            print("❌ Failed to send prediction")
        
        # Wait a bit
        await asyncio.sleep(2)
        
        # Test evaluation
        print("\nSending test evaluation...")
        success = await notifier.send_evaluation(sample_evaluation)
        
        if success:
            print("✅ Evaluation sent successfully!")
        else:
            print("❌ Failed to send evaluation")
            
    except Exception as e:
        print(f"❌ Test failed: {e}")


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    asyncio.run(test_bot())
