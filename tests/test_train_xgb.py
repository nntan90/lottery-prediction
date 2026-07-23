import asyncio
import subprocess
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.models.xgb_model import FEATURE_COLS
from src.scripts import retrain_weekday_models
from src.scripts.train_xgb import (
    build_walk_forward_folds,
    train_with_walk_forward,
    validate_and_sort_training_data,
)


def _training_frame(draw_count: int) -> pd.DataFrame:
    rows = []
    start = date(2025, 1, 1)
    for draw_index in range(draw_count):
        draw_date = (start + timedelta(days=draw_index * 7)).isoformat()
        for pair in range(100):
            row = {
                column: float((pair + draw_index) % 11)
                for column in FEATURE_COLS
            }
            row.update(
                {
                    "pair": pair,
                    "feature_date": draw_date,
                    "hit": int(pair % 10 == draw_index % 10),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def test_walk_forward_keeps_shuffled_draws_intact_and_ordered():
    shuffled = _training_frame(60).sample(frac=1.0, random_state=42)

    folds = build_walk_forward_folds(shuffled)

    assert len(folds) == 5
    assert all(len(fold.validation_dates) == 6 for fold in folds)
    assert len(folds[0].train_dates) == 30
    assert folds[-1].validation_dates[-1] == max(shuffled["feature_date"])
    for fold in folds:
        assert set(fold.train_dates).isdisjoint(fold.validation_dates)
        assert max(fold.train_dates) < min(fold.validation_dates)


def test_force_sized_history_builds_at_least_two_folds_by_draw_count():
    frame = _training_frame(24)

    folds = build_walk_forward_folds(frame)

    assert len(folds) == 4
    assert len(folds[0].train_dates) == 12
    assert all(len(fold.validation_dates) == 3 for fold in folds)
    assert folds[-1].validation_dates[-1] == frame["feature_date"].max()


@pytest.mark.parametrize("corruption", ["missing", "duplicate"])
def test_draw_integrity_rejects_missing_or_duplicate_pair(corruption):
    frame = _training_frame(24)
    first_date = frame["feature_date"].min()
    first_draw = frame["feature_date"] == first_date

    if corruption == "missing":
        frame = frame.drop(frame[first_draw & (frame["pair"] == 99)].index)
    else:
        duplicate = frame[first_draw & (frame["pair"] == 0)].iloc[[0]]
        frame = pd.concat([frame, duplicate], ignore_index=True)

    with pytest.raises(ValueError, match="Dữ liệu kỳ không hợp lệ"):
        validate_and_sort_training_data(frame)


@pytest.mark.parametrize("invalid_hit", [None, 2, "not-a-label"])
def test_draw_integrity_rejects_non_binary_hit_labels(invalid_hit):
    frame = _training_frame(24)
    frame["hit"] = frame["hit"].astype(object)
    frame.loc[0, "hit"] = invalid_hit

    with pytest.raises(ValueError, match="nhãn nhị phân"):
        validate_and_sort_training_data(frame)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_folds": 1}, "max_folds"),
        ({"min_initial_train_draws": 0}, "min_initial_train_draws"),
    ],
)
def test_walk_forward_rejects_invalid_fold_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        build_walk_forward_folds(_training_frame(24), **kwargs)


def test_walk_forward_aggregates_metrics_and_refits_all_data():
    frame = _training_frame(60).sample(frac=1.0, random_state=7)

    class FakeModel:
        instances = []
        aucs = iter([0.55, 0.61, 0.63, 0.70, 0.80])
        hits = iter([0.20, 0.40, 0.60, 0.80, 1.00])

        def __init__(self, **params):
            self.params = params
            self.calls = []
            self.__class__.instances.append(self)

        def train(self, X_train, y_train, X_val=None, y_val=None):
            self.calls.append((len(X_train), X_val is not None))
            if X_val is None:
                return {}
            return {
                "auc": next(self.__class__.aucs),
                "hit_rate_top3": next(self.__class__.hits),
            }

    production_model, metrics = train_with_walk_forward(
        frame,
        {"n_estimators": 10},
        model_class=FakeModel,
    )

    assert len(FakeModel.instances) == 6
    assert all(instance.calls[0][1] for instance in FakeModel.instances[:-1])
    assert production_model is FakeModel.instances[-1]
    assert production_model.calls == [(6000, False)]
    assert metrics["auc"] == 0.63
    assert metrics["auc_mean"] == 0.658
    assert metrics["auc_min"] == 0.55
    assert metrics["hit_rate_top3"] == 0.6


def test_walk_forward_requires_two_validation_folds_with_both_classes():
    frame = _training_frame(24)
    dates = sorted(frame["feature_date"].unique())
    for draw_date in dates[15:]:
        frame.loc[frame["feature_date"] == draw_date, "hit"] = 0

    class FakeModel:
        final_fit_called = False

        def __init__(self, **_params):
            pass

        def train(self, _X_train, _y_train, X_val=None, _y_val=None):
            if X_val is None:
                self.__class__.final_fit_called = True
                return {}
            return {"auc": 0.7, "hit_rate_top3": 0.5}

    with pytest.raises(ValueError, match="ít nhất 2 folds"):
        train_with_walk_forward(frame, {}, model_class=FakeModel)

    assert not FakeModel.final_fit_called


@pytest.mark.parametrize("bad_hit_rate", [None, float("nan"), -0.1, 1.1])
def test_walk_forward_rejects_missing_or_invalid_hit_rate(bad_hit_rate):
    frame = _training_frame(24)

    class FakeModel:
        def __init__(self, **_params):
            pass

        def train(self, _X_train, _y_train, X_val=None, _y_val=None):
            if X_val is None:
                return {}
            metrics = {"auc": 0.7}
            if bad_hit_rate is not None:
                metrics["hit_rate_top3"] = bad_hit_rate
            return metrics

    with pytest.raises(ValueError, match="hit_rate_top3"):
        train_with_walk_forward(frame, {}, model_class=FakeModel)


def test_local_weekday_retrain_converts_timeout_to_failure(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="train_xgb.py", timeout=1800)

    monkeypatch.setattr(retrain_weekday_models.subprocess, "run", timeout)

    assert asyncio.run(
        retrain_weekday_models.train_local("XSMN", "tay-ninh", 3)
    ) is False


def test_training_workflow_keeps_cli_and_allows_setup_overhead():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/05-train-model.yml"
    ).read_text(encoding="utf-8")

    assert "timeout-minutes: 45" in workflow
    assert "python src/scripts/train_xgb.py" in workflow
