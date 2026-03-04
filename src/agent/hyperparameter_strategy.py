"""
hyperparameter_strategy.py
Quyết định hyperparameters mới cho XGBoost dựa vào số lần fail liên tiếp.

3 chiến lược (theo thứ tự leo thang):
  boost_estimators : Tăng n_estimators + giảm learning_rate nhẹ → phù hợp khi mới bắt đầu miss
  conservative     : Regularization mạnh (giảm max_depth) → khi fail kéo dài
  full_reset       : Quay về defaults ổn định, dùng --force để train nhiều data hơn
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
}

# Strategy 1: 1-4 kỳ fail liên tiếp
# → Tăng nhẹ số cây, giảm learning_rate để model "cẩn thận" hơn
BOOST_ESTIMATORS_PARAMS = {
    "n_estimators": 500,
    "max_depth": 4,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

# Strategy 2: 5-6 kỳ fail liên tiếp
# → Regularization mạnh hơn: giảm max_depth + tăng subsample sampling
CONSERVATIVE_PARAMS = {
    "n_estimators": 400,
    "max_depth": 3,
    "learning_rate": 0.03,
    "subsample": 0.7,
    "colsample_bytree": 0.7,
}

# Strategy 3: 7+ kỳ fail liên tiếp
# → Full reset: quay về params ổn định, nhưng train với nhiều data hơn (--force)
FULL_RESET_PARAMS = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "_force": True,   # Cờ đặc biệt: truyền --force vào train_xgb.py
}

STRATEGY_MAP = {
    "boost_estimators": BOOST_ESTIMATORS_PARAMS,
    "conservative":     CONSERVATIVE_PARAMS,
    "full_reset":       FULL_RESET_PARAMS,
}


def get_params(strategy: str) -> Tuple[dict, dict]:
    """
    Trả về (old_params, new_params) cho strategy đã chọn.

    Args:
        strategy: 'boost_estimators' | 'conservative' | 'full_reset'

    Returns:
        (old_params, new_params) - old_params luôn là DEFAULT_PARAMS
    """
    new_params = STRATEGY_MAP.get(strategy, DEFAULT_PARAMS).copy()
    return DEFAULT_PARAMS.copy(), new_params


def build_train_args(region: str, province: str | None, weekday: int | None, new_params: dict) -> list[str]:
    """
    Xây dựng danh sách arguments để truyền vào train_xgb.py thông qua subprocess.

    Args:
        region: 'XSMB' | 'XSMN'
        province: tỉnh slug hoặc None
        weekday: 0-6 hoặc None
        new_params: dict hyperparameters (output từ get_params)

    Returns:
        List[str] — args để truyền vào subprocess
    """
    DOW_NAMES = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    wd_suffix = f"_wd{weekday}" if weekday is not None else ""
    
    from datetime import date
    version = f"v3_agent_{date.today().strftime('%Y%m%d')}{wd_suffix}"

    args = [
        "--region", region,
        "--province", province or "all",
        "--version", version,
        "--n_estimators", str(new_params.get("n_estimators", DEFAULT_PARAMS["n_estimators"])),
        "--max_depth", str(new_params.get("max_depth", DEFAULT_PARAMS["max_depth"])),
        "--learning_rate", str(new_params.get("learning_rate", DEFAULT_PARAMS["learning_rate"])),
    ]

    if weekday is not None:
        args += ["--weekday", str(weekday)]

    if new_params.get("_force"):
        args.append("--force")

    return args


def describe_strategy(strategy: str, old_params: dict, new_params: dict) -> str:
    """Tạo mô tả human-readable về thay đổi hyperparameters."""
    changes = []
    for key in ["n_estimators", "max_depth", "learning_rate", "subsample", "colsample_bytree"]:
        old_val = old_params.get(key)
        new_val = new_params.get(key)
        if old_val is not None and new_val is not None and old_val != new_val:
            direction = "↑" if new_val > old_val else "↓"
            changes.append(f"{key}: {old_val}→{new_val}{direction}")

    if new_params.get("_force"):
        changes.append("force=True (train với nhiều data hơn)")

    return f"[{strategy}] " + (", ".join(changes) if changes else "Không thay đổi")
