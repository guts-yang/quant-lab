"""把基准情景每股价值铺到每个交易日。

内在价值只在财报公告日重算，其余交易日沿用上一次成功的结果。
股价是日线收盘价。缺股本或 TTM 的公告日跳过，不改估值规则。
"""

from __future__ import annotations

import logging

import pandas as pd

from core.provider import recent_period_ends
from factors.fundamental.common import CONSOLIDATED_REPORTS
from valuation.assumptions import EstimatesProvider, ValuationConfig
from valuation.data import LOOKBACK_QUARTERS
from valuation.scenario import run_valuation

logger = logging.getLogger(__name__)

HISTORY_COLUMNS = ["trade_date", "close", "intrinsic"]
HISTORY_YEARS = 3


def announcement_dates(income: pd.DataFrame | None, ts_code: str, asof: pd.Timestamp) -> pd.Series:
    """这只股票在截面日及以前已经公告的合并报表日期。"""
    if income is None or income.empty or "ts_code" not in income.columns or "ann_date" not in income.columns:
        return pd.Series(dtype="datetime64[ns]")
    frame = income.loc[income["ts_code"].astype(str).eq(str(ts_code))].copy()
    if frame.empty:
        return pd.Series(dtype="datetime64[ns]")
    if "report_type" in frame.columns:
        types = frame["report_type"].map(_report_code)
        frame = frame.loc[types.isin(CONSOLIDATED_REPORTS)]
    announced = pd.to_datetime(frame["ann_date"], errors="coerce").dt.normalize()
    keep = announced.notna() & announced.le(pd.Timestamp(asof).normalize())
    if "end_date" in frame.columns:
        ends = pd.to_datetime(frame["end_date"], errors="coerce").dt.normalize()
        keep &= ends.notna() & announced.ge(ends)
    return pd.Series(sorted(set(announced.loc[keep])), dtype="datetime64[ns]")


def valuation_knots(announcements: pd.Series, start: pd.Timestamp, asof: pd.Timestamp) -> list[pd.Timestamp]:
    """窗口内的公告日，加上窗口开始前最近一次公告，以及截面日。"""
    start = pd.Timestamp(start).normalize()
    asof = pd.Timestamp(asof).normalize()
    dates = pd.to_datetime(pd.Series(announcements), errors="coerce").dropna().dt.normalize()
    unique = pd.Index(sorted(set(dates)))
    chosen: list[pd.Timestamp] = []
    prior = unique[unique <= start]
    if len(prior):
        chosen.append(pd.Timestamp(prior[-1]))
    inside = unique[(unique > start) & (unique <= asof)]
    chosen.extend(pd.Timestamp(day) for day in inside)
    if not chosen or chosen[-1] != asof:
        chosen.append(asof)
    result: list[pd.Timestamp] = []
    for day in chosen:
        if not result or result[-1] != day:
            result.append(day)
    return result


def align_history(prices: pd.DataFrame | None, knots: pd.DataFrame | None) -> pd.DataFrame:
    """每个交易日带上不晚于当天的最近一次内在价值。"""
    if prices is None or prices.empty or "trade_date" not in prices.columns or "close" not in prices.columns:
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    frame = pd.DataFrame(
        {
            "trade_date": _normalize_dates(prices["trade_date"]),
            "close": pd.to_numeric(prices["close"], errors="coerce"),
        }
    ).dropna()
    frame = frame.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last")
    if knots is None or knots.empty or "trade_date" not in knots.columns or "intrinsic" not in knots.columns:
        frame["intrinsic"] = pd.NA
        return frame[HISTORY_COLUMNS].reset_index(drop=True)
    steps = pd.DataFrame(
        {
            "trade_date": _normalize_dates(knots["trade_date"]),
            "intrinsic": pd.to_numeric(knots["intrinsic"], errors="coerce"),
        }
    ).dropna()
    steps = steps.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last")
    if steps.empty:
        frame["intrinsic"] = pd.NA
        return frame[HISTORY_COLUMNS].reset_index(drop=True)
    merged = pd.merge_asof(frame, steps, on="trade_date", direction="backward")
    return merged[HISTORY_COLUMNS].reset_index(drop=True)


def build_history(
    provider,
    ts_code: str,
    asof: pd.Timestamp,
    config: ValuationConfig | None = None,
    estimates: EstimatesProvider | None = None,
    valuer=None,
) -> pd.DataFrame:
    """三年收盘价，以及按公告日阶梯延伸的基准每股价值。"""
    asof = pd.Timestamp(asof).normalize()
    start = asof - pd.DateOffset(years=HISTORY_YEARS)
    periods = recent_period_ends(asof, n=LOOKBACK_QUARTERS)
    income = provider.get_income_statements(periods)
    knots = valuation_knots(announcement_dates(income, ts_code, asof), start, asof)
    value_at = valuer or (lambda day: _base_per_share(provider, ts_code, day, config, estimates))
    points: list[dict[str, object]] = []
    for day in knots:
        try:
            value = value_at(day)
        except ValueError as exc:
            logger.warning("%s %s 跳过：%s", ts_code, day.strftime("%Y-%m-%d"), exc)
            continue
        if value is None or pd.isna(value):
            logger.warning("%s %s 跳过：没有基准每股价值", ts_code, day.strftime("%Y-%m-%d"))
            continue
        logger.info("%s %s 基准每股价值 %.2f", ts_code, day.strftime("%Y-%m-%d"), float(value))
        points.append({"trade_date": day, "intrinsic": float(value)})
    prices = provider.get_stock_ohlc(ts_code, start, asof)
    return align_history(prices, pd.DataFrame(points, columns=["trade_date", "intrinsic"]))


def _base_per_share(provider, ts_code: str, asof: pd.Timestamp, config, estimates) -> float:
    report = run_valuation(provider, ts_code, asof, config, estimates)
    return float(report.scenarios["base"].per_share)


def _normalize_dates(values: pd.Series) -> pd.Series:
    dates = pd.to_datetime(values, errors="coerce").dt.normalize()
    return dates.astype("datetime64[ns]")


def _report_code(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text
