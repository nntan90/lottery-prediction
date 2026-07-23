"""Configuration for the XSMN Provincial Digit Transition shadow model."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DigitTransitionConfig:
    """Deterministic statistical and evidence gates for PDA/DDT inference."""

    top_k_states: int = 32
    min_transitions: int = 12
    province_prior_strength: float = 12.0
    pair_prior_strength: float = 18.0
    interaction_prior_strength: float = 24.0
    calibration_min_folds: int = 20
    calibration_bins: int = 10
    coupling_prior_strength: float = 24.0
    regional_prior_transitions_per_province: int = 52
    top_unit_digits: int = 3
    candidates_per_unit: int = 10

    def __post_init__(self) -> None:
        integer_fields = {
            "top_k_states": self.top_k_states,
            "min_transitions": self.min_transitions,
            "calibration_min_folds": self.calibration_min_folds,
            "calibration_bins": self.calibration_bins,
            "top_unit_digits": self.top_unit_digits,
            "candidates_per_unit": self.candidates_per_unit,
            "regional_prior_transitions_per_province": self.regional_prior_transitions_per_province,
        }
        for name, value in integer_fields.items():
            if value < 1:
                raise ValueError(f"{name} must be positive")
        if not 2 <= self.top_unit_digits <= 10:
            raise ValueError("top_unit_digits must be between 2 and 10")
        if self.candidates_per_unit > 10:
            raise ValueError("candidates_per_unit cannot exceed 10")

        numeric_fields = {
            "province_prior_strength": self.province_prior_strength,
            "pair_prior_strength": self.pair_prior_strength,
            "interaction_prior_strength": self.interaction_prior_strength,
            "coupling_prior_strength": self.coupling_prior_strength,
        }
        for name, value in numeric_fields.items():
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
