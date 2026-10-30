"""按冻结月末信号执行开发、验证、锁定样本外三个时间段。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest.engine import BacktestResult, TradingCosts, simulate
from core.metrics import performance
from research.config import Period, ResearchConfig


@dataclass
class PeriodResult:
    period: Period
    strategy: BacktestResult
    benchmark: pd.DataFrame
    benchmark_metrics: dict[str, float]
    monthly_holdings: pd.DataFrame


def run_walkforward(
    bars: pd.DataFrame,
    index_bars: pd.DataFrame,
    snapshots: dict[pd.Timestamp, pd.DataFrame],
    config: ResearchConfig,
) -> dict[str, PeriodResult]:
    """只从冻结的 snapshots 读取月末分数，不在样本外重算或调参。"""
    costs = TradingCosts(
        commission_rate=config.scenario.commission_rate,
        min_commission=config.scenario.min_commission,
        stamp_tax=config.scenario.stamp_tax,
        slippage=config.scenario.slippage,
    )

    def scores_at(asof: pd.Timestamp) -> pd.DataFrame:
        return snapshots.get(pd.Timestamp(asof).normalize(), pd.DataFrame(columns=["ts_code", "composite"]))

    results: dict[str, PeriodResult] = {}
    for period in config.periods:
        strategy = simulate(
            bars,
            index_bars,
            scores_at,
            period.start,
            period.end,
            capital=config.scenario.capital,
            top_n=config.scenario.top_n,
            exit_rank=config.scenario.exit_rank,
            costs=costs,
        )
        benchmark = benchmark_curve(index_bars, period, config.scenario.capital)
        results[period.name] = PeriodResult(
            period=period,
            strategy=strategy,
            benchmark=benchmark,
            benchmark_metrics=performance(benchmark),
            monthly_holdings=holdings_from_trades(strategy.trades, strategy.equity.get("trade_date")),
        )
    return results


def benchmark_curve(index_bars: pd.DataFrame, period: Period, capital: float) -> pd.DataFrame:
    """沪深 300 的价格收益买入持有基准；不包含分红总回报。"""
    frame = index_bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.loc[frame["trade_date"].between(period.start, period.end)].dropna(subset=["close"]).sort_values("trade_date")
    if frame.empty:
        return pd.DataFrame(columns=["trade_date", "equity"])
    first = float(frame["close"].iloc[0])
    return pd.DataFrame({"trade_date": frame["trade_date"], "equity": float(capital) * frame["close"] / first})


def holdings_from_trades(trades: pd.DataFrame, dates: pd.Series | None = None) -> pd.DataFrame:
    """从成交明细还原每个自然月最后一次成交后的持仓股数。"""
    columns = ["trade_date", "ts_code", "shares"]
    if trades is None or trades.empty:
        return pd.DataFrame(columns=columns)
    frame = trades.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame["signed"] = frame["shares"].where(frame["side"].eq("buy"), -frame["shares"])
    daily = frame.groupby(["trade_date", "ts_code"], as_index=False)["signed"].sum().sort_values("trade_date")
    wide = daily.pivot(index="trade_date", columns="ts_code", values="signed").fillna(0)
    if dates is not None:
        sessions = pd.DatetimeIndex(pd.to_datetime(dates, errors="coerce").dropna().normalize().drop_duplicates()).sort_values()
        wide = wide.reindex(sessions, fill_value=0)
    wide = wide.cumsum()
    monthly = wide.groupby(wide.index.to_period("M")).tail(1)
    output = monthly.stack().rename("shares").reset_index()
    output.columns = columns
    return output.loc[output["shares"].gt(0)].reset_index(drop=True)


def persist_walkforward(results: dict[str, PeriodResult], root: str | Path) -> pd.DataFrame:
    root = Path(root) / "walkforward"
    records: list[dict[str, object]] = []
    for name, result in results.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        result.strategy.equity.to_parquet(directory / "strategy_equity.parquet", index=False)
        result.strategy.trades.to_parquet(directory / "trades.parquet", index=False)
        result.benchmark.to_parquet(directory / "benchmark_equity.parquet", index=False)
        result.monthly_holdings.to_parquet(directory / "monthly_holdings.parquet", index=False)
        cash_ratio = (
            float((result.strategy.equity["cash"] / result.strategy.equity["equity"]).mean())
            if "cash" in result.strategy.equity and not result.strategy.equity.empty
            else float("nan")
        )
        records.append(
            {
                "period": name,
                **{f"strategy_{key}": value for key, value in result.strategy.metrics.items()},
                **{f"benchmark_{key}": value for key, value in result.benchmark_metrics.items()},
                "trades": int(len(result.strategy.trades)),
                "mean_cash_ratio": cash_ratio,
            }
        )
    summary = pd.DataFrame(records)
    root.mkdir(parents=True, exist_ok=True)
    summary.to_parquet(root / "summary.parquet", index=False)
    return summary
