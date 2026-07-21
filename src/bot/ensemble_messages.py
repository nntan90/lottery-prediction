"""Compact Telegram formatters for daily ensemble predictions."""

from __future__ import annotations

import html
from datetime import date
from typing import Mapping, Sequence


def _format_numbers(numbers: Sequence[int]) -> str:
    return " ".join(f"<code>{number:02d}</code>" for number in numbers)


def format_compact_ensemble_message(
    *,
    region: str,
    target_date: date,
    dow_label: str,
    top_pairs: Sequence[tuple[int, float]],
    models_active: int,
    models_total: int,
    version: str,
    provinces: Sequence[str] = (),
    province_labels: Mapping[str, str] | None = None,
    consensus_pairs: Sequence[int] = (),
    remaining_candidates: Sequence[int] = (),
    active_weights: Mapping[str, float] | None = None,
    model_labels: Mapping[str, str] | None = None,
    missing_by_scope: Mapping[str, Sequence[str]] | None = None,
    shadow_label: str = "CMR shadow",
    shadow_top_pairs: Sequence[tuple[int, float]] = (),
    shadow_status: str | None = None,
) -> str:
    """Build one scan-friendly HTML message without verbose model traces."""
    if len(top_pairs) < 3:
        raise ValueError("compact ensemble message requires three predictions")

    safe_region = html.escape(region.upper())
    safe_dow = html.escape(dow_label)
    lines = [
        f"🎯 <b>{safe_region} • {target_date:%d/%m/%Y} ({safe_dow})</b>",
    ]

    labels = province_labels or {}
    if provinces:
        station_text = " + ".join(
            html.escape(labels.get(province, province)) for province in provinces
        )
        lines.append(f"📍 {station_text}")

    selected = list(top_pairs[:3])
    top_text = " - ".join(
        f"<code>{number:02d}</code> ({score:.3f})" for number, score in selected
    )
    lines.append(f"<b>Top 3:</b> {top_text}")

    selected_numbers = {number for number, _ in selected}
    remaining = [number for number in remaining_candidates if number not in selected_numbers]
    if remaining:
        lines.append(
            f"<b>Các ứng cử viên còn lại:</b> {_format_numbers(remaining[:7])}"
        )

    if shadow_top_pairs:
        shadow_text = " - ".join(
            f"<code>{number:02d}</code> ({score:.3f})"
            for number, score in shadow_top_pairs[:3]
        )
        lines.append(f"🧪 <b>{html.escape(shadow_label)}:</b> {shadow_text}")
    elif shadow_status:
        lines.append(
            f"🧪 <b>{html.escape(shadow_label)}:</b> {html.escape(shadow_status)}"
        )

    selected_consensus = [number for number in consensus_pairs if number in selected_numbers]
    if selected_consensus:
        lines.append(f"🤝 Đồng thuận: {_format_numbers(selected_consensus)}")

    lines.append(
        f"⚙️ {html.escape(version)} • Models {models_active}/{models_total}"
    )

    weights = active_weights or {}
    if weights:
        names = model_labels or {}
        weight_text = " · ".join(
            f"{html.escape(names.get(name, name))} {weight:.2f}"
            for name, weight in weights.items()
        )
        lines.append(f"⚖️ {weight_text}")

    missing_parts = []
    for scope, missing_models in (missing_by_scope or {}).items():
        if missing_models:
            missing_parts.append(
                f"{html.escape(labels.get(scope, scope))}: "
                + ", ".join(html.escape(model) for model in missing_models)
            )
    if missing_parts:
        lines.append(f"⚠️ Thiếu: {' | '.join(missing_parts)}")

    return "\n".join(lines)
