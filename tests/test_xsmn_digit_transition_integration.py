from __future__ import annotations

from copy import deepcopy
from datetime import date
import asyncio
from pathlib import Path
import subprocess

import pytest

from src.scripts import predict_ensemble
from src.scripts.predict_xsmn_digit_transition import _resolve_provinces


def test_ddt_fault_boundary_cannot_mutate_or_replace_production_output(monkeypatch) -> None:
    production = {
        "top_pairs": [(12, 0.4), (34, 0.3), (56, 0.2)],
        "contributing_models": ["frequency@a"],
    }
    expected = deepcopy(production)

    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("ddt", 60)

    monkeypatch.setattr(predict_ensemble.subprocess, "run", fail)
    result = predict_ensemble._generate_ddt_shadow_safely(
        object(), ["vung-tau", "ben-tre"], date(2026, 7, 21)
    )

    assert result == {"status": "error", "reason": "shadow timeout after 60s"}
    assert production == expected


def test_ddt_subprocess_rejects_malformed_payload(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(["python"], 0, stdout="[]", stderr="")
    monkeypatch.setattr(predict_ensemble.subprocess, "run", lambda *_a, **_k: completed)

    result = predict_ensemble._generate_ddt_shadow_safely(
        object(), ["vung-tau", "ben-tre"], date(2026, 7, 21)
    )

    assert result["status"] == "error"
    assert "invalid payload" in result["reason"]


def test_ddt_shadow_row_preserves_probability_semantics() -> None:
    calibrated = predict_ensemble._ddt_shadow_row(
        {
            "status": "success",
            "score_semantics": "merged_pair_hit_probability_calibrated",
            "selected_evidence": [
                {"pair": 3, "probability": 0.31},
                {"pair": 12, "probability": 0.25},
                {"pair": 23, "probability": 0.20},
            ],
        }
    )
    uncalibrated = predict_ensemble._ddt_shadow_row(
        {
            "status": "success",
            "score_semantics": "merged_pair_hit_likelihood_uncalibrated",
            "selected_evidence": [
                {"pair": 3, "estimated_likelihood_uncalibrated": 0.31},
            ],
        }
    )

    assert calibrated.status == "calibrated"
    assert calibrated.top_pairs[0] == (3, 0.31)
    assert uncalibrated.status == "uncalibrated"


def test_cli_override_must_match_existing_xsmn_schedule() -> None:
    target = date(2026, 7, 22)

    assert _resolve_provinces(target, "dong-nai,can-tho") == (
        "dong-nai",
        "can-tho",
    )
    with pytest.raises(SystemExit, match="must match the schedule"):
        _resolve_provinces(target, "tp-hcm,dong-thap")


def test_ddt_runs_in_existing_prediction_workflow_without_new_cron() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/02-predict-ensemble.yml").read_text(
        encoding="utf-8"
    )
    orchestrator = (root / "src/scripts/predict_ensemble.py").read_text(
        encoding="utf-8"
    )

    assert "cron: '14 0 * * *'" in workflow
    assert workflow.count("python src/scripts/predict_ensemble.py") == 1
    assert "predict_xsmn_digit_transition.py" not in workflow
    assert orchestrator.index("compute_xsmn_merged_combo_selector_ensemble(") < orchestrator.index(
        "_generate_ddt_shadow_safely(db, provs_to_run, target_date)"
    )


def test_production_models_and_engines_do_not_depend_on_ddt() -> None:
    root = Path(__file__).resolve().parents[1]
    production_files = list((root / "src/xsmn_ensemble").glob("*.py"))
    production_files.extend((root / "src/xsmb_ensemble").glob("*.py"))

    assert all(
        "xsmn_digit_transition" not in path.read_text(encoding="utf-8")
        for path in production_files
    )


def test_real_xsmn_orchestration_persists_before_malformed_ddt(monkeypatch) -> None:
    events: list[str] = []

    async def model_results(*_args, **_kwargs):
        return [{"status": "success", "model_name": "frequency", "province": "a"}]

    async def send(*_args, **_kwargs):
        events.append("telegram")
        return True

    ensemble = {
        "top_pairs": [(12, 0.4), (34, 0.3), (56, 0.2)],
        "contributing_models": ["frequency@a"],
        "consensus_pairs": [],
        "active_weights": {},
        "top_candidates": [],
    }
    monkeypatch.setattr(predict_ensemble, "run_xsmn_models_for_target", model_results)
    monkeypatch.setattr(predict_ensemble, "get_recent_tails", lambda *_a, **_k: [])
    monkeypatch.setattr(predict_ensemble, "get_recent_province_tails", lambda *_a, **_k: {})
    monkeypatch.setattr(
        predict_ensemble,
        "get_recent_merged_tail_sets_for_province_pair",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        predict_ensemble,
        "compute_credibility_scores",
        lambda *_a, **_k: {"credibility_weights": {}, "scoring_log": ""},
    )
    monkeypatch.setattr(
        predict_ensemble,
        "compute_xsmn_merged_combo_selector_ensemble",
        lambda *_a, **_k: deepcopy(ensemble),
    )
    monkeypatch.setattr(
        predict_ensemble,
        "generate_shadow_prediction",
        lambda *_a, **_k: {"status": "insufficient_evidence"},
    )
    monkeypatch.setattr(
        predict_ensemble,
        "xsmn_format_ensemble_result",
        lambda *_a, **_k: {"top_pairs": [12, 34, 56]},
    )
    monkeypatch.setattr(
        predict_ensemble,
        "save_prediction",
        lambda *_a, **_k: events.append("production_saved"),
    )

    def malformed_ddt(*_args, **_kwargs):
        assert events == ["production_saved"]
        events.append("ddt")
        return {"status": "success", "selected_evidence": [{"bad": "payload"}]}

    monkeypatch.setattr(predict_ensemble, "_generate_ddt_shadow_safely", malformed_ddt)
    monkeypatch.setattr(predict_ensemble, "get_missing_models", lambda *_a, **_k: [])
    monkeypatch.setattr(
        predict_ensemble,
        "format_compact_ensemble_message",
        lambda **_kwargs: "message",
    )
    monkeypatch.setattr(predict_ensemble, "_send_chunked", send)

    asyncio.run(
        predict_ensemble.run_xsmn_ensemble(
            date(2026, 7, 22),
            ["dong-nai", "can-tho"],
            object(),
            object(),
            object(),
            "/tmp",
        )
    )

    assert events == ["production_saved", "ddt", "telegram"]
