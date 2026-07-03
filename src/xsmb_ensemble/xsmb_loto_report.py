from datetime import date
from typing import Dict, List

def format_loto_report_telegram(report: Dict, target_date: date) -> List[str]:
    """
    Chuyển dict báo cáo từ XSMBLotoAnalyzer thành list chuỗi HTML.
    Chia thành 2 tin nhắn để tránh giới hạn 4096 ký tự của Telegram.
    
    Tin nhắn 1: Báo cáo Chính (Lô Nóng, Lô Gan, Lô Rơi, Kép)
    Tin nhắn 2: Phân Tích Nâng Cao (Đầu/Đuôi, Xiên, Dàn đề)
    """
    messages = []
    
    date_str = target_date.strftime("%d/%m/%Y")
    
    # ─── TIN NHẮN 1: BÁO CÁO CHÍNH ───
    msg1 = f"🎯 <b>BÁO CÁO PHÂN TÍCH LÔ TÔ XSMB</b>\n"
    msg1 += f"📅 <b>Ngày: {date_str}</b>\n"
    msg1 += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 1. Lô Nóng
    hot = report.get("hot_numbers", {})
    if hot.get("pairs"):
        window = hot.get("window_size", 30)
        msg1 += f"🔥 <b>LÔ NÓNG (Top 10 ra nhiều {window} ngày)</b>\n"
        hot_str = ", ".join([f"<code>{p:02d}</code>({c}lần-{f}%)" for p, c, f in hot["pairs"][:5]])
        msg1 += f"  • Top 1-5: {hot_str}\n"
        if len(hot["pairs"]) > 5:
            hot_str_2 = ", ".join([f"<code>{p:02d}</code>({c}lần)" for p, c, f in hot["pairs"][5:10]])
            msg1 += f"  • Top 6-10: {hot_str_2}\n"
        msg1 += "\n"
        
    # 2. Lô Gan
    overdue = report.get("overdue_numbers", {})
    if overdue.get("pairs"):
        msg1 += f"🕳️ <b>LÔ GAN (Top 10 lâu chưa về)</b>\n"
        ov_str = ", ".join([f"<code>{p:02d}</code>({g} ngày)" for p, g, _ in overdue["pairs"][:5]])
        msg1 += f"  • Max Gan: {ov_str}\n"
        if len(overdue["pairs"]) > 5:
            ov_str_2 = ", ".join([f"<code>{p:02d}</code>({g}n)" for p, g, _ in overdue["pairs"][5:10]])
            msg1 += f"  • Gan tiếp: {ov_str_2}\n"
        msg1 += "\n"
        
    # 3. Lô Rơi
    falling = report.get("falling_numbers", {})
    if falling.get("yesterday_pairs"):
        msg1 += f"🔻 <b>LÔ RƠI (Dự đoán từ kết quả hôm qua)</b>\n"
        y_pairs = ", ".join([f"{p:02d}" for p in falling["yesterday_pairs"][:15]])
        msg1 += f"  • Các số ra hôm qua: {y_pairs}...\n"
        
        f1_str = ", ".join([f"<code>{p:02d}</code>({prob}%)" for p, prob in falling.get("fall_1day_probs", [])[:3] if prob > 0])
        f2_str = ", ".join([f"<code>{p:02d}</code>({prob}%)" for p, prob in falling.get("fall_2day_probs", [])[:3] if prob > 0])
        
        if f1_str:
            msg1 += f"  • XS rơi lại 1 ngày cao: {f1_str}\n"
        if f2_str:
            msg1 += f"  • XS rơi lại 2 ngày cao: {f2_str}\n"
        msg1 += "\n"
        
    # 4. Lô Kép
    doubles = report.get("doubles", {})
    if doubles.get("doubles_status"):
        msg1 += f"🎲 <b>LÔ KÉP (00-99)</b>\n"
        overdue_doubles = [f"<code>{p:02d}</code>({g}n)" for p, g, avg, is_ov in doubles["doubles_status"] if is_ov]
        if overdue_doubles:
            msg1 += f"  • Kép đang gan: {', '.join(overdue_doubles)}\n"
        
        recent_doubles = [f"{p:02d}" for p, g, avg, is_ov in doubles["doubles_status"] if g <= 2]
        if recent_doubles:
            msg1 += f"  • Kép vừa ra (≤2 ngày): {', '.join(recent_doubles)}\n"
        
    messages.append(msg1.strip())
    
    # ─── TIN NHẮN 2: PHÂN TÍCH NÂNG CAO ───
    msg2 = f"🔍 <b>PHÂN TÍCH NÂNG CAO XSMB ({date_str})</b>\n"
    msg2 += f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # 5. Đầu/Đuôi Mạnh
    ht = report.get("head_tail", {})
    if ht.get("strong_heads"):
        msg2 += f"🔢 <b>ĐẦU - ĐUÔI ({ht.get('window_size', 30)} ngày)</b>\n"
        msg2 += f"  • Đầu mạnh: {', '.join(map(str, ht['strong_heads']))}  |  Đuôi mạnh: {', '.join(map(str, ht['strong_tails']))}\n"
        msg2 += f"  • Đầu yếu: {', '.join(map(str, ht['weak_heads']))}  |  Đuôi yếu: {', '.join(map(str, ht['weak_tails']))}\n"
        msg2 += "\n"
        
    # 6. Cặp Lộn
    rev = report.get("reverse_pairs", {})
    if rev.get("active_reverse_pairs"):
        msg2 += f"🔀 <b>CẶP LỘN TIỀM NĂNG</b>\n"
        for p_a, p_b, g_a, g_b, reason in rev["active_reverse_pairs"][:3]:
            msg2 += f"  • <code>{p_a:02d}</code> - <code>{p_b:02d}</code>: {reason}\n"
        msg2 += "\n"
        
    # 7. Tổng / Chạm
    st = report.get("sum_touch", {})
    if st.get("strong_sums"):
        msg2 += f"🎯 <b>TỔNG - CHẠM MẠNH ({st.get('window_size', 30)} ngày)</b>\n"
        msg2 += f"  • Tổng mạnh nhất: {', '.join(map(str, st['strong_sums']))}\n"
        msg2 += f"  • Chạm ra nhiều nhất: {', '.join(map(str, st['strong_touches']))}\n"
        msg2 += "\n"
        
    # 8. Xiên Tiềm Năng
    xien = report.get("xien", {})
    if xien.get("xien_same_head") or xien.get("xien_same_tail"):
        msg2 += f"✂️ <b>XIÊN 2 GỢI Ý</b>\n"
        for p_a, p_b, reason in xien.get("xien_same_head", []):
            msg2 += f"  • <code>{p_a:02d}</code> xiên <code>{p_b:02d}</code> ({reason})\n"
        for p_a, p_b, reason in xien.get("xien_same_tail", []):
            msg2 += f"  • <code>{p_a:02d}</code> xiên <code>{p_b:02d}</code> ({reason})\n"
        msg2 += "\n"
        
    # 9. Dàn Số Gợi Ý (Top 3)
    dan = report.get("dan_de", {})
    if dan.get("top_3"):
        msg2 += f"📋 <b>DÀN SỐ GỢI Ý (TOP 3 VIP)</b>\n"
        dd_list = [f"<code>{p:02d}</code>" for p in dan["top_3"]]
        msg2 += f"  👉 {', '.join(dd_list)}\n"
        
        msg2 += f"  <i>Bộ lọc đa tiêu chí đã áp dụng:</i>\n"
        for f in dan.get("criteria", []):
            msg2 += f"   - {f}\n"
            
    messages.append(msg2.strip())
    
    return messages
