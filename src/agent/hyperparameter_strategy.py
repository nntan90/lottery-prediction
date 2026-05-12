"""
hyperparameter_strategy.py
Quyết định hyperparameters mới cho XGBoost dựa vào số lần fail liên tiếp.
Áp dụng cho cả XSMB (weekday models) và XSMN (province models).

4 chiến lược (theo thứ tự leo thang):
  boost_estimators : Tăng n_estimators + giảm lr → mới bắt đầu miss
  conservative     : Regularization mạnh (giảm max_depth) → fail kéo dài
  scale_weight     : Điều chỉnh scale_pos_weight để xử lý class imbalance → khi AUC ~0.5 lặp lại
  full_reset       : Quay về defaults + --force → reset hoàn toàn
"""

from typing import Tuple

# ─── Hyperparameter presets ──────────────────────────────────────────────────

# Params mặc định hiện tại (dùng trong train_xgb.py)
DEFAULT_PARAMS = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "scale_pos_weight": 1.0,
}

# Strategy 1: 1-4 kỳ fail liên tiếp
# → Tăng nhẹ số cây, giảm learning_rate để model "học kỹ hơn"
BOOST_ESTIMATORS_PARAMS = {
    "n_estimators": 500,
    "max_depth": 4,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "scale_pos_weight": 1.0,
}

# Strategy 2: 5-6 kỳ fail liên tiếp
# → Regularization mạnh hơn: giảm max_depth, subsample để tránh overfit
CONSERVATIVE_PARAMS = {
    "n_estimators": 400,
    "max_depth": 3,
    "learning_rate": 0.03,
    "subsample": 0.7,
    "colsample_bytree": 0.7,
    "scale_pos_weight": 1.0,
}

# Strategy 3: AUC vẫn ~0.5 sau nhiều lần retrain (no_improve >= 2)
# → Điều chỉnh class weight: hit=True ~24%, hit=False ~76%
#   scale_pos_weight = 76/24 ≈ 3.2 → model tập trung hơn vào minority class (hit=True)
# Áp dụng cho cả XSMB weekday và XSMN province (đều có class imbalance tương tự)
SCALE_WEIGHT_PARAMS = {
    "n_estimators": 500,
    "max_depth": 4,
    "learning_rate": 0.03,
    "subsample": 0.75,
    "colsample_bytree": 0.75,
    "scale_pos_weight": 3.2,
    "_force": True,
}

# Strategy 4: 7+ kỳ fail liên tiếp HOẶC no_improve >= 3
# → Full reset: quay về params ổn định, train với toàn bộ data (--force)
FULL_RESET_PARAMS = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "scale_pos_weight": 1.0,
    "_force": True,   # Cờ đặc biệt: truyền --force vào train_xgb.py
}

STRATEGY_MAP = {
    "boost_estimators": BOOST_ESTIMATORS_PARAMS,
    "conservative":     CONSERVATIVE_PARAMS,
    "scale_weight":     SCALE_WEIGHT_PARAMS,
    "full_reset":       FULL_RESET_PARAMS,
}


def get_params(strategy: str) -> Tuple[dict, dict]:
    """
    Trả về (old_params, new_params) cho strategy đã chọn.

    Args:
        strategy: 'boost_estimators' | 'conservative' | 'scale_weight' | 'full_reset'

    Returns:
        (old_params, new_params) - old_params luôn là DEFAULT_PARAMS
    """
    new_params = STRATEGY_MAP.get(strategy, DEFAULT_PARAMS).copy()
    return DEFAULT_PARAMS.copy(), new_params


def recommend_params(
    strategy: str,
    *,
    consecutive_fails: int = 0,
    old_auc: float | None = None,
    old_hit_rate: float | None = None,
    fail_streak_threshold: int = 3,
) -> Tuple[dict, dict]:
    """
    Đề xuất hyperparameters theo ngữ cảnh model vừa fail.

    Agent vẫn dùng strategy làm khung chính, nhưng tự tinh chỉnh thêm theo:
      - fail_streak >= 3: bắt buộc retrain, bật --force cho model ít data
      - AUC sát random: tăng scale_pos_weight để model chú ý class hit=True
      - hit_rate thấp: tăng regularization để giảm overfit vào nhiễu ngắn hạn

    Returns:
        (old_params, new_params) để truyền tiếp vào build_train_args().
    """
    old_params, new_params = get_params(strategy)

    if consecutive_fails >= fail_streak_threshold:
        new_params["_force"] = True
        new_params["n_estimators"] = max(int(new_params.get("n_estimators", 0)), 500)
        new_params["learning_rate"] = min(float(new_params.get("learning_rate", 0.05)), 0.03)

    if old_auc is not None and old_auc <= 0.52:
        new_params["scale_pos_weight"] = max(float(new_params.get("scale_pos_weight", 1.0)), 2.5)

    if old_hit_rate is not None and old_hit_rate <= 0.25:
        new_params["max_depth"] = min(int(new_params.get("max_depth", 4)), 3)
        new_params["subsample"] = min(float(new_params.get("subsample", 0.8)), 0.75)
        new_params["colsample_bytree"] = min(float(new_params.get("colsample_bytree", 0.8)), 0.75)

    return old_params, new_params


def build_train_args(region: str, province: str | None, weekday: int | None, new_params: dict) -> list[str]:
    """
    Xây dựng danh sách arguments để truyền vào train_xgb.py thông qua subprocess.
    Áp dụng cho cả XSMB và XSMN (province models).

    Args:
        region: 'XSMB' | 'XSMN'
        province: tỉnh slug hoặc None (None = XSMB all)
        weekday: 0-6 hoặc None
        new_params: dict hyperparameters (output từ get_params)

    Returns:
        List[str] — args để truyền vào subprocess
    """
    from datetime import date
    wd_suffix = f"_wd{weekday}" if weekday is not None else ""
    version = f"v3_agent_{date.today().strftime('%Y%m%d')}{wd_suffix}"

    args = [
        "--region", region,
        "--province", province or "all",
        "--version", version,
        "--n_estimators",      str(new_params.get("n_estimators",      DEFAULT_PARAMS["n_estimators"])),
        "--max_depth",         str(new_params.get("max_depth",         DEFAULT_PARAMS["max_depth"])),
        "--learning_rate",     str(new_params.get("learning_rate",     DEFAULT_PARAMS["learning_rate"])),
        "--subsample",         str(new_params.get("subsample",         DEFAULT_PARAMS["subsample"])),
        "--colsample_bytree",  str(new_params.get("colsample_bytree",  DEFAULT_PARAMS["colsample_bytree"])),
        "--scale_pos_weight",  str(new_params.get("scale_pos_weight",  DEFAULT_PARAMS["scale_pos_weight"])),
    ]

    if weekday is not None:
        args += ["--weekday", str(weekday)]

    if new_params.get("_force"):
        args.append("--force")

    return args


def describe_strategy(strategy: str, old_params: dict, new_params: dict) -> str:
    """Tạo mô tả human-readable về thay đổi hyperparameters."""
    changes = []
    for key in ["n_estimators", "max_depth", "learning_rate", "subsample", "colsample_bytree", "scale_pos_weight"]:
        old_val = old_params.get(key)
        new_val = new_params.get(key)
        if old_val is not None and new_val is not None and old_val != new_val:
            direction = "↑" if new_val > old_val else "↓"
            changes.append(f"{key}: {old_val}→{new_val}{direction}")

    if new_params.get("_force"):
        changes.append("force=True")

    return f"[{strategy}] " + (", ".join(changes) if changes else "Không thay đổi")
