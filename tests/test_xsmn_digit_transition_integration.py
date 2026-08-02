from __future__ import annotations

from copy import deepcopy
from datetime import date
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.scripts import predict_ensemble
from src.scripts import predict_xsmn_digit_transition as ddt_cli
from src.scripts.predict_xsmn_digit_transition import _resolve_provinces


def test_ddt_persisted_read_cannot_mutate_or_replace_production_output(monkeypatch) -> None:
    production = {
        "top_pairs": [(12, 0.4), (34, 0.3), (56, 0.2)],
        "contributing_models": ["frequency@a"],
    }
    expected = deepcopy(production)

    monkeypatch.setattr(
        predict_ensemble,
        "get_shadow_prediction",
        lambda *_args: {
            "status": "success",
            "pair_1": 3,
            "pair_2": 12,
            "pair_3": 25,
            "score_1": 0.3,
            "score_2": 0.2,
            "score_3": 0.1,
        },
    )
    result = predict_ensemble._generate_ddt_shadow_safely(
        object(), ["vung-tau", "ben-tre"], date(2026, 7, 21)
    )

    assert result["pair_1"] == 3
    assert production == expected


def test_ddt_persisted_read_failure_is_nonblocking(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(predict_ensemble, "get_shadow_prediction", fail)

    result = predict_ensemble._generate_ddt_shadow_safely(
        object(), ["vung-tau", "ben-tre"], date(2026, 7, 21)
    )

    assert result["status"] == "error"
    assert "database unavailable" in result["reason"]


def test_ddt_persisted_scope_mismatch_is_not_rendered(monkeypatch) -> None:
    monkeypatch.setattr(
        predict_ensemble,
        "get_shadow_prediction",
        lambda *_args: {
            "status": "success",
            "run_metadata": {"provinces": ["tp-hcm", "long-an"]},
        },
    )

    result = predict_ensemble._generate_ddt_shadow_safely(
        object(),
        ["tien-giang", "kien-giang"],
        date(2026, 7, 26),
    )

    assert result == {
        "status": "error",
        "reason": "persisted_ddt_scope_mismatch",
    }


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
    with pytest.raises(ValueError, match="must match the schedule"):
        _resolve_provinces(target, "tp-hcm,dong-thap")


def test_cli_schedule_ignores_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_PROVINCES", "tp-hcm,dong-thap")

    assert _resolve_provinces(
        date(2026, 7, 26),
        "tien-giang,kien-giang",
    ) == ("tien-giang", "kien-giang")


@pytest.mark.parametrize(
    ("status", "expected_rc"),
    [
        ("success", 0),
        ("uncalibrated", 0),
        ("insufficient_evidence", 2),
    ],
)
def test_cli_stdout_is_one_json_contract(
    monkeypatch,
    capsys,
    status: str,
    expected_rc: int,
) -> None:
    monkeypatch.setattr(
        ddt_cli,
        "_parse_args",
        lambda: SimpleNamespace(
            target_date="2026-07-22",
            provinces="dong-nai,can-tho",
            output=None,
            min_transitions=12,
            top_k_states=32,
        ),
    )
    monkeypatch.setattr(ddt_cli, "LotteryDB", lambda: object())
    monkeypatch.setattr(
        ddt_cli,
        "generate_shadow_prediction",
        lambda *_a, **_k: {"status": status, "reason": "short"},
    )

    assert ddt_cli.main() == expected_rc
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert json.loads(output)["status"] == status


def test_cli_exception_is_sanitized_json_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        ddt_cli,
        "_parse_args",
        lambda: SimpleNamespace(
            target_date="2026-07-22",
            provinces="dong-nai,can-tho",
            output=None,
            min_transitions=12,
            top_k_states=32,
        ),
    )
    monkeypatch.setattr(ddt_cli, "LotteryDB", lambda: object())
    monkeypatch.setattr(
        ddt_cli,
        "generate_shadow_prediction",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("line one\nline two")),
    )

    assert ddt_cli.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "error", "reason": "line one line two"}


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
    assert "subprocess.run(" not in orchestrator
    assert "get_shadow_prediction(db, \"ddt_shadow\"" in orchestrator


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
    rendered: dict = {}

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
        "generate_relationship_shadow",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("relationship unavailable")
        ),
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
    def save_shadow(_db, record):
        events.append(f"{record['model_name']}_saved")
        return record["model_name"] != "relationship"

    monkeypatch.setattr(predict_ensemble, "save_shadow_prediction", save_shadow)
    monkeypatch.setattr(
        predict_ensemble,
        "get_shadow_prediction",
        lambda _db, model_name, *_a, **_k: (
            {
                "status": "success",
                "pair_1": 11,
                "pair_2": 25,
                "pair_3": 3,
                "run_metadata": {
                    "provinces": ["dong-nai", "can-tho"],
                    "selected_combo": {"relationship_score": 0.6123},
                },
            }
            if model_name == "relationship"
            else None
        ),
    )

    def malformed_ddt(*_args, **_kwargs):
        assert events == [
            "production_saved",
            "cmr_shadow_saved",
            "relationship_saved",
        ]
        events.append("ddt")
        return {"status": "success", "selected_evidence": [{"bad": "payload"}]}

    monkeypatch.setattr(predict_ensemble, "_generate_ddt_shadow_safely", malformed_ddt)
    monkeypatch.setattr(predict_ensemble, "get_missing_models", lambda *_a, **_k: [])
    def format_message(**kwargs):
        rendered.update(kwargs)
        return "message"

    monkeypatch.setattr(
        predict_ensemble,
        "format_compact_ensemble_message",
        format_message,
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

    assert events == [
        "production_saved",
        "cmr_shadow_saved",
        "relationship_saved",
        "ddt",
        "telegram",
    ]
    relationship_row = rendered["additional_shadows"][0]
    assert tuple(relationship_row.numbers) == (11, 25, 3)
    assert relationship_row.aggregate_score == pytest.approx(0.6123)
