"""Typed contracts for the additive XSMB combo selector."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Tuple


PAIR_COUNT = 100


class SelectorStatus(str, Enum):
    """Stable status values returned by the shadow selector."""

    SUCCESS = "success"
    INSUFFICIENT_CANDIDATES = "insufficient_candidates"
    INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class PairScoreVector:
    """Relative evidence for all pairs ``00..99`` from one legacy model.

    ``scores`` are ranking evidence only. They are deliberately not named
    probabilities because legacy model outputs are not out-of-fold calibrated.
    """

    model_name: str
    scores: Tuple[float, ...]
    source_pairs: Tuple[int, ...]
    source_family: str = "unknown"
    score_kind: str = "relative_evidence"
    coverage_kind: str = "legacy_top_n"

    def __post_init__(self) -> None:
        if not self.model_name:
            raise ValueError("model_name must not be empty")
        if not self.source_family:
            raise ValueError("source_family must not be empty")
        if len(self.scores) != PAIR_COUNT:
            raise ValueError(f"scores must contain {PAIR_COUNT} values")
        if any(not math.isfinite(score) or score < 0.0 for score in self.scores):
            raise ValueError("scores must be finite and non-negative")
        if any(pair < 0 or pair >= PAIR_COUNT for pair in self.source_pairs):
            raise ValueError("source_pairs must be between 0 and 99")


@dataclass(frozen=True)
class AdapterResult:
    """Sanitized legacy model outputs plus compatibility diagnostics."""

    vectors: Tuple[PairScoreVector, ...]
    skipped_models: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ComboEvaluation:
    """Canonical evaluation of exactly three predicted XSMB pairs."""

    predicted_pairs: Tuple[int, int, int]
    actual_tails: frozenset[int]
    matched_pairs: Tuple[int, ...]
    hit_count: int
    combo_hit: bool
    winning_circles: int
    cost: int
    revenue: int
    profit: int


@dataclass(frozen=True)
class JointPairEvidence:
    """Auditable pairwise joint probability used by a selected triple."""

    pair_a: int
    pair_b: int
    probability: float


@dataclass(frozen=True)
class ComboSelectorResult:
    """Result of deterministic exhaustive selection for the shadow challenger."""

    status: SelectorStatus
    top_pairs: Tuple[int, ...] = ()
    objective: str = "combo_probability"
    objective_score: float = 0.0
    combo_probability: float = 0.0
    expected_winning_circles: float = 0.0
    candidate_pool: Tuple[int, ...] = ()
    contributing_models: Tuple[str, ...] = ()
    skipped_models: Tuple[str, ...] = ()
    evaluated_triples: int = 0
    joint_pair_evidence: Tuple[JointPairEvidence, ...] = ()
    triple_probability: float = 0.0
    diagnostics: Tuple[str, ...] = ()
    score_semantics: str = "combo_score_uncalibrated"
    selector_version: str = "xsmb_hybrid_combo_v6"
    active_weights: Tuple[Tuple[str, float], ...] = ()
    source_families: Tuple[Tuple[str, str], ...] = ()
