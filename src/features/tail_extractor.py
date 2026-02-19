"""
tail_extractor.py
Trích xuất 2 số cuối của mọi giải từ một bản ghi lottery_draws.
"""
from typing import Dict, List, Optional


# Map prize field → prize_code
PRIZE_FIELDS = [
    ("special_prize",  "DB"),
    ("first_prize",    "1"),
    ("second_prize",   "2"),
    ("third_prize",    "3"),
    ("fourth_prize",   "4"),
    ("fifth_prize",    "5"),
    ("sixth_prize",    "6"),
    ("seventh_prize",  "7"),
    ("eighth_prize",   "8"),
]


def extract_tail(value: str) -> Optional[int]:
    """Lấy 2 số cuối từ 1 chuỗi số. Trả về None nếu không hợp lệ."""
    if not value:
        return None
    # Lọc chỉ chữ số
    digits = "".join(c for c in str(value) if c.isdigit())
    if len(digits) < 2:
        return None
    return int(digits[-2:])


def extract_tails_from_draw(draw: Dict) -> List[Dict]:
    """
    Từ 1 bản ghi lottery_draws, trả về list các dict để insert vào tails_2d.

    Args:
        draw: dict từ Supabase (bao gồm id, draw_date, region, province, các giải)

    Returns:
        List[Dict] — mỗi dict có: draw_id, draw_date, region, province, prize_code, tail_2d
    """
    results = []
    draw_id   = draw["id"]
    draw_date = draw["draw_date"]
    region    = draw["region"]
    # XSMB là 1 đài quốc gia —province chỉ phản ánh tỉnh quay ngày đó, không phân biệt station
    province  = None if region == "XSMB" else draw.get("province")

    for field, prize_code in PRIZE_FIELDS:
        raw = draw.get(field)
        if raw is None:
            continue

        # Một số giải có nhiều số (TEXT[])
        if isinstance(raw, list):
            values = raw
        else:
            values = [raw]

        for val in values:
            tail = extract_tail(str(val))
            if tail is not None:
                results.append({
                    "draw_id":    draw_id,
                    "draw_date":  draw_date,
                    "region":     region,
                    "province":   province,
                    "prize_code": prize_code,
                    "tail_2d":    tail,
                })

    return results


def build_tail_set(tails: List[Dict]) -> set:
    """
    Từ list tails_2d (có field tail_2d), trả về set các cặp xuất hiện.
    Dùng để verify: nếu predicted_pair ∈ tail_set → trúng.
    """
    return {t["tail_2d"] for t in tails}
