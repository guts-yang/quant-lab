"""净值曲线上的收益和回撤。"""

from __future__ import annotations

import math

import pandas as pd

TRADING_DAYS = 252
CALENDAR_DAYS = 365.25


def max_drawdown(equity: pd.Series) -> float:
    """相对历史最高净值的最大跌幅，结果小于等于 0。"""
    wealth = pd.to_numeric(equity, errors="coerce").dropna()
    if wealth.empty:
        return 0.0
    drawdown = wealth / wealth.cummax() - 1.0
    return float(drawdown.min())


def cagr(equity: pd.Series, dates: pd.Series) -> float:
    wealth = pd.to_numeric(equity, errors="coerce")
    calendar = pd.to_datetime(dates)
    valid = wealth.notna() & calendar.notna()
    wealth = wealth.loc[valid]
    calendar = calendar.loc[valid]
    if len(wealth) < 2:
        return 0.0
    days = (calendar.iloc[-1] - calendar.iloc[0]).days
    start = float(wealth.iloc[0])
    end = float(wealth.iloc[-1])
    if days <= 0 or start <= 0 or end <= 0:
        return 0.0
    return (end / start) ** (CALENDAR_DAYS / days) - 1.0


def sharpe(equity: pd.Series) -> float:
    wealth = pd.to_numeric(equity, errors="coerce").dropna()
    returns = wealth.pct_change().dropna()
    if returns.empty:
        return 0.0
    scale = float(returns.std(ddof=0))
    if scale == 0.0 or math.isnan(scale):
        return 0.0
    return float(returns.mean() / scale * math.sqrt(TRADING_DAYS))


def calmar(annual_return: float, drawdown: float) -> float:
    """年化收益除以最大回撤的绝对值。没有回撤时没有定义。"""
    if drawdown >= 0 or math.isnan(drawdown):
        return math.nan
    return float(annual_return) / abs(float(drawdown))


def performance(equity: pd.DataFrame) -> dict[str, float]:
    """equity 需要 trade_date 和 equity 两列。"""
    frame = equity.sort_values("trade_date")
    annual_return = cagr(frame["equity"], frame["trade_date"])
    drawdown = max_drawdown(frame["equity"])
    return {
        "cagr": annual_return,
        "max_drawdown": drawdown,
        "sharpe": sharpe(frame["equity"]),
        "calmar": calmar(annual_return, drawdown),
    }
