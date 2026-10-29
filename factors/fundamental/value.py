"""价值因子：EP_TTM、BP、股息率。市值用截面日总市值，不用现成 PE/PB。"""

from __future__ import annotations

import pandas as pd

from factors.fundamental.common import CONSOLIDATED_REPORTS, announced_history, four_quarter_sum, latest_column


def value_factors(
    asof: pd.Timestamp,
    codes: pd.Series,
    market: pd.DataFrame | None,
    balance_sheets: pd.DataFrame | None,
    indicators: pd.DataFrame | None,
    dividends: pd.DataFrame | None,
) -> pd.DataFrame:
    asof = pd.Timestamp(asof).normalize()
    frame = pd.DataFrame({"ts_code": pd.Series(codes, copy=False).astype(str).drop_duplicates().reset_index(drop=True)})
    snapshot = _market_snapshot(market)
    frame = frame.merge(snapshot, on="ts_code", how="left")
    if "close" not in frame.columns:
        frame["close"] = pd.NA
    if "total_mv" not in frame.columns:
        frame["total_mv"] = pd.NA

    history = announced_history(indicators, asof)
    ttm = four_quarter_sum(history, "q_dtprofit")
    frame = frame.merge(ttm[["ts_code", "total"]], on="ts_code", how="left")
    market_value = pd.to_numeric(frame["total_mv"], errors="coerce")
    profit = pd.to_numeric(frame["total"], errors="coerce")
    frame["ep_ttm"] = profit / market_value.where(market_value.gt(0))

    balance = announced_history(balance_sheets, asof, allowed_report_types=CONSOLIDATED_REPORTS)
    equity = latest_column(balance, asof, "equity")
    frame = frame.merge(equity, on="ts_code", how="left")
    book = pd.to_numeric(frame["equity"], errors="coerce")
    frame["bp"] = book.where(book.gt(0)) / market_value.where(market_value.gt(0))

    close = pd.to_numeric(frame["close"], errors="coerce")
    frame["dividend_yield"] = _cash_dividend_per_share(asof, frame["ts_code"], dividends) / close.where(close.gt(0))
    return frame[["ts_code", "ep_ttm", "bp", "dividend_yield"]]


def _market_snapshot(market: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["ts_code", "close", "total_mv"]
    if market is None or market.empty:
        return pd.DataFrame(columns=columns)
    frame = market.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    keep = [column for column in columns if column in frame.columns]
    return frame[keep].drop_duplicates("ts_code", keep="last")


def _cash_dividend_per_share(asof: pd.Timestamp, codes: pd.Series, dividends: pd.DataFrame | None) -> pd.Series:
    window_start = asof - pd.Timedelta(days=365)
    if dividends is None or dividends.empty:
        return pd.Series(0.0, index=codes.index)
    frame = dividends.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["ex_date"] = pd.to_datetime(frame["ex_date"], errors="coerce").dt.normalize()
    frame["cash_div_tax"] = pd.to_numeric(frame["cash_div_tax"], errors="coerce")
    visible = frame["ex_date"].notna() & frame["ex_date"].ge(window_start) & frame["ex_date"].le(asof)
    cash = frame.loc[visible].groupby("ts_code")["cash_div_tax"].sum()
    return codes.map(cash).fillna(0.0).astype(float)
