"""XSMB combo-selection core focused on the production ``>=2/3`` KPI.

The package is additive. Legacy XSMB models and ensemble functions keep their
existing contracts; adapters in this package translate their output when the
optional shadow selector is enabled.
"""

from src.xsmb_combo.adapters import adapt_legacy_model_results
from src.xsmb_combo.domain import (
    AdapterResult,
    ComboEvaluation,
    ComboSelectorResult,
    PairScoreVector,
    SelectorStatus,
)
from src.xsmb_combo.metrics import (
    evaluate_combo,
    random_combo_hit_probability,
    random_expected_winning_circles,
)
from src.xsmb_combo.selector import select_combo

__all__ = [
    "AdapterResult",
    "ComboEvaluation",
    "ComboSelectorResult",
    "PairScoreVector",
    "SelectorStatus",
    "adapt_legacy_model_results",
    "evaluate_combo",
    "random_combo_hit_probability",
    "random_expected_winning_circles",
    "select_combo",
]
