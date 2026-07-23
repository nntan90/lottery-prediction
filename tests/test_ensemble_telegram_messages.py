from datetime import date

import pytest

from src.bot.ensemble_messages import ShadowRow, format_compact_ensemble_message


def test_xsmn_message_is_compact_and_complete() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 21),
        dow_label="Thứ Ba",
        provinces=("vung-tau", "ben-tre"),
        province_labels={"vung-tau": "Vũng Tàu", "ben-tre": "Bến Tre"},
        top_pairs=((73, 0.4231), (1, 0.4171), (89, 0.4034)),
        consensus_pairs=(73, 44),
        remaining_candidates=(73, 20, 30, 40, 50, 60, 70, 80, 90),
        models_active=11,
        models_total=12,
        version="Ensemble v3.5",
        active_weights={"frequency": 1 / 6, "markov": 1 / 6},
        model_labels={"frequency": "Freq", "markov": "Markov"},
        missing_by_scope={"vung-tau": ("LSTM",)},
        shadow_top_pairs=((9, 0.3912), (28, 0.3821), (47, 0.3754)),
    )

    assert "XSMN • 21/07/2026 (Thứ Ba)" in message
    assert "Vũng Tàu + Bến Tre" in message
    assert "<code>73</code> (0.423) - <code>01</code> (0.417) - <code>89</code> (0.403)" in message
    assert "Các ứng cử viên còn lại:</b> <code>20</code>" in message
    assert "<code>90</code>" not in message
    assert "CMR shadow:</b> <code>09</code> (0.391)" in message
    assert "Models 11/12" in message
    assert "Thiếu: Vũng Tàu: LSTM" in message
    assert "Top 5 theo từng model" not in message
    assert len(message) < 600


def test_message_escapes_dynamic_labels() -> None:
    message = format_compact_ensemble_message(
        region="XSMB<script>",
        target_date=date(2026, 7, 21),
        dow_label="Thứ Ba",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=3,
        models_total=5,
        version="v5.1 & stable",
    )

    assert "<script>" not in message
    assert "&lt;SCRIPT&gt;" in message
    assert "v5.1 &amp; stable" in message


def test_message_requires_exact_top_three_surface() -> None:
    with pytest.raises(ValueError, match="requires three predictions"):
        format_compact_ensemble_message(
            region="XSMB",
            target_date=date(2026, 7, 21),
            dow_label="Thứ Ba",
            top_pairs=((1, 1.0),),
            models_active=1,
            models_total=5,
            version="Ensemble v5.1",
        )


def test_message_shows_cmr_insufficient_evidence_without_changing_top_three() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 21),
        dow_label="Thứ Ba",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        shadow_status="Chưa đủ dữ liệu",
    )

    assert "Top 3:</b> <code>01</code> (1.000)" in message
    assert "CMR shadow:</b> Chưa đủ dữ liệu" in message


def test_additional_ddt_shadow_coexists_with_unchanged_cmr_and_production_top_three() -> None:
    kwargs = {
        "region": "XSMN",
        "target_date": date(2026, 7, 21),
        "dow_label": "Thứ Ba",
        "top_pairs": ((1, 1.0), (2, 0.9), (3, 0.8)),
        "models_active": 12,
        "models_total": 12,
        "version": "Ensemble v3.5",
        "shadow_top_pairs": ((9, 0.3912), (28, 0.3821), (47, 0.3754)),
    }

    existing_message = format_compact_ensemble_message(**kwargs)
    message = format_compact_ensemble_message(
        **kwargs,
        additional_shadows=(
            ShadowRow(
                label="DDT shadow",
                top_pairs=((3, 0.3012), (12, 0.1821), (25, 0.1454), (99, 0.1)),
            ),
        ),
    )

    existing_lines = existing_message.splitlines()
    message_lines_without_ddt = [
        line for line in message.splitlines() if "DDT shadow" not in line
    ]
    assert message_lines_without_ddt == existing_lines
    assert "<b>Top 3:</b> <code>01</code> (1.000) - <code>02</code> (0.900) - <code>03</code> (0.800)" in message
    assert "CMR shadow:</b> <code>09</code> (0.391) - <code>28</code> (0.382) - <code>47</code> (0.375)" in message
    assert "DDT shadow:</b> <code>03</code> (0.301) - <code>12</code> (0.182) - <code>25</code> (0.145)" in message
    assert "<code>99</code> (0.100)" not in message


def test_additional_shadow_escapes_label_and_status() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 21),
        dow_label="Thứ Ba",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        additional_shadows=(
            ShadowRow(
                label="DDT <shadow>",
                top_pairs=((7, 0.3),),
                status="Ready & <calibrated>",
            ),
            ShadowRow(label="PDA & status", status="Waiting <history>"),
        ),
    )

    assert "DDT &lt;shadow&gt;:</b> <code>07</code> (0.300) • Ready &amp; &lt;calibrated&gt;" in message
    assert "PDA &amp; status:</b> Waiting &lt;history&gt;" in message
    assert "<shadow>" not in message
    assert "<calibrated>" not in message
    assert "<history>" not in message
