"""质量因子：ROE_TTM、ROIC、毛利率方差。方差越小越好，方向在打分时反过来。"""

from __future__ import annotations

import pandas as pd

from factors.fundamental.common import (
    CONSOLIDATED_REPORTS,
    announced_history,
    equity_lookup,
    four_quarter_sum,
    latest_column,
    trailing_variance,
)


def quality_factors(
    asof: pd.Timestamp,
    codes: pd.Series,
    balance_sheets: pd.DataFrame | None,
    indicators: pd.DataFrame | None,
) -> pd.DataFrame:
    asof = pd.Timestamp(asof).normalize()
    frame = pd.DataFrame({"ts_code": pd.Series(codes, copy=False).astype(str).drop_duplicates().reset_index(drop=True)})
    history = announced_history(indicators, asof)
    ttm = four_quarter_sum(history, "q_dtprofit")
    balance = announced_history(balance_sheets, asof, allowed_report_types=CONSOLIDATED_REPORTS)
    roe = _roe_ttm(ttm, balance)
    frame = frame.merge(roe, on="ts_code", how="left")
    frame = frame.merge(latest_column(indicators, asof, "roic"), on="ts_code", how="left")
    variance = trailing_variance(history, "grossprofit_margin", periods=12)
    frame = frame.merge(variance.rename(columns={"variance": "gross_margin_var"}), on="ts_code", how="left")
    return frame[["ts_code", "roe_ttm", "roic", "gross_margin_var"]]


def _roe_ttm(ttm: pd.DataFrame, balance: pd.DataFrame) -> pd.DataFrame:
    if ttm.empty:
        return pd.DataFrame(columns=["ts_code", "roe_ttm"])
    ending = ttm[["ts_code", "end_date"]].rename(columns={"end_date": "equity_date"})
    beginning = ttm[["ts_code", "start_date"]].rename(columns={"start_date": "equity_date"})
    end_equity = pd.to_numeric(equity_lookup(balance, ending.rename(columns={"equity_date": "end_date"})), errors="coerce")
    start_equity = pd.to_numeric(
        equity_lookup(balance, beginning.rename(columns={"equity_date": "end_date"})),
        errors="coerce",
    )
    average = (end_equity + start_equity) / 2
    profit = pd.to_numeric(ttm["total"], errors="coerce")
    roe = profit / average.where(average.gt(0))
    return pd.DataFrame({"ts_code": ttm["ts_code"].astype(str), "roe_ttm": roe.to_numpy()})
