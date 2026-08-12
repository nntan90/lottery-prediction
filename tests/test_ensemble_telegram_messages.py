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

    assert "XSMN • Thứ Ba, 21/07/2026" in message
    assert "Đài:</b> Vũng Tàu • Bến Tre" in message
    assert "🥇 <code>73</code> — <b>0.423</b>" in message
    assert "🥈 <code>01</code> — <b>0.417</b>" in message
    assert "🥉 <code>89</code> — <b>0.403</b>" in message
    assert "Dự phòng:</b> <code>20</code>" in message
    assert "<code>90</code>" not in message
    assert "SHADOW — CHỈ THAM KHẢO" in message
    assert "CMR shadow:</b> <code>09</code> (0.391)" in message
    assert "11/12 model hoạt động" in message
    assert "Model chưa sẵn sàng" in message
    assert "Vũng Tàu:</b> LSTM" in message
    assert "Top 5 theo từng model" not in message
    assert len(message) < 800


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


@pytest.mark.parametrize(
    ("active", "total"),
    ((0, 0), (-1, 12), (13, 12)),
)
def test_message_rejects_invalid_model_health_counts(active: int, total: int) -> None:
    with pytest.raises(ValueError, match="model counts"):
        format_compact_ensemble_message(
            region="XSMN",
            target_date=date(2026, 7, 21),
            dow_label="Thứ Ba",
            top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
            models_active=active,
            models_total=total,
            version="Ensemble v3.5",
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

    assert "🥇 <code>01</code> — <b>1.000</b>" in message
    assert "CMR shadow:</b> ⏳ Chưa đủ dữ liệu" in message


def test_whitespace_only_shadow_status_is_not_rendered() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 21),
        dow_label="Thứ Ba",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        shadow_status="   ",
    )

    assert "SHADOW" not in message


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

    def normalize_tree_connector(line: str) -> str:
        return line[2:] if line.startswith(("├ ", "└ ")) else line

    existing_lines = [
        normalize_tree_connector(line) for line in existing_message.splitlines()
    ]
    message_lines_without_ddt = [
        normalize_tree_connector(line)
        for line in message.splitlines()
        if "DDT shadow" not in line
    ]
    assert message_lines_without_ddt == existing_lines
    assert "🥇 <code>01</code> — <b>1.000</b>" in message
    assert "CMR shadow:</b> <code>09</code> (0.391) • <code>28</code> (0.382) • <code>47</code> (0.375)" in message
    assert "DDT shadow:</b> <code>03</code> (0.301) • <code>12</code> (0.182) • <code>25</code> (0.145)" in message
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
    assert "PDA &amp; status:</b> ⏳ Waiting &lt;history&gt;" in message
    assert "<shadow>" not in message
    assert "<calibrated>" not in message
    assert "<history>" not in message


def test_llm_gen_shadow_names_the_selected_provider_without_probability_wording() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 8, 4),
        dow_label="Thứ Ba",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        additional_shadows=(
            ShadowRow(
                label="LLM_Gen [GPT-5.6 Sol]",
                top_pairs=((12, 0.91), (25, 0.72), (38, 0.68)),
                status="điểm xếp hạng chưa calibration",
            ),
        ),
    )

    assert "LLM_Gen [GPT-5.6 Sol]" in message
    assert "<code>12</code> (0.910)" in message
    assert "xác suất" not in message.casefold()


def test_llm_gen_agentrouter_shadow_names_current_model_and_chat_backend() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 8, 12),
        dow_label="Thứ Tư",
        top_pairs=((1, 1.0), (2, 0.9), (3, 0.8)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        additional_shadows=(
            ShadowRow(
                label="LLM_Gen [AgentRouter · GPT-5.5]",
                top_pairs=((12, 0.91), (25, 0.72), (38, 0.68)),
                status="điểm xếp hạng chưa calibration",
            ),
        ),
    )

    assert "LLM_Gen [AgentRouter · GPT-5.5]" in message
    assert "<code>12</code> (0.910)" in message
    assert "xác suất" not in message.casefold()


def test_xsmn_friday_message_has_clear_production_shadow_and_health_sections() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 24),
        dow_label="Thứ Sáu",
        provinces=("vinh-long", "binh-duong"),
        province_labels={
            "vinh-long": "Vĩnh Long",
            "binh-duong": "Bình Dương",
        },
        top_pairs=((22, 1.392), (21, 1.384), (8, 1.278)),
        consensus_pairs=(22, 21),
        remaining_candidates=(96, 90, 50, 23, 1, 99, 6),
        models_active=10,
        models_total=12,
        version="Ensemble v3.5",
        missing_by_scope={
            "vinh-long": ("LSTM",),
            "binh-duong": ("LSTM",),
        },
        shadow_top_pairs=((82, 0.451), (49, 0.450), (34, 0.425)),
        additional_shadows=(
            ShadowRow(label="DDT shadow", status="Tạm không khả dụng"),
        ),
    )

    assert message == (
        "🎯 <b>XSMN • Thứ Sáu, 24/07/2026</b>\n"
        "📍 <b>Đài:</b> Vĩnh Long • Bình Dương\n"
        "\n"
        "🏆 <b>DỰ ĐOÁN CHÍNH</b>\n"
        "🥇 <code>22</code> — <b>1.392</b>\n"
        "🥈 <code>21</code> — <b>1.384</b>\n"
        "🥉 <code>08</code> — <b>1.278</b>\n"
        "📋 <b>Dự phòng:</b> <code>96</code> • <code>90</code> • "
        "<code>50</code> • <code>23</code> • <code>01</code> • "
        "<code>99</code> • <code>06</code>\n"
        "\n"
        "🧪 <b>SHADOW — CHỈ THAM KHẢO</b>\n"
        "├ <b>CMR shadow:</b> <code>82</code> (0.451) • "
        "<code>49</code> (0.450) • <code>34</code> (0.425)\n"
        "└ <b>DDT shadow:</b> ⚠️ Tạm không khả dụng\n"
        "\n"
        "🤝 <b>Đồng thuận:</b> <code>22</code> • <code>21</code>\n"
        "\n"
        "⚙️ <b>Ensemble v3.5</b> • 10/12 model hoạt động\n"
        "⚠️ <b>Model chưa sẵn sàng</b>\n"
        "├ <b>Vĩnh Long:</b> LSTM\n"
        "└ <b>Bình Dương:</b> LSTM"
    )


def test_complete_count_with_reported_missing_model_is_not_green() -> None:
    message = format_compact_ensemble_message(
        region="XSMN",
        target_date=date(2026, 7, 24),
        dow_label="Thứ Sáu",
        top_pairs=((22, 1.392), (21, 1.384), (8, 1.278)),
        models_active=12,
        models_total=12,
        version="Ensemble v3.5",
        missing_by_scope={"vinh-long": ("LSTM",)},
    )

    assert "⚙️ <b>Ensemble v3.5</b> • 12/12 model hoạt động" in message
    assert "✅ <b>Ensemble v3.5</b>" not in message


def test_xsmb_layout_has_no_station_or_shadow_section() -> None:
    message = format_compact_ensemble_message(
        region="XSMB",
        target_date=date(2026, 7, 24),
        dow_label="Thứ Sáu",
        top_pairs=((22, 1.392), (21, 1.384), (8, 1.278)),
        remaining_candidates=(96, 90),
        models_active=5,
        models_total=5,
        version="Ensemble v5.1",
        active_weights={"frequency": 0.2},
        model_labels={"frequency": "Freq"},
    )

    assert "🎯 <b>XSMB • Thứ Sáu, 24/07/2026</b>" in message
    assert "✅ <b>Ensemble v5.1</b> • 5/5 model hoạt động" in message
    assert "⚖️ <b>Trọng số:</b> Freq 0.20" in message
    assert "📍" not in message
    assert "SHADOW" not in message


def test_xsmb_combo_shadow_shows_one_uncalibrated_aggregate_score() -> None:
    message = format_compact_ensemble_message(
        region="XSMB",
        target_date=date(2026, 7, 28),
        dow_label="Thứ Ba",
        top_pairs=((60, 0.138), (83, 0.103), (52, 0.081)),
        models_active=5,
        models_total=5,
        version="Ensemble v5.1",
        additional_shadows=(
            ShadowRow(
                label="Combo v6 shadow",
                numbers=(12, 34, 56),
                aggregate_score=0.184321,
                aggregate_label="điểm tổ hợp chưa calibration",
                status="không thay production",
            ),
        ),
    )

    assert "<code>12</code> • <code>34</code> • <code>56</code>" in message
    assert "điểm tổ hợp chưa calibration: <b>0.1843</b>" in message
    assert message.count("0.1843") == 1
    assert "xác suất" not in message.casefold()
