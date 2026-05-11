import pandas as pd
from datetime import date
from typing import Optional


def _load_tails_by_draws(
    db,
    region: str,
    province: Optional[str] = None,
    n_draws: int = 100,
    before_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    Lấy N kỳ quay gần nhất của 1 province từ tails_2d.
    Lookback theo kỳ quay (LIMIT), KHÔNG theo ngày.

    Returns:
        DataFrame: columns ['draw_date', 'tail_set']
        Mỗi row = 1 kỳ quay, tail_set = frozenset of ints
    """
    query = db.supabase.table("tails_2d") \
        .select("draw_date,tail_2d") \
        .eq("region", region) \
        .order("draw_date", desc=True)

    if province:
        query = query.eq("province", province)
    else:
        query = query.is_("province", "null")

    if before_date:
        query = query.lt("draw_date", before_date.isoformat())

    # Lấy gấp 30 lần vì mỗi kỳ có ~18 tails → 100 kỳ cần ~1800 rows max
    query = query.limit(n_draws * 30)

    rows = query.execute().data
    if not rows:
        return pd.DataFrame(columns=["draw_date", "tail_set"])

    df = pd.DataFrame(rows)
    grouped = df.groupby("draw_date")["tail_2d"].apply(frozenset).reset_index()
    grouped.columns = ["draw_date", "tail_set"]

    # Consumers treat the end of the frame as the newest draw.
    grouped["draw_date"] = pd.to_datetime(grouped["draw_date"])
    grouped = grouped.sort_values("draw_date").tail(n_draws)

    return grouped.reset_index(drop=True)
