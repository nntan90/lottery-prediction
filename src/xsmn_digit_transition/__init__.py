"""Province-first Dynamic Digit Transition shadow predictor for XSMN."""

from .config import DigitTransitionConfig
from .service import generate_shadow_prediction, predict_digit_transition

__all__ = [
    "DigitTransitionConfig",
    "generate_shadow_prediction",
    "predict_digit_transition",
]
