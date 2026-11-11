"""CAPM 股权成本。beta 用个股对指数的周收益回归，Blume 调整后夹取。"""

from __future__ import annotations

import pandas as pd

BETA_FLOOR = 0.5
BETA_CAP = 2.5
BLUME_WEIGHT = 0.67
MIN_WEEKS = 52
DEFAULT_WINDOW_WEEKS = 104
RATE_FLOOR = 0.06
RATE_CAP = 0.15


def weekly_returns(closes: pd.Series) -> pd.Series:
    """日收盘价（按日期升序、DatetimeIndex）重采样到周五收盘后的周收益。"""
    closes = closes.dropna().sort_index()
    if closes.empty:
        return pd.Series(dtype="Float64")
    weekly = closes.resample("W-FRI").last().dropna()
    return weekly.pct_change().dropna()


def estimate_beta(
    stock: pd.DataFrame,
    index: pd.DataFrame,
    *,
    window_weeks: int = DEFAULT_WINDOW_WEEKS,
    blume: bool = True,
) -> float | None:
    """个股周收益对指数周收益的回归斜率。数据不足 MIN_WEEKS 周时返回 None。

    stock / index 都需要 trade_date、close 两列。先按共同交易日对齐再重采样，
    避免个股停牌周被指数收益带偏。
    """
    left = _close_series(stock)
    right = _close_series(index)
    if left.empty or right.empty:
        return None
    common = left.index.intersection(right.index)
    if common.empty:
        return None
    stock_ret = weekly_returns(left.loc[common])
    index_ret = weekly_returns(right.loc[common])
    aligned = pd.concat([stock_ret.rename("stock"), index_ret.rename("index")], axis=1, join="inner").dropna()
    aligned = aligned.tail(window_weeks)
    if len(aligned) < MIN_WEEKS:
        return None
    variance = float(aligned["index"].var(ddof=1))
    if variance <= 0:
        return None
    beta = float(aligned["stock"].cov(aligned["index"]) / variance)
    if blume:
        beta = BLUME_WEIGHT * beta + (1.0 - BLUME_WEIGHT) * 1.0
    return min(BETA_CAP, max(BETA_FLOOR, beta))


def cost_of_equity(
    risk_free: float,
    beta: float,
    equity_risk_premium: float,
    *,
    floor: float = RATE_FLOOR,
    cap: float = RATE_CAP,
) -> float:
    """rf + β×ERP，夹取在 [floor, cap] 防止极端 beta 把估值打飞。"""
    rate = risk_free + beta * equity_risk_premium
    return min(cap, max(floor, rate))


def _close_series(frame: pd.DataFrame) -> pd.Series:
    if frame is None or frame.empty or "close" not in frame.columns:
        return pd.Series(dtype="Float64")
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    series = pd.Series(closes.to_numpy(dtype="float64"), index=pd.DatetimeIndex(dates))
    series = series[series.index.notna()].dropna()
    return series.groupby(level=0).last().sort_index()
