"""小账户交易成本与参数邻域压力测试，不修改基线策略。"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from backtest.engine import TradingCosts, simulate
from research.config import CostScenario, ResearchConfig


def default_scenarios(base: CostScenario) -> tuple[CostScenario, ...]:
    """预先定义的保守情景；表现最好者不会反写为默认参数。"""
    return (
        base,
        replace(base, name="high_slippage", slippage=base.slippage * 2),
        replace(base, name="double_min_commission", min_commission=base.min_commission * 2),
        replace(base, name="capital_20000", capital=20_000.0),
        replace(base, name="capital_80000", capital=80_000.0),
        replace(base, name="top_5", top_n=5),
        replace(base, name="top_8_no_buffer", exit_rank=base.top_n),
    )


def run_stress(
    bars: pd.DataFrame,
    index_bars: pd.DataFrame,
    snapshots: dict[pd.Timestamp, pd.DataFrame],
    config: ResearchConfig,
    *,
    period_name: str = "holdout",
) -> pd.DataFrame:
    """在固定时间段对成本、资金和持仓数做敏感性测试。"""
    period = next(item for item in config.periods if item.name == period_name)

    def scores_at(asof: pd.Timestamp) -> pd.DataFrame:
        return snapshots.get(pd.Timestamp(asof).normalize(), pd.DataFrame(columns=["ts_code", "composite"]))

    records: list[dict[str, object]] = []
    for scenario in default_scenarios(config.scenario):
        costs = TradingCosts(
            commission_rate=scenario.commission_rate,
            min_commission=scenario.min_commission,
            stamp_tax=scenario.stamp_tax,
            slippage=scenario.slippage,
        )
        result = simulate(
            bars,
            index_bars,
            scores_at,
            period.start,
            period.end,
            capital=scenario.capital,
            top_n=scenario.top_n,
            exit_rank=scenario.exit_rank,
            costs=costs,
        )
        records.append(
            {
                **_summary(result, costs, bars, scenario.capital),
                "scenario": scenario.name,
                "capital": scenario.capital,
                "top_n": scenario.top_n,
                "exit_rank": scenario.exit_rank,
            }
        )
    return pd.DataFrame(records).sort_values("scenario").reset_index(drop=True)


def _summary(result: object, costs: TradingCosts, bars: pd.DataFrame, capital: float) -> dict[str, float]:
    equity = result.equity
    trades = result.trades
    if trades.empty:
        return {
            **result.metrics,
            "annualized_turnover": 0.0,
            "estimated_cost": 0.0,
            "estimated_cost_to_average_equity": 0.0,
            "mean_cash_ratio": 1.0,
            "minimum_trade_value": 0.0,
            "median_lot_weight": _median_lot_weight(bars, capital),
            "trades": 0.0,
        }
    trade_value = pd.to_numeric(trades["price"], errors="coerce") * pd.to_numeric(trades["shares"], errors="coerce")
    commissions = trade_value.map(lambda amount: max(costs.min_commission, abs(float(amount)) * costs.commission_rate))
    taxes = trade_value.where(trades["side"].eq("sell"), 0.0) * costs.stamp_tax
    slippage = trade_value * costs.slippage
    estimated_cost = float((commissions + taxes + slippage).sum())
    average_equity = float(equity["equity"].mean()) if not equity.empty else float("nan")
    sessions = max(len(equity), 1)
    turnover = float(trade_value.sum()) / average_equity * 252 / sessions if average_equity > 0 else float("nan")
    cash_ratio = float((equity["cash"] / equity["equity"]).mean()) if "cash" in equity and not equity.empty else float("nan")
    return {
        **result.metrics,
        "annualized_turnover": turnover,
        "estimated_cost": estimated_cost,
        "estimated_cost_to_average_equity": estimated_cost / average_equity if average_equity > 0 else float("nan"),
        "mean_cash_ratio": cash_ratio,
        "minimum_trade_value": float(trade_value.min()),
        "median_lot_weight": _median_lot_weight(bars, capital),
        "trades": float(len(trades)),
    }


def _median_lot_weight(bars: pd.DataFrame, capital: float) -> float:
    """一手股票相对资金的典型权重，是整手导致权重偏差的可读代理。"""
    closes = pd.to_numeric(bars.get("close", pd.Series(dtype=float)), errors="coerce").dropna()
    if closes.empty or capital <= 0:
        return float("nan")
    return float(closes.median() * 100 / capital)
