"""Compact Telegram formatters for daily ensemble predictions."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Optional, Sequence


@dataclass(frozen=True)
class ShadowRow:
    """One optional shadow-predictor row in the compact message."""

    label: str
    top_pairs: Sequence[tuple[int, float]] = ()
    status: Optional[str] = None


def _format_numbers(numbers: Sequence[int]) -> str:
    return " • ".join(f"<code>{number:02d}</code>" for number in numbers)


def _format_shadow_row(
    label: str,
    top_pairs: Sequence[tuple[int, float]],
    status: str | None,
) -> str | None:
    """Format one shadow row without implying it participates in production."""
    safe_label = html.escape(label)
    normalized_status = (status or "").strip()
    if top_pairs:
        scored = " • ".join(
            f"<code>{number:02d}</code> ({score:.3f})"
            for number, score in top_pairs[:3]
        )
        suffix = f" • {html.escape(normalized_status)}" if normalized_status else ""
        return f"<b>{safe_label}:</b> {scored}{suffix}"
    if normalized_status:
        lowered = normalized_status.casefold()
        unavailable = any(
            marker in lowered
            for marker in ("không khả dụng", "lỗi", "error", "unavailable", "failed")
        )
        icon = "⚠️" if unavailable else "⏳"
        return f"<b>{safe_label}:</b> {icon} {html.escape(normalized_status)}"
    return None


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
    additional_shadows: Sequence[ShadowRow] = (),
) -> str:
    """Build one scan-friendly HTML message without verbose model traces."""
    if len(top_pairs) < 3:
        raise ValueError("compact ensemble message requires three predictions")
    if models_total <= 0 or not 0 <= models_active <= models_total:
        raise ValueError("model counts require 0 <= active <= total and total > 0")

    safe_region = html.escape(region.upper())
    safe_dow = html.escape(dow_label)
    lines = [f"🎯 <b>{safe_region} • {safe_dow}, {target_date:%d/%m/%Y}</b>"]

    labels = province_labels or {}
    if provinces:
        station_text = " • ".join(
            html.escape(labels.get(province, province)) for province in provinces
        )
        lines.append(f"📍 <b>Đài:</b> {station_text}")

    selected = list(top_pairs[:3])
    lines.extend(["", "🏆 <b>DỰ ĐOÁN CHÍNH</b>"])
    medals = ("🥇", "🥈", "🥉")
    lines.extend(
        f"{medal} <code>{number:02d}</code> — <b>{score:.3f}</b>"
        for medal, (number, score) in zip(medals, selected)
    )

    selected_numbers = {number for number, _ in selected}
    remaining = [number for number in remaining_candidates if number not in selected_numbers]
    if remaining:
        lines.append(
            f"📋 <b>Dự phòng:</b> {_format_numbers(remaining[:7])}"
        )

    shadow_rows = []
    primary_shadow = _format_shadow_row(
        shadow_label,
        shadow_top_pairs,
        shadow_status,
    )
    if primary_shadow:
        shadow_rows.append(primary_shadow)
    for shadow in additional_shadows:
        row = _format_shadow_row(shadow.label, shadow.top_pairs, shadow.status)
        if row:
            shadow_rows.append(row)

    if shadow_rows:
        lines.extend(["", "🧪 <b>SHADOW — CHỈ THAM KHẢO</b>"])
        for index, row in enumerate(shadow_rows):
            branch = "└" if index == len(shadow_rows) - 1 else "├"
            lines.append(f"{branch} {row}")

    selected_consensus = [number for number in consensus_pairs if number in selected_numbers]
    if selected_consensus:
        lines.extend(
            [
                "",
                f"🤝 <b>Đồng thuận:</b> {_format_numbers(selected_consensus)}",
            ]
        )

    has_reported_missing = any(
        bool(missing_models)
        for missing_models in (missing_by_scope or {}).values()
    )
    health_icon = (
        "✅" if models_active == models_total and not has_reported_missing else "⚙️"
    )
    lines.extend(
        [
            "",
            (
                f"{health_icon} <b>{html.escape(version)}</b> • "
                f"{models_active}/{models_total} model hoạt động"
            ),
        ]
    )

    weights = active_weights or {}
    if weights:
        names = model_labels or {}
        weight_text = " · ".join(
            f"{html.escape(names.get(name, name))} {weight:.2f}"
            for name, weight in weights.items()
        )
        lines.append(f"⚖️ <b>Trọng số:</b> {weight_text}")

    missing_parts = []
    for scope, missing_models in (missing_by_scope or {}).items():
        if missing_models:
            missing_parts.append(
                f"<b>{html.escape(labels.get(scope, scope))}:</b> "
                + ", ".join(html.escape(model) for model in missing_models)
            )
    if missing_parts:
        lines.append("⚠️ <b>Model chưa sẵn sàng</b>")
        for index, part in enumerate(missing_parts):
            branch = "└" if index == len(missing_parts) - 1 else "├"
            lines.append(f"{branch} {part}")

    return "\n".join(lines)
