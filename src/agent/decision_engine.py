"""
decision_engine.py
Rule-based decision engine cho Master Retrain Agent.

Logic:
  1. Nếu đài hôm nay HIT (hit=True) → NO_ACTION
  2. Lấy model active của đài từ model_registry → metric_auc, metric_hit_rate, trained_at
  3. Kiểm tra metric per-station: AUC < 0.55 HOẶC hit_rate < 0.40
  4. Kiểm tra cooldown: chưa retrain trong 7 ngày
  5. Nếu MISS + metric kém + cooldown OK → RETRAIN với strategy phù hợp
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional


# Thresholds — có thể override khi khởi tạo
DEFAULT_AUC_THRESHOLD = 0.55
DEFAULT_HIT_RATE_THRESHOLD = 0.40
DEFAULT_MIN_DAYS_SINCE_RETRAIN = 7  # cooldown ngày


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
    ):
        self.auc_threshold = auc_threshold
        self.hit_rate_threshold = hit_rate_threshold
        self.min_days_since_retrain = min_days_since_retrain

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

        Args:
            region: 'XSMB' hoặc 'XSMN'
            province: tỉnh (None = XSMB all)
            weekday: weekday model cần check (0=Mon..6=Sun hoặc None)
            hit_today: kết quả verify hôm nay
            db: LotteryDB instance
            target_date: ngày verify

        Returns:
            DecisionResult
        """
        label = f"{region}/{province or 'all'}"
        if weekday is not None:
            DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            label += f"[wd={DOW[weekday]}]"

        # Bước 1: Hôm nay trúng → không cần làm gì
        if hit_today:
            return DecisionResult(
                should_retrain=False,
                action_type="no_action",
                reason="Trúng hôm nay — model đang hoạt động tốt",
                strategy=None,
                consecutive_fails=0,
                old_metric_auc=None,
                old_hit_rate=None,
            )

        # Bước 2: Lấy model active của ĐÀI NÀY từ model_registry (per-station)
        registry = self._get_active_model(db, region, province, weekday)
        old_auc = registry.get("metric_auc") if registry else None
        old_hit_rate = registry.get("metric_hit_rate") if registry else None
        trained_at_str = registry.get("trained_at") if registry else None

        # Bước 3: Kiểm tra metric per-station
        metric_bad = self._is_metric_bad(old_auc, old_hit_rate)
        if not metric_bad:
            return DecisionResult(
                should_retrain=False,
                action_type="skipped",
                reason=(
                    f"Miss hôm nay nhưng metric vẫn OK "
                    f"(AUC={old_auc}, hit_rate={old_hit_rate:.0%} nếu có)"
                ),
                strategy=None,
                consecutive_fails=1,
                old_metric_auc=old_auc,
                old_hit_rate=old_hit_rate,
            )

        # Bước 4: Cooldown — đã retrain gần đây chưa?
        days_since_retrain = self._days_since_last_retrain(db, region, province, target_date)
        if days_since_retrain is not None and days_since_retrain < self.min_days_since_retrain:
            return DecisionResult(
                should_retrain=False,
                action_type="skipped",
                reason=(
                    f"Cooldown: đã retrain {days_since_retrain} ngày trước "
                    f"(cần ≥ {self.min_days_since_retrain} ngày)"
                ),
                strategy=None,
                consecutive_fails=1,
                old_metric_auc=old_auc,
                old_hit_rate=old_hit_rate,
            )

        # Bước 5: Đếm consecutive fails gần nhất để chọn strategy
        consecutive_fails = self._count_consecutive_fails(db, region, province, target_date)
        strategy = self._pick_strategy(consecutive_fails)

        reason_parts = [f"Miss hôm nay"]
        if old_auc is not None and old_auc < self.auc_threshold:
            reason_parts.append(f"AUC={old_auc:.3f}<{self.auc_threshold}")
        if old_hit_rate is not None and old_hit_rate < self.hit_rate_threshold:
            reason_parts.append(f"hit_rate={old_hit_rate:.0%}<{self.hit_rate_threshold:.0%}")
        reason_parts.append(f"strategy={strategy}")

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
        self, db, region: str, province: Optional[str], target_date: date
    ) -> Optional[int]:
        """
        Kiểm tra xem đài này có được retrain gần đây không, qua bảng agent_actions.
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

            result = q.execute()
            if not result.data:
                return None

            last_retrain = date.fromisoformat(result.data[0]["action_date"])
            return (target_date - last_retrain).days
        except Exception:
            return None  # nếu table chưa tồn tại → không có cooldown

    def _count_consecutive_fails(
        self, db, region: str, province: Optional[str], target_date: date
    ) -> int:
        """
        Đếm bao nhiêu ngày liên tiếp (kể cả hôm nay) đài này bị miss.
        Đọc từ prediction_results ORDER BY date DESC, dừng khi gặp hit=True.
        """
        q = (
            db.supabase.table("prediction_results")
            .select("prediction_date,hit")
            .eq("region", region)
            .eq("hit", False)
            .lte("prediction_date", target_date.isoformat())
            .order("prediction_date", desc=True)
            .limit(30)  # chỉ cần check 30 ngày gần nhất
        )
        if province:
            q = q.eq("province", province)
        else:
            q = q.is_("province", "null")

        rows = q.execute().data
        if not rows:
            return 1  # chính ngày hôm nay

        # Đếm chuỗi fail liên tục (ngày kề nhau)
        count = 0
        prev_date = None
        for row in rows:
            row_date = date.fromisoformat(row["prediction_date"])
            if prev_date is None:
                prev_date = row_date
                count = 1
            else:
                # Kiểm tra có phải ngày liền kề không
                # (XSMN tỉnh chỉ mở hàng tuần → gap có thể 7 ngày)
                gap = (prev_date - row_date).days
                if gap <= 7:  # cho phép gap tối đa 7 ngày (1 tuần = 1 kỳ tỉnh XSMN)
                    count += 1
                    prev_date = row_date
                else:
                    break

        return count

    @staticmethod
    def _pick_strategy(consecutive_fails: int) -> str:
        """
        Chọn strategy dựa vào số ngày/kỳ fail liên tiếp.
        
        - 1-4 lần: boost_estimators (tăng nhẹ complexity)
        - 5-6 lần: conservative (regularization mạnh hơn)
        - 7+ lần:  full_reset (quay về default + train nhiều data hơn)
        """
        if consecutive_fails >= 7:
            return "full_reset"
        elif consecutive_fails >= 5:
            return "conservative"
        else:
            return "boost_estimators"
