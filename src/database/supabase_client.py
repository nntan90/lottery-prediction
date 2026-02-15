"""
Supabase Database Client
Quản lý tất cả operations với Supabase database
"""

import os
from supabase import create_client, Client
from datetime import datetime, date
from typing import List, Dict, Optional
from dotenv import load_dotenv


class LotteryDB:
    """Client để tương tác với Supabase database"""
    
    def __init__(self):
        """Initialize Supabase client với credentials từ environment variables"""
        load_dotenv()
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError(
                "Missing Supabase credentials! "
                "Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables."
            )
        
        self.supabase: Client = create_client(supabase_url, supabase_key)
    
    # ==================== LOTTERY DRAWS ====================
    
    def insert_draw(self, draw_data: Dict) -> Dict:
        """
        Insert kết quả quay số vào database
        
        Args:
            draw_data: Dictionary chứa thông tin kết quả
                {
                    'draw_date': date object,
                    'region': 'XSMB' hoặc 'XSMN',
                    'special_prize': '12345',
                    'first_prize': '67890',
                    ...
                }
        
        Returns:
            Response từ Supabase
        """
        # Convert date object sang string nếu cần
        if isinstance(draw_data.get('draw_date'), date):
            draw_data['draw_date'] = draw_data['draw_date'].isoformat()
        
        try:
            response = self.supabase.table("lottery_draws").insert(draw_data).execute()
            return response
        except Exception as e:
            print(f"❌ Error inserting draw: {e}")
            raise
    
    def upsert_draw(self, draw_data: Dict) -> Dict:
        """
        Insert hoặc update kết quả (upsert - không báo lỗi nếu trùng)
        
        Args:
            draw_data: Dictionary chứa thông tin kết quả
        
        Returns:
            Response từ Supabase
        """
        # Convert date object sang string nếu cần
        if isinstance(draw_data.get('draw_date'), date):
            draw_data['draw_date'] = draw_data['draw_date'].isoformat()
        
        try:
            # Upsert: insert nếu chưa có, update nếu đã có
            # Use the new constraint with province column
            response = self.supabase.table("lottery_draws").upsert(
                draw_data,
                on_conflict='draw_date,region,province'  # Updated constraint
            ).execute()
            return response
        except Exception as e:
            print(f"❌ Error upserting draw: {e}")
            raise
    
    def get_historical_data(
        self, 
        region: str, 
        days: int = 365,
        province: str = None
    ) -> List[Dict]:
        """
        Lấy dữ liệu lịch sử
        
        Args:
            region: 'XSMB' hoặc 'XSMN'
            days: Số ngày muốn lấy (mặc định 365)
            province: Tỉnh (optional, for XSMN only)
        
        Returns:
            List of dictionaries chứa kết quả
        """
        try:
            query = self.supabase.table("lottery_draws")\
                .select("*")\
                .eq("region", region)\
                .order("draw_date", desc=True)\
                .limit(days)
            
            # Add province filter if specified
            if province:
                query = query.eq("province", province)
            
            response = query.execute()
            
            return response.data
        except Exception as e:
            print(f"❌ Error fetching historical data: {e}")
            return []
    
    def get_draw_by_date(self, draw_date: date, region: str, province: str = None) -> Optional[Dict]:
        """
        Lấy kết quả cho một ngày cụ thể
        
        Args:
            draw_date: Ngày cần lấy
            region: 'XSMB' hoặc 'XSMN'
            province: Tỉnh (optional, for XSMN only)
        
        Returns:
            Dictionary chứa kết quả hoặc None nếu không tìm thấy
        """
        try:
            query = self.supabase.table("lottery_draws")\
                .select("*")\
                .eq("draw_date", draw_date.isoformat())\
                .eq("region", region)
            
            # Add province filter if specified
            if province:
                query = query.eq("province", province)
            
            response = query.execute()
            
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"❌ Error fetching draw by date: {e}")
            return None
    
    # ==================== PREDICTIONS ====================
    
    def save_prediction(self, prediction_data: Dict) -> Dict:
        """
        Lưu dự đoán vào database (upsert - update nếu đã tồn tại)
        
        Args:
            prediction_data: Dictionary chứa dự đoán
                {
                    'prediction_date': date object,
                    'region': 'XSMB',
                    'province': None or province code,
                    'model_version': 'frequency_v1',
                    'predicted_numbers': {...},
                    'confidence_score': 0.3
                }
        
        Returns:
            Response từ Supabase
        """
        # Convert date object sang string
        if isinstance(prediction_data.get('prediction_date'), date):
            prediction_data['prediction_date'] = prediction_data['prediction_date'].isoformat()
        
        # Force province to empty string if None (to support unique constraint)
        if prediction_data.get('province') is None:
            prediction_data['province'] = ''
            
        try:
            # Upsert: insert nếu chưa có, update nếu đã có
            # Use the new constraint keys
            response = self.supabase.table("predictions").upsert(
                prediction_data,
                on_conflict='prediction_date,region,province,model_version'
            ).execute()
            return response
        except Exception as e:
            print(f"❌ Error saving prediction: {e}")
            raise
    
    def get_prediction_by_date(
        self, 
        prediction_date: date, 
        region: str,
        province: str = None
    ) -> Optional[Dict]:
        """
        Lấy dự đoán cho một ngày cụ thể
        
        Args:
            prediction_date: Ngày cần lấy dự đoán
            region: 'XSMB' hoặc 'XSMN'
            province: Tỉnh (optional, for XSMN only)
        
        Returns:
            Dictionary chứa dự đoán hoặc None
        """
        try:
            query = self.supabase.table("predictions")\
                .select("*")\
                .eq("prediction_date", prediction_date.isoformat())\
                .eq("region", region)\
                .order("created_at", desc=True)\
                .limit(1)
            
            # Add province filter if specified
            if province:
                query = query.eq("province", province)
            
            response = query.execute()
            
            return response.data[0] if response.data else None
        except Exception as e:
            print(f"❌ Error fetching prediction: {e}")
            return None
    
    # ==================== EVALUATION METRICS ====================
    
    def save_evaluation(self, metrics_data: Dict) -> Dict:
        """
        Lưu kết quả đánh giá
        
        Args:
            metrics_data: Dictionary chứa metrics
                {
                    'evaluation_date': date object,
                    'region': 'XSMB',
                    'total_predictions': 1,
                    'correct_predictions': 2,
                    'accuracy_rate': 0.4,
                    'model_version': 'frequency_v1'
                }
        
        Returns:
            Response từ Supabase
        """
        # Convert date object sang string
        if isinstance(metrics_data.get('evaluation_date'), date):
            metrics_data['evaluation_date'] = metrics_data['evaluation_date'].isoformat()
        
        try:
            response = self.supabase.table("evaluation_metrics").insert(metrics_data).execute()
            return response
        except Exception as e:
            print(f"❌ Error saving evaluation: {e}")
            raise
    
    def get_recent_metrics(self, region: str, days: int = 30) -> List[Dict]:
        """
        Lấy metrics gần đây
        
        Args:
            region: 'XSMB' hoặc 'XSMN'
            days: Số ngày gần đây (mặc định 30)
        
        Returns:
            List of dictionaries chứa metrics
        """
        try:
            response = self.supabase.table("evaluation_metrics")\
                .select("*")\
                .eq("region", region)\
                .order("evaluation_date", desc=True)\
                .limit(days)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"❌ Error fetching metrics: {e}")
            return []
    
    # ==================== CRAWLER LOGS ====================
    
    def log_crawler_status(self, log_data: Dict) -> Dict:
        """
        Ghi log crawler
        
        Args:
            log_data: Dictionary chứa log info
                {
                    'crawl_date': date object,
                    'region': 'XSMB',
                    'status': 'success' hoặc 'failed',
                    'error_message': '...' (optional),
                    'records_inserted': 1
                }
        
        Returns:
            Response từ Supabase
        """
        # Convert date object sang string
        if isinstance(log_data.get('crawl_date'), date):
            log_data['crawl_date'] = log_data['crawl_date'].isoformat()
        
        try:
            response = self.supabase.table("crawler_logs").insert(log_data).execute()
            return response
        except Exception as e:
            print(f"❌ Error logging crawler status: {e}")
            raise
    
    def get_recent_crawler_logs(self, days: int = 7) -> List[Dict]:
        """
        Lấy crawler logs gần đây
        
        Args:
            days: Số ngày gần đây (mặc định 7)
        
        Returns:
            List of dictionaries chứa logs
        """
        try:
            response = self.supabase.table("crawler_logs")\
                .select("*")\
                .order("created_at", desc=True)\
                .limit(days * 2)\
                .execute()
            
            return response.data
        except Exception as e:
            print(f"❌ Error fetching crawler logs: {e}")
            return []


# ==================== HELPER FUNCTION ====================

def test_connection():
    """Test kết nối với Supabase"""
    try:
        db = LotteryDB()
        print("✅ Connected to Supabase successfully!")
        
        # Test query
        result = db.supabase.table("lottery_draws").select("count").execute()
        print(f"✅ Database accessible. Total draws: {len(result.data)}")
        
        return True
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        return False


if __name__ == "__main__":
    # Test khi chạy file này trực tiếp
    test_connection()
