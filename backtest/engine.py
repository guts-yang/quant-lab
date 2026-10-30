"""月末调仓、日频止损，并计入印花税、佣金和滑点。"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pandas as pd

from core.metrics import performance
from core.provider import HS300
from strategy.risk_control import (
    ATR_WINDOW,
    average_true_range,
    index_exposure,
    month_end_book,
    stop_fill,
    stop_price,
)

INITIAL_CAPITAL = 40_000.0
STAMP_TAX = 0.0005
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.0
SLIPPAGE = 0.001
LOT_SIZE = 100


@dataclass(frozen=True)
class TradingCosts:
    """成交成本。默认值与当前 A 股本地回测基线一致。"""

    commission_rate: float = COMMISSION_RATE
    min_commission: float = MIN_COMMISSION
    stamp_tax: float = STAMP_TAX
    slippage: float = SLIPPAGE


DEFAULT_COSTS = TradingCosts()


@dataclass
class _Position:
    shares: int
    cost: float
    peak: float


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float] = field(default_factory=dict)


def commission(amount: float, costs: TradingCosts = DEFAULT_COSTS) -> float:
    """双向佣金。成交额的 0.025% 不足 5 元时按 5 元。"""
    return max(costs.min_commission, abs(float(amount)) * costs.commission_rate)


def execution_price(price: float, side: str, costs: TradingCosts = DEFAULT_COSTS) -> float:
    if side == "buy":
        return float(price) * (1.0 + costs.slippage)
    if side == "sell":
        return float(price) * (1.0 - costs.slippage)
    raise ValueError(f"未知方向 {side}")


def sell_cash(price: float, shares: int, costs: TradingCosts = DEFAULT_COSTS) -> float:
    fill = execution_price(price, "sell", costs)
    gross = fill * int(shares)
    return gross - commission(gross, costs) - gross * costs.stamp_tax


def buy_cash(price: float, shares: int, costs: TradingCosts = DEFAULT_COSTS) -> tuple[float, float]:
    """返回 (现金支出, 成交价)。"""
    fill = execution_price(price, "buy", costs)
    gross = fill * int(shares)
    return gross + commission(gross, costs), fill


def simulate(
    bars: pd.DataFrame,
    index_bars: pd.DataFrame | None,
    scores_at: Callable[[pd.Timestamp], pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float = INITIAL_CAPITAL,
    top_n: int = 8,
    exit_rank: int = 15,
    costs: TradingCosts = DEFAULT_COSTS,
) -> BacktestResult:
    """bars 含 ts_code、trade_date、open、high、low、close。scores_at 只在月末调用。"""
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    sessions = _sessions(bars)
    window = [day for day in sessions if start <= day <= end]
    if not window:
        empty = pd.DataFrame(columns=["trade_date", "equity"])
        return BacktestResult(
            empty,
            pd.DataFrame(columns=["trade_date", "ts_code", "side", "shares", "price", "reason"]),
            {"cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": float("nan")},
        )

    atr = average_true_range(bars, ATR_WINDOW)
    cash = float(capital)
    positions: dict[str, _Position] = {}
    trades: list[dict[str, object]] = []
    curve: list[dict[str, object]] = []
    month_ends = _month_ends(window)

    for day in window:
        today = _day_bars(bars, day)
        cash, positions, day_trades = _apply_stops(day, today, atr, cash, positions, costs)
        trades.extend(day_trades)
        if day in month_ends:
            cash, positions, rebalance_trades = _rebalance(
                day, today, cash, positions, scores_at(day), index_bars, top_n, exit_rank, costs
            )
            trades.extend(rebalance_trades)
        curve.append({"trade_date": day, "cash": cash, "equity": cash + _market_value(positions, today)})

    equity = pd.DataFrame(curve)
    return BacktestResult(equity, pd.DataFrame(trades), performance(equity))


def run_backtest(
    provider: object,
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float = INITIAL_CAPITAL,
    top_n: int = 8,
    exit_rank: int = 15,
    costs: TradingCosts = DEFAULT_COSTS,
) -> BacktestResult:
    from strategy.scorer import score_candidates

    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    warmup = start - pd.Timedelta(days=120)
    bars = provider.get_ohlc(warmup, end)
    index_bars = provider.get_index_daily(HS300, warmup, end)

    def scores_at(asof: pd.Timestamp) -> pd.DataFrame:
        return score_candidates(provider, asof)

    return simulate(
        bars,
        index_bars,
        scores_at,
        start,
        end,
        capital=capital,
        top_n=top_n,
        exit_rank=exit_rank,
        costs=costs,
    )


def _apply_stops(
    day: pd.Timestamp,
    today: pd.DataFrame,
    atr: pd.DataFrame,
    cash: float,
    positions: dict[str, _Position],
    costs: TradingCosts,
) -> tuple[float, dict[str, _Position], list[dict[str, object]]]:
    trades: list[dict[str, object]] = []
    for code in list(positions):
        row = _row(today, code)
        if row is None:
            continue
        position = positions[code]
        width = _atr_before(atr, code, day)
        level = stop_price(position.cost, position.peak, width)
        raw = stop_fill(_number(row, "open"), _number(row, "low"), level)
        if raw is None:
            high = _number(row, "high")
            if pd.notna(high):
                position.peak = max(position.peak, float(high))
            continue
        cash += sell_cash(raw, position.shares, costs)
        trades.append(_trade(day, code, "sell", position.shares, raw, "stop"))
        del positions[code]
    return cash, positions, trades


def _rebalance(
    day: pd.Timestamp,
    today: pd.DataFrame,
    cash: float,
    positions: dict[str, _Position],
    scores: pd.DataFrame,
    index_bars: pd.DataFrame | None,
    top_n: int,
    exit_rank: int,
    costs: TradingCosts,
) -> tuple[float, dict[str, _Position], list[dict[str, object]]]:
    book = month_end_book(scores, list(positions), top_n=top_n, exit_rank=exit_rank)
    exposure = index_exposure(index_bars, day)
    trades: list[dict[str, object]] = []
    for code in list(positions):
        if code in book:
            continue
        row = _row(today, code)
        if row is None:
            continue
        price = _number(row, "close")
        if pd.isna(price) or float(price) <= 0:
            continue
        shares = positions[code].shares
        cash += sell_cash(float(price), shares, costs)
        trades.append(_trade(day, code, "sell", shares, float(price), "rebalance"))
        del positions[code]

    equity = cash + _market_value(positions, today)
    count = len(book)
    target_value = equity * exposure / count if count else 0.0
    for code in list(positions):
        if code not in book:
            continue
        cash, sold = _resize_down(day, code, today, positions, target_value, cash, costs)
        trades.extend(sold)
    for code in book:
        cash, bought = _resize_up(day, code, today, positions, target_value, cash, costs)
        trades.extend(bought)
    return cash, positions, trades


def _resize_down(
    day: pd.Timestamp,
    code: str,
    today: pd.DataFrame,
    positions: dict[str, _Position],
    target_value: float,
    cash: float,
    costs: TradingCosts,
) -> tuple[float, list[dict[str, object]]]:
    row = _row(today, code)
    if row is None or code not in positions:
        return cash, []
    price = _number(row, "close")
    if pd.isna(price) or float(price) <= 0:
        return cash, []
    held = positions[code].shares
    target_shares = _lots(target_value / float(price))
    sell_shares = held - target_shares
    if sell_shares < LOT_SIZE:
        return cash, []
    sell_shares = _lots(sell_shares)
    cash += sell_cash(float(price), sell_shares, costs)
    left = held - sell_shares
    if left <= 0:
        del positions[code]
    else:
        positions[code].shares = left
    return cash, [_trade(day, code, "sell", sell_shares, float(price), "rebalance")]


def _resize_up(
    day: pd.Timestamp,
    code: str,
    today: pd.DataFrame,
    positions: dict[str, _Position],
    target_value: float,
    cash: float,
    costs: TradingCosts,
) -> tuple[float, list[dict[str, object]]]:
    row = _row(today, code)
    if row is None:
        return cash, []
    price = _number(row, "close")
    if pd.isna(price) or float(price) <= 0:
        return cash, []
    held = positions[code].shares if code in positions else 0
    target_shares = _lots(target_value / execution_price(float(price), "buy", costs))
    buy_shares = target_shares - held
    if buy_shares < LOT_SIZE:
        return cash, []
    while buy_shares >= LOT_SIZE:
        spent, fill = buy_cash(float(price), buy_shares, costs)
        if spent <= cash + 1e-9:
            cash -= spent
            _add_shares(positions, code, buy_shares, fill, _number(row, "high"))
            return cash, [_trade(day, code, "buy", buy_shares, float(price), "rebalance")]
        buy_shares -= LOT_SIZE
    return cash, []


def _add_shares(positions: dict[str, _Position], code: str, shares: int, fill: float, high: float) -> None:
    peak = fill if pd.isna(high) else max(fill, float(high))
    current = positions.get(code)
    if current is None:
        positions[code] = _Position(shares=shares, cost=fill, peak=peak)
        return
    total = current.shares + shares
    current.cost = (current.cost * current.shares + fill * shares) / total
    current.shares = total
    current.peak = max(current.peak, peak)


def _market_value(positions: dict[str, _Position], today: pd.DataFrame) -> float:
    value = 0.0
    for code, position in positions.items():
        row = _row(today, code)
        if row is None:
            continue
        price = _number(row, "close")
        if pd.notna(price):
            value += position.shares * float(price)
    return value


def _atr_before(atr: pd.DataFrame, code: str, day: pd.Timestamp) -> float | None:
    if atr.empty:
        return None
    history = atr.loc[(atr["ts_code"] == code) & (atr["trade_date"] < day) & atr["atr"].notna()]
    if history.empty:
        return None
    return float(history.iloc[-1]["atr"])


def _sessions(bars: pd.DataFrame) -> list[pd.Timestamp]:
    dates = pd.to_datetime(bars["trade_date"]).dt.normalize().dropna().drop_duplicates().sort_values()
    return list(dates)


def _month_ends(dates: list[pd.Timestamp]) -> set[pd.Timestamp]:
    if not dates:
        return set()
    frame = pd.DataFrame({"trade_date": dates})
    frame["month"] = frame["trade_date"].dt.to_period("M")
    last = frame.groupby("month", sort=False)["trade_date"].max()
    return set(last.tolist())


def _day_bars(bars: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    dates = pd.to_datetime(bars["trade_date"]).dt.normalize()
    return bars.loc[dates.eq(day)]


def _row(today: pd.DataFrame, code: str) -> pd.Series | None:
    matched = today.loc[today["ts_code"].astype(str).eq(code)]
    if matched.empty:
        return None
    return matched.iloc[-1]


def _number(row: pd.Series, name: str) -> float:
    if name not in row.index:
        return float("nan")
    return float(pd.to_numeric(row[name], errors="coerce"))


def _lots(shares: float) -> int:
    if shares <= 0:
        return 0
    return int(shares // LOT_SIZE) * LOT_SIZE


def _trade(day: pd.Timestamp, code: str, side: str, shares: int, price: float, reason: str) -> dict[str, object]:
    return {"trade_date": day, "ts_code": code, "side": side, "shares": shares, "price": price, "reason": reason}


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="按月末打分和日频止损回测")
    parser.add_argument("--start", required=True, help="开始日期，YYYYMMDD")
    parser.add_argument("--end", required=True, help="结束日期，YYYYMMDD")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL, help="初始资金，默认 40000")
    parser.add_argument("--top", type=int, default=8, help="目标持仓数，5 到 8")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from core.tushare_provider import TushareProvider

    result = run_backtest(TushareProvider(), args.start, args.end, capital=args.capital, top_n=args.top)
    metrics = result.metrics
    print(
        f"start={args.start} end={args.end} "
        f"cagr={metrics['cagr']:.4f} max_drawdown={metrics['max_drawdown']:.4f} "
        f"sharpe={metrics['sharpe']:.4f} calmar={metrics['calmar']:.4f}"
    )


if __name__ == "__main__":
    main()
