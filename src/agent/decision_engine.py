"""
decision_engine.py
Rule-based decision engine cho Master Retrain Agent.

Logic:
  1. Nếu đài hôm nay HIT (hit=True) → NO_ACTION
  2. Đếm chuỗi miss gần nhất của đúng model/station đó
  3. Nếu MISS 3 kỳ gần nhất → RETRAIN, kể cả metric cũ vẫn OK
  4. Nếu chưa đủ 3 miss, dùng metric gate: AUC < 0.55 HOẶC hit_rate < 0.40
  5. Kiểm tra cooldown cho retrain do metric kém; 3-miss streak là hard trigger
     - Strategy được chọn dựa trên consecutive_fails VÀ lịch sử AUC improvement
     - Nếu AUC không cải thiện sau nhiều lần retrain → leo thang strategy nhanh hơn
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


# Thresholds — có thể override khi khởi tạo
DEFAULT_AUC_THRESHOLD = 0.55
DEFAULT_HIT_RATE_THRESHOLD = 0.40
DEFAULT_MIN_DAYS_SINCE_RETRAIN = 14  # cooldown ngày (2-3 kỳ/weekday cho XSMB weekday)
DEFAULT_MAX_RETRAIN_NO_IMPROVE = 3   # số lần retrain không cải thiện AUC → leo thang strategy
DEFAULT_FAIL_STREAK_RETRAIN_THRESHOLD = 3


@dataclass
class DecisionResult:
    should_retrain: bool
    action_type: str           # 'retrain_triggered' | 'skipped' | 'no_action'
    reason: str
    strategy: Optional[str]    # 'boost_estimators' | 'conservative' | 'full_reset'
    consecutive_fails: int
    old_metric_auc: Optional[float]
    old_hit_rate: Optional[float]
    old_params: dict = field(default_factory=dict)


class DecisionEngine:
    """
    Per-station decision engine.
    Đọc metric của chính đài đó từ model_registry, KHÔNG dùng global threshold cứng.
    """

    def __init__(
        self,
        auc_threshold: float = DEFAULT_AUC_THRESHOLD,
        hit_rate_threshold: float = DEFAULT_HIT_RATE_THRESHOLD,
        min_days_since_retrain: int = DEFAULT_MIN_DAYS_SINCE_RETRAIN,
        fail_streak_retrain_threshold: int = DEFAULT_FAIL_STREAK_RETRAIN_THRESHOLD,
    ):
        self.auc_threshold = auc_threshold
        self.hit_rate_threshold = hit_rate_threshold
        self.min_days_since_retrain = min_days_since_retrain
        self.fail_streak_retrain_threshold = fail_streak_retrain_threshold

    def analyze(
        self,
        region: str,
        province: Optional[str],
        weekday: Optional[int],
        hit_today: bool,
        db,
        target_date: date,
    ) -> DecisionResult:
        """
        Phân tích và ra quyết định có nên retrain không.
        (Cập nhật: MÔ HÌNH HỌC LIÊN TỤC - CONTINUOUS LEARNING)
        - Luôn luôn trigger retrain để cập nhật dữ liệu KQXS mới nhất của ngày hôm nay.
        - Nếu hôm nay đoán trúng -> giữ nguyên tham số (strategy='maintain').
        - Nếu hôm nay đoán trượt -> dùng logic leo thang strategy của Agent.
        """
        label = f"{region}/{province or 'all'}"
        if weekday is not None:
            DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            label += f"[wd={DOW[weekday]}]"

        # Bước 1: Lấy model active của ĐÀI NÀY từ model_registry
        registry = self._get_active_model(db, region, province, weekday)
        old_auc = registry.get("metric_auc") if registry else None
        old_hit_rate = registry.get("metric_hit_rate") if registry else None

        # Bước 2: Lấy các thông số thống kê để agent ra quyết định (fail streak, no improve)
        consecutive_fails = self._count_consecutive_fails(db, region, province, target_date, weekday)
        fail_streak_trigger = consecutive_fails >= self.fail_streak_retrain_threshold
        retrain_no_improve_count = self._count_retrain_without_auc_improvement(
            db, region, province, weekday, old_auc
        )

        # Bước 3: Phân loại trường hợp Trúng vs Trượt để Agent chọn strategy
        if hit_today:
            strategy = "maintain"
            reason_parts = ["Trúng hôm nay (Continuous Learning)"]
        else:
            strategy = self._pick_strategy(consecutive_fails, retrain_no_improve_count)
            reason_parts = ["Miss hôm nay (Continuous Learning)"]
            if fail_streak_trigger:
                reason_parts.append(f"3_miss_streak={consecutive_fails}>={self.fail_streak_retrain_threshold}")
            if old_auc is not None and old_auc < self.auc_threshold:
                reason_parts.append(f"AUC={old_auc:.3f}<{self.auc_threshold}")
            if old_hit_rate is not None and old_hit_rate < self.hit_rate_threshold:
                reason_parts.append(f"hit_rate={old_hit_rate:.0%}<{self.hit_rate_threshold:.0%}")
            if retrain_no_improve_count > 0:
                reason_parts.append(f"no_improve={retrain_no_improve_count}lần")

        reason_parts.append(f"strategy={strategy}")

        # Bước 4: Luôn trả về retrain_triggered
        return DecisionResult(
            should_retrain=True,
            action_type="retrain_triggered",
            reason=" | ".join(reason_parts),
            strategy=strategy,
            consecutive_fails=consecutive_fails,
            old_metric_auc=old_auc,
            old_hit_rate=old_hit_rate,
        )

    # ─────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────

    def _get_active_model(self, db, region: str, province: Optional[str], weekday: Optional[int]) -> Optional[dict]:
        """Lấy model active của chính đài này từ model_registry."""
        q = (
            db.supabase.table("model_registry")
            .select("metric_auc,metric_hit_rate,trained_at,version")
            .eq("region", region)
            .eq("status", "active")
            .order("trained_at", desc=True)
            .limit(1)
        )
        if province:
            q = q.eq("province", province)
        else:
            q = q.is_("province", "null")

        if weekday is not None:
            q = q.eq("weekday", weekday)
        else:
            q = q.is_("weekday", "null")

        result = q.execute()
        return result.data[0] if result.data else None

    def _is_metric_bad(self, auc: Optional[float], hit_rate: Optional[float]) -> bool:
        """
        True nếu metric đang kém (theo per-station values từ model_registry).
        Nếu không có metric (model cũ không track) → coi là kém để trigger retrain.
        """
        if auc is None and hit_rate is None:
            return True  # không có metric → coi là kém

        if auc is not None and auc < self.auc_threshold:
            return True
        if hit_rate is not None and hit_rate < self.hit_rate_threshold:
            return True
        return False

    def _days_since_last_retrain(
        self, db, region: str, province: Optional[str], target_date: date,
        weekday: Optional[int] = None,
    ) -> Optional[int]:
        """
        Kiểm tra xem đài này (theo weekday nếu có) có được retrain gần đây không.
        Returns: số ngày kể từ lần retrain cuối, hoặc None nếu chưa bao giờ retrain.
        """
        try:
            q = (
                db.supabase.table("agent_actions")
                .select("action_date")
                .eq("region", region)
                .eq("action_type", "retrain_triggered")
                .order("action_date", desc=True)
                .limit(1)
            )
            if province:
                q = q.eq("province", province)
            else:
                q = q.is_("province", "null")

            # Filter theo weekday để cooldown per-weekday (XSMB wd=0 không block wd=1)
            if weekday is not None:
                q = q.eq("weekday", weekday)
            else:
                q = q.is_("weekday", "null")

            result = q.execute()
            if not result.data:
                return None

            last_retrain = date.fromisoformat(result.data[0]["action_date"])
            return (target_date - last_retrain).days
        except Exception:
            return None  # nếu table chưa tồn tại → không có cooldown


    def _count_consecutive_fails(
        self, db, region: str, province: Optional[str], target_date: date,
        weekday: Optional[int] = None,
    ) -> int:
        """
        Đếm bao nhiêu kỳ liên tiếp (kể cả hôm nay) đài này bị miss.
        - Với XSMB weekday model: chỉ đếm các ngày có cùng weekday (mỗi 7 ngày xổ 1 kỳ)
        - Với XSMN province: đếm theo gap <= 7 ngày
        - Dừng khi gặp kỳ hit=True
        """
        q = (
            db.supabase.table("prediction_results")
            .select("prediction_date,hit")
            .eq("region", region)
            .not_.is_("hit", "null")   # chỉ kỳ đã verify
            .lte("prediction_date", target_date.isoformat())
            .order("prediction_date", desc=True)
            .limit(60)  # lấy 60 kỳ, chọn lọc sau
        )
        if province:
            q = q.eq("province", province)
        else:
            q = q.is_("province", "null")

        rows = q.execute().data
        if not rows:
            return 1  # chính ngày hôm nay

        # Nếu XSMB weekday: lọc chỉ lấy ngày cùng weekday
        if region.upper() == "XSMB" and weekday is not None:
            rows = [
                r for r in rows
                if date.fromisoformat(r["prediction_date"]).weekday() == weekday
            ]

        count = 0
        for row in rows:
            if not row["hit"]:
                count += 1
            else:
                break  # gặp kỳ trúng → dừng chuỗi

        return max(count, 1)

    def _count_retrain_without_auc_improvement(
        self, db, region: str, province: Optional[str],
        weekday: Optional[int], current_auc: Optional[float]
    ) -> int:
        """
        Đếm số lần retrain gần nhất mà AUC không cải thiện so với trước đó.
        Đọc từ agent_actions table, lấy 5 lần retrain gần nhất.
        Nếu AUC liên tục <= current_auc (không có tiến bộ) → trả về số lần đó.
        Mục đích: giúp agent leo thang strategy nhanh hơn khi retrain nhiều lần
        mà AUC vẫn không tăng vượt threshold.
        """
        if current_auc is None:
            return 0
        try:
            q = (
                db.supabase.table("agent_actions")
                .select("action_date,old_metric_auc,new_params")
                .eq("region", region)
                .eq("action_type", "retrain_triggered")
                .order("action_date", desc=True)
                .limit(5)
            )
            if province:
                q = q.eq("province", province)
            else:
                q = q.is_("province", "null")
            if weekday is not None:
                q = q.eq("weekday", weekday)
            else:
                q = q.is_("weekday", "null")

            rows = q.execute().data
            if not rows:
                return 0

            # Đếm bao nhiêu lần retrain mà AUC lúc đó cũng không vượt threshold
            no_improve_count = 0
            for r in rows:
                auc_then = r.get("old_metric_auc")
                if auc_then is not None and auc_then < self.auc_threshold:
                    no_improve_count += 1
                else:
                    break  # gặp lần retrain mà AUC từng tốt → dừng

            return no_improve_count
        except Exception:
            return 0

    @staticmethod
    def _pick_strategy(consecutive_fails: int, retrain_no_improve: int = 0) -> str:
        """
        Chọn strategy dựa vào số kỳ fail liên tiếp VÀ lịch sử AUC improvement.

        consecutive_fails (normal escalation):
          1-4:  boost_estimators  (tăng số cây + giảm lr)
          5-6:  conservative      (regularization mạnh, giảm max_depth)
          7+:   full_reset        (default + --force train toàn bộ data)

        retrain_no_improve (số lần retrain AUC vẫn <0.55, leo thang nhanh):
          1:   → conservative     (tăng regularization)
          2:   → scale_weight     (giải quyết class imbalance hit~24%)
          3+:  → full_reset       (đổi hoàn toàn chiến lược)

        Áp dụng cho cả XSMB weekday models và XSMN province models.
        """
        # Leo thang nhanh dựa trên lịch sử AUC không cải thiện
        if retrain_no_improve >= DEFAULT_MAX_RETRAIN_NO_IMPROVE:
            # Đã thử đủ kiểu mà AUC vẫn ~0.5 → full reset
            return "full_reset"
        elif retrain_no_improve == 2:
            # Đã thử boost + conservative → giờ thử scale class weight
            return "scale_weight"
        elif retrain_no_improve == 1:
            # Lần retrain trước không cải thiện → conservative
            if consecutive_fails >= 5:
                return "full_reset"
            return "conservative"

        # Normal escalation theo consecutive_fails (retrain_no_improve == 0)
        if consecutive_fails >= 7:
            return "full_reset"
        elif consecutive_fails >= 5:
            return "conservative"
        else:
            return "boost_estimators"
