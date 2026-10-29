"""成长因子：单季扣非同比、单季营收同比、财报后跳空。"""

from __future__ import annotations

import pandas as pd

from core.pit import latest_visible
from factors.fundamental.common import announced_history, point_yoy, single_quarter_yoy


def growth_factors(
    asof: pd.Timestamp,
    codes: pd.Series,
    indicators: pd.DataFrame | None,
    prices: pd.DataFrame | None,
    *,
    gap_window_days: int = 120,
) -> pd.DataFrame:
    asof = pd.Timestamp(asof).normalize()
    frame = pd.DataFrame({"ts_code": pd.Series(codes, copy=False).astype(str).drop_duplicates().reset_index(drop=True)})
    history = announced_history(indicators, asof)
    yoy = single_quarter_yoy(history, "q_dtprofit")
    frame = frame.merge(yoy[["ts_code", "yoy"]], on="ts_code", how="left")
    frame = frame.rename(columns={"yoy": "q_profit_yoy"})

    latest = latest_visible(indicators, asof)
    if latest.empty or "q_sales_yoy" not in latest.columns:
        frame["q_sales_yoy"] = pd.NA
    else:
        frame = frame.merge(latest[["ts_code", "q_sales_yoy"]], on="ts_code", how="left")

    frame = frame.merge(_earnings_gap(asof, history, prices, gap_window_days), on="ts_code", how="left")
    return frame[["ts_code", "q_profit_yoy", "q_sales_yoy", "earnings_gap"]]


def _earnings_gap(
    asof: pd.Timestamp,
    history: pd.DataFrame,
    prices: pd.DataFrame | None,
    gap_window_days: int,
) -> pd.DataFrame:
    columns = ["ts_code", "earnings_gap"]
    if history is None or history.empty or "ann_date" not in history.columns or "q_dtprofit" not in history.columns:
        return pd.DataFrame(columns=columns)
    window_start = asof - pd.Timedelta(days=gap_window_days)
    recent = history[history["ann_date"].notna() & history["ann_date"].ge(window_start) & history["ann_date"].le(asof)]
    if recent.empty:
        return pd.DataFrame(columns=columns)
    recent = recent.sort_values(["ts_code", "ann_date", "end_date"]).drop_duplicates("ts_code", keep="last")
    if prices is None or prices.empty:
        return pd.DataFrame(columns=columns)

    price = prices.copy()
    price["ts_code"] = price["ts_code"].astype(str)
    price["trade_date"] = pd.to_datetime(price["trade_date"], errors="coerce").dt.normalize()
    price = price[price["trade_date"].notna() & price["trade_date"].le(asof)]
    gaps: list[dict[str, object]] = []
    for record in recent.itertuples(index=False):
        sessions = price[(price["ts_code"] == record.ts_code) & (price["trade_date"] > record.ann_date)]
        sessions = sessions.sort_values("trade_date")
        change = point_yoy(history, "q_dtprofit", str(record.ts_code), record.end_date)
        gap = pd.NA
        if pd.notna(change) and change > 0 and not sessions.empty:
            first = sessions.iloc[0]
            pre_close = pd.to_numeric(first["pre_close"], errors="coerce")
            opened = pd.to_numeric(first["open"], errors="coerce")
            if pd.notna(pre_close) and pre_close > 0 and pd.notna(opened):
                gap = float(opened) / float(pre_close) - 1
        gaps.append({"ts_code": record.ts_code, "earnings_gap": gap})
    return pd.DataFrame(gaps, columns=columns)
