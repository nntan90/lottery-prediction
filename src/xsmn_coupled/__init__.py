"""Shadow-only Coupled Motif Retrieval predictor for XSMN."""

from .domain import CMRConfig
from .predictor import predict_coupled
from .service import generate_shadow_prediction

__all__ = ["CMRConfig", "generate_shadow_prediction", "predict_coupled"]
