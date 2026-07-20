"""Shadow-only Coupled Motif Retrieval predictor for XSMN."""

from .domain import CMRConfig
from .predictor import predict_coupled

__all__ = ["CMRConfig", "predict_coupled"]
