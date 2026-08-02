"""Pure Telegram formatter for daily prediction verification results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
from typing import Any


MODEL_SHORT_NAMES = {
    "frequency": "Freq",
    "gap_overdue": "Gap",
    "markov": "Markov",
    "xgboost_core": "XGB",
    "xgboost_single": "XGB",
    "lstm_gru": "LSTM",
    "lstm": "LSTM",
    "bayesian": "Bayes",
    "cyclic": "Cyclic",
    "stats_freq_gap": "StatsFG",
    "chisquare_gof": "ChiGOF",
    "chisquare_independence": "ChiInd",
    "cdm": "CDM",
    "loto_statistical": "LotoStat",
}
SHADOW_LABELS = {
    "cmr_shadow": "CMR",
    "ddt_shadow": "DDT",
    "relationship": "Relationship",
    "xsmb_combo_shadow": "XSMB Combo v6",
}
SHADOW_ORDER = {
    "xsmb_combo_shadow": 0,
    "cmr_shadow": 1,
    "relationship": 2,
    "ddt_shadow": 3,
}

REGION_ORDER = {"XSMB": 0, "XSMN": 1}
MODEL_ORDER = {
    name: index
    for index, name in enumerate(
        (
            "frequency",
            "gap_overdue",
            "markov",
            "xgboost_core",
            "xgboost_single",
            "lstm",
            "lstm_gru",
            "bayesian",
            "cyclic",
            "stats_freq_gap",
            "chisquare_gof",
            "chisquare_independence",
            "cdm",
            "loto_statistical",
        )
    )
}


def _unique_pairs(values: Sequence[Any]) -> list[int]:
    """Return valid unique pairs in input order."""
    pairs: list[int] = []
    for value in values:
        try:
            pair = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= pair <= 99 and pair not in pairs:
            pairs.append(pair)
    return pairs


def _format_pairs(pairs: Sequence[int], separator: str = " | ") -> str:
    return separator.join(f"{pair:02d}" for pair in pairs)


def _format_pair_slots(values: Sequence[Any]) -> str:
    """Preserve stored Top-3 slots so invalid duplicates remain diagnosable."""
    slots: list[str] = []
    for value in list(values)[:3]:
        try:
            pair = int(value)
        except (TypeError, ValueError):
            slots.append("?")
            continue
        slots.append(f"{pair:02d}" if 0 <= pair <= 99 else "?")
    while len(slots) < 3:
        slots.append("?")
    return " | ".join(slots)


def _format_matches(matched: Sequence[int]) -> str:
    pairs = _unique_pairs(matched)
    return ", ".join(f"{pair:02d}" for pair in pairs) if pairs else "—"


def _scope(record: Mapping[str, Any]) -> str:
    return str(record.get("model_scope") or "single").lower()


def summarize_multi_results(
    results_summary: Sequence[Mapping[str, Any]],
) -> tuple[int, int, float]:
    """Return ensemble-only hits, total, and percentage for the headline."""
    ensemble_rows = [row for row in results_summary if _scope(row) == "ensemble"]
    total = len(ensemble_rows)
    hits = sum(bool(row.get("combo_hit", row.get("hit"))) for row in ensemble_rows)
    rate = hits / total * 100.0 if total else 0.0
    return hits, total, rate


def _region_key(region: str) -> tuple[int, str]:
    normalized = region.upper()
    return REGION_ORDER.get(normalized, len(REGION_ORDER)), normalized


def _model_key(model: Mapping[str, Any]) -> tuple[int, str]:
    name = str(model.get("model_name") or "")
    return MODEL_ORDER.get(name, len(MODEL_ORDER)), name


def _province_name(province: str, province_map: Mapping[str, str]) -> str:
    return escape(str(province_map.get(province, province)).title())


def _single_lines(
    region: str,
    sub_model_stats: Mapping[str, Sequence[Mapping[str, Any]]],
    province_map: Mapping[str, str],
) -> list[str]:
    """Render Single Model only when an XGBoost diagnostic row exists."""
    candidates: list[tuple[str, Mapping[str, Any]]] = []
    prefix = f"{region}/"
    for label in sorted(sub_model_stats):
        if not label.startswith(prefix):
            continue
        models = sub_model_stats[label]
        single = next(
            (
                model
                for model in models
                if model.get("model_name") == "xgboost_single"
            ),
            None,
        )
        if single is not None:
            candidates.append((label, single))

    if not candidates:
        return []

    lines = ["🤖 <b>Single Model [XGBoost v3]</b>"]
    for label, model in candidates:
        province = label.split("/", 1)[1]
        if region == "XSMB" or province.lower() == "all":
            display_name = "Top 3"
        else:
            display_name = _province_name(province, province_map)

        pairs = _unique_pairs(list(model.get("pairs") or []))[:3]
        matched_set = set(_unique_pairs(list(model.get("matched") or [])))
        visible = [pair for pair in pairs if pair in matched_set]
        if not pairs:
            lines.append(f"└ ⚪ {display_name}: không có dữ liệu")
            continue
        lines.append(
            f"└ 🔎 {display_name}: {_format_pairs(pairs)} → "
            f"{_format_matches(visible)} ({len(visible)}/3 khớp)"
        )
    return lines


def _ensemble_lines(
    records: Sequence[Mapping[str, Any]],
) -> list[str]:
    ensemble_rows = [row for row in records if _scope(row) == "ensemble"]
    if not ensemble_rows:
        return []

    lines = ["🤖 <b>Multi-Model</b>"]
    for record in sorted(
        ensemble_rows,
        key=lambda row: str(row.get("province") or "all"),
    ):
        pairs = _unique_pairs(list(record.get("pairs") or []))[:3]
        matched_set = set(_unique_pairs(list(record.get("matched") or [])))
        matched = [pair for pair in pairs if pair in matched_set]
        hit_count = int(record.get("hit_count", len(matched)))
        combo_hit = bool(record.get("combo_hit", record.get("hit", False)))
        validation_error = record.get("validation_error")
        icon = "🟢" if combo_hit else "🔴"
        if validation_error:
            verdict = "dữ liệu không hợp lệ"
        elif combo_hit:
            verdict = "TRÚNG"
        else:
            verdict = "chưa đạt" if hit_count else "TRƯỢT"
        pair_text = (
            _format_pair_slots(list(record.get("pairs") or []))
            if validation_error
            else _format_pairs(pairs) if pairs else "không có dữ liệu"
        )
        lines.append(
            f"└ {icon} Bộ 3: {pair_text} → {_format_matches(matched)} "
            f"({hit_count}/3 · {verdict})"
        )
    return lines


def _diagnostic_lines(
    region: str,
    sub_model_stats: Mapping[str, Sequence[Mapping[str, Any]]],
    province_map: Mapping[str, str],
    province_order: Sequence[str],
) -> list[str]:
    """Render any-hit sub-model diagnostics without presenting them as verdicts."""
    prefix = f"{region}/"
    province_positions = {
        province: index for index, province in enumerate(province_order)
    }

    def label_key(label: str) -> tuple[int, str]:
        province = label.split("/", 1)[1]
        return province_positions.get(province, len(province_positions)), province

    labels = [
        label
        for label in sorted(sub_model_stats, key=label_key)
        if label.startswith(prefix)
    ]
    if region == "XSMB":
        labels = [label for label in labels if label.split("/", 1)[1].lower() == "all"]
    else:
        labels = [label for label in labels if label.split("/", 1)[1].lower() != "all"]

    sections: list[str] = []
    for label in labels:
        models = [
            model
            for model in sub_model_stats[label]
            if model.get("model_name") != "xgboost_single"
        ]
        if not models:
            continue

        if region == "XSMN":
            province = label.split("/", 1)[1]
            sections.append(f"📍 <b>{_province_name(province, province_map)}</b>")

        display_limit = 3 if region == "XSMB" else 5
        for model in sorted(models, key=_model_key):
            name = str(model.get("model_name") or "unknown")
            display_name = escape(MODEL_SHORT_NAMES.get(name, name))
            pairs = _unique_pairs(list(model.get("pairs") or []))[:display_limit]
            if not pairs:
                sections.append(f"└ ⚪ {display_name}: không có dữ liệu")
                continue
            matched_set = set(_unique_pairs(list(model.get("matched") or [])))
            visible = [pair for pair in pairs if pair in matched_set]
            icon = "🟢" if visible else "🔴"
            pair_text = "[" + ", ".join(f"{pair:02d}" for pair in pairs) + "]"
            sections.append(
                f"└ {icon} {display_name}: {pair_text} → {_format_matches(visible)}"
            )

    if not sections:
        return []
    return [
        "🧪 <b>Chi tiết sub-model</b>",
        "<i>🟢 = có candidate khớp; không phải verdict Multi-Model.</i>",
        *sections,
    ]


def _shadow_lines(
    shadow_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Render shadow verification separately from production diagnostics."""
    if not shadow_results:
        return []
    lines = ["🧪 <b>SHADOW — ĐỐI CHIẾU</b>"]
    ordered = sorted(
        shadow_results,
        key=lambda row: (
            SHADOW_ORDER.get(str(row.get("model_name") or ""), 99),
            str(row.get("model_name") or ""),
        ),
    )
    for result in ordered:
        model_name = str(result.get("model_name") or "shadow")
        label = escape(SHADOW_LABELS.get(model_name, model_name))
        verification_status = result.get("verification_status")
        if verification_status == "pending_results":
            lines.append(f"└ ⏳ {label}: chờ kết quả xổ số")
            continue
        if verification_status == "no_prediction":
            status = str(result.get("status") or "error")
            reason = escape(" ".join(str(result.get("reason") or "").split())[:120])
            if status in {
                "insufficient",
                "insufficient_evidence",
                "insufficient_candidates",
                "insufficient_history",
            }:
                text = "chưa đủ dữ liệu"
                icon = "⏳"
            else:
                text = "không có Top 3 hợp lệ"
                icon = "⚠️"
            suffix = f" · {reason}" if reason else ""
            lines.append(f"└ {icon} {label}: {text}{suffix}")
            continue

        pairs = list(result.get("pairs") or [])
        validation_error = result.get("validation_error")
        pair_text = (
            _format_pair_slots(pairs)
            if validation_error
            else _format_pairs(_unique_pairs(pairs)[:3])
        )
        matched = _unique_pairs(list(result.get("matched") or []))
        hit_count = int(result.get("hit_count") or 0)
        combo_hit = bool(result.get("combo_hit"))
        icon = "🟢" if combo_hit else "🔴"
        verdict = "đạt shadow" if combo_hit else "chưa đạt shadow"
        if validation_error:
            verdict = "dữ liệu không hợp lệ"
        lines.append(
            f"└ {icon} {label}: {pair_text or 'không có dữ liệu'} → "
            f"{_format_matches(matched)} ({hit_count}/3 · {verdict})"
        )
    return lines


def format_verification_message(
    date_str: str,
    results_summary: Sequence[Mapping[str, Any]],
    sub_model_stats: Mapping[str, Sequence[Mapping[str, Any]]],
    province_map: Mapping[str, str] | None = None,
    province_order: Sequence[str] | None = None,
    shadow_results: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Build a deterministic HTML report from already verified data."""
    province_map = province_map or {}
    province_order = province_order or ()
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in results_summary:
        region = str(record.get("region") or "").upper()
        if region:
            grouped.setdefault(region, []).append(record)

    lines = [
        f"📊 <b>KẾT QUẢ DỰ ĐOÁN — {escape(date_str)}</b>",
        "<i>Điều kiện Multi-Model: đạt ít nhất 2/3 số.</i>",
    ]

    for region in sorted(grouped, key=_region_key):
        region_sections: list[list[str]] = []
        single = _single_lines(region, sub_model_stats, province_map)
        if single:
            region_sections.append(single)
        ensemble = _ensemble_lines(grouped[region])
        if ensemble:
            region_sections.append(ensemble)
        diagnostics = _diagnostic_lines(
            region,
            sub_model_stats,
            province_map,
            province_order,
        )
        if diagnostics:
            region_sections.append(diagnostics)

        lines.extend(["", f"📍 <b>{escape(region)}</b>"])
        for section in region_sections:
            lines.extend(section)

    shadow = _shadow_lines(shadow_results)
    if shadow:
        lines.extend(["", *shadow])

    hits, total, rate = summarize_multi_results(results_summary)
    footer = (
        f"📈 <b>Multi-Model đạt ≥2/3: {hits}/{total} ({rate:.0f}%)</b>"
        if total
        else "📈 <b>Multi-Model: không có dữ liệu để đánh giá</b>"
    )
    lines.extend(["", footer])
    return "\n".join(lines)
