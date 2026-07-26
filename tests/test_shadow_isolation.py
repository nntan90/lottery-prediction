"""Regression guards that keep CMR/DDT out of production weighting."""

from src.xsmn_ensemble import auto_weight


def test_shadow_models_never_enter_xsmn_auto_weights(monkeypatch) -> None:
    predictions = [
        {
            "prediction_date": "2026-07-26",
            "model_name": "frequency",
            "pair_1": 10,
            "pair_2": 11,
            "pair_3": 12,
        },
        {
            "prediction_date": "2026-07-26",
            "model_name": "ddt_shadow",
            "pair_1": 20,
            "pair_2": 21,
            "pair_3": 22,
        },
    ]
    monkeypatch.setattr(
        auto_weight,
        "_query_model_predictions",
        lambda *_args, **_kwargs: predictions,
    )
    monkeypatch.setattr(
        auto_weight,
        "_query_actual_tails",
        lambda *_args, **_kwargs: {"2026-07-26": {10, 20}},
    )

    result = auto_weight.compute_optimal_weights(
        object(),
        current_weights={"frequency": 1.0},
    )

    assert set(result) == {"frequency"}
