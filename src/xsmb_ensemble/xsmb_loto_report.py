from datetime import date
from typing import Dict, List


def format_loto_report_telegram(report: Dict, target_date: date) -> List[str]:
    """
    Format XSMB loto report as one compact Telegram message.

    The analyzer still computes all detailed sections; Telegram receives the
    strongest signals only to avoid three long XSMB messages every day.
    """
    date_str = target_date.strftime("%d/%m/%Y")
    lines = [
        "🎯 <b>BÁO CÁO LÔ TÔ XSMB</b>",
        f"📅 <b>Ngày: {date_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    hot = report.get("hot_numbers", {})
    hot_pairs = hot.get("pairs", [])
    if hot_pairs:
        hot_text = ", ".join(
            f"<code>{p:02d}</code>({c}l-{freq}%)"
            for p, c, freq in hot_pairs[:5]
        )
        lines.append(f"🔥 <b>Nóng 30 ngày:</b> {hot_text}")

    overdue = report.get("overdue_numbers", {})
    overdue_pairs = overdue.get("pairs", [])
    if overdue_pairs:
        gan_text = ", ".join(
            f"<code>{p:02d}</code>({gap}n)"
            for p, gap, _ in overdue_pairs[:5]
        )
        lines.append(f"🕳️ <b>Gan:</b> {gan_text}")

    falling = report.get("falling_numbers", {})
    fall_1 = [(p, prob) for p, prob in falling.get("fall_1day_probs", []) if prob > 0]
    fall_2 = [(p, prob) for p, prob in falling.get("fall_2day_probs", []) if prob > 0]
    if fall_1 or fall_2:
        fall_parts = []
        if fall_1:
            fall_parts.append(
                "1 ngày: " + ", ".join(f"<code>{p:02d}</code>({prob}%)" for p, prob in fall_1[:3])
            )
        if fall_2:
            fall_parts.append(
                "2 ngày: " + ", ".join(f"<code>{p:02d}</code>({prob}%)" for p, prob in fall_2[:3])
            )
        lines.append(f"🔻 <b>Lô rơi:</b> {' | '.join(fall_parts)}")

    doubles = report.get("doubles", {})
    overdue_doubles = [
        f"<code>{p:02d}</code>({gap}n)"
        for p, gap, _avg, is_overdue in doubles.get("doubles_status", [])
        if is_overdue
    ]
    recent_doubles = [
        f"<code>{p:02d}</code>"
        for p, gap, _avg, _is_overdue in doubles.get("doubles_status", [])
        if gap <= 2
    ]
    if overdue_doubles or recent_doubles:
        parts = []
        if overdue_doubles:
            parts.append(f"gan: {', '.join(overdue_doubles[:5])}")
        if recent_doubles:
            parts.append(f"vừa ra: {', '.join(recent_doubles[:5])}")
        lines.append(f"🎲 <b>Kép:</b> {' | '.join(parts)}")

    head_tail = report.get("head_tail", {})
    if head_tail.get("strong_heads"):
        lines.append(
            "🔢 <b>Đầu/đuôi mạnh:</b> "
            f"đầu {', '.join(map(str, head_tail['strong_heads']))} | "
            f"đuôi {', '.join(map(str, head_tail['strong_tails']))}"
        )

    sum_touch = report.get("sum_touch", {})
    if sum_touch.get("strong_sums"):
        lines.append(
            "🎯 <b>Tổng/chạm:</b> "
            f"tổng {', '.join(map(str, sum_touch['strong_sums']))} | "
            f"chạm {', '.join(map(str, sum_touch['strong_touches']))}"
        )

    reverse = report.get("reverse_pairs", {})
    reverse_pairs = reverse.get("active_reverse_pairs", [])
    if reverse_pairs:
        rev_text = ", ".join(
            f"<code>{p_a:02d}</code>-<code>{p_b:02d}</code>"
            for p_a, p_b, _g_a, _g_b, _reason in reverse_pairs[:3]
        )
        lines.append(f"🔀 <b>Cặp lộn:</b> {rev_text}")

    xien = report.get("xien", {})
    xien_pairs = xien.get("xien_same_head", []) + xien.get("xien_same_tail", [])
    if xien_pairs:
        xien_text = ", ".join(
            f"<code>{p_a:02d}</code>x<code>{p_b:02d}</code>"
            for p_a, p_b, _reason in xien_pairs[:3]
        )
        lines.append(f"✂️ <b>Xiên:</b> {xien_text}")

    dan = report.get("dan_de", {})
    if dan.get("top_3"):
        top_3 = ", ".join(f"<code>{p:02d}</code>" for p in dan["top_3"])
        lines.append(f"📋 <b>Top 3 VIP:</b> {top_3}")

    return ["\n".join(lines)]
