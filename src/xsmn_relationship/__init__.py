"""XSMN relationship consensus shadow predictor."""

from .domain import MatchedOccasion, RelationshipConfig
from .predictor import predict_relationship
from .service import generate_relationship_shadow

__all__ = [
    "MatchedOccasion",
    "RelationshipConfig",
    "generate_relationship_shadow",
    "predict_relationship",
]
