"""缠论日线组合。先离场，再按已经确认的二买、三买在下一交易日开盘成交。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest.engine import (
    DEFAULT_COSTS,
    INITIAL_CAPITAL,
    LOT_SIZE,
    BacktestResult,
    TradingCosts,
    buy_cash,
    execution_price,
    sell_cash,
)
from core.metrics import performance
from strategy.risk_control import average_true_range, index_exposure, stop_fill, stop_price

MAX_POSITIONS = 5
_LIMIT_UP = 1.10
_LIMIT_DOWN = 0.90


@dataclass
class _Position:
    shares: int
    cost: float
    peak: float
    defend: float
    kind: str
    structure_date: pd.Timestamp
    broken: bool = False


def simulate_chan(
    bars: pd.DataFrame,
    events: pd.DataFrame | None,
    index_bars: pd.DataFrame | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
    capital: float = INITIAL_CAPITAL,
    max_positions: int = MAX_POSITIONS,
    costs: TradingCosts = DEFAULT_COSTS,
) -> BacktestResult:
    """最多持有 max_positions 只，等权，100 股整手。涨停不买，跌停不卖，停牌顺延。"""
    if int(max_positions) < 1:
        raise ValueError("max_positions 至少为 1")
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    empty_trades = pd.DataFrame(columns=["trade_date", "ts_code", "side", "shares", "price", "reason"])
    frame = _prepare_bars(bars)
    sessions = [day for day in _sessions(frame) if start <= day <= end]
    if not sessions:
        empty_equity = pd.DataFrame(columns=["trade_date", "cash", "equity"])
        return BacktestResult(empty_equity, empty_trades, performance(empty_equity))

    signals = _prepare_events(events, start)
    atr = average_true_range(frame)
    last_close = _closes_before(frame, start)
    cash = float(capital)
    positions: dict[str, _Position] = {}
    consumed: set[tuple[str, str, pd.Timestamp]] = set()
    applied: tuple[float, tuple[str, ...]] | None = None
    trades: list[dict[str, object]] = []
    curve: list[dict[str, object]] = []
    marks: dict[str, float] = {}

    for day in sessions:
        today = _day_map(frame, day)
        cash, positions, exit_trades = _exit_positions(day, today, positions, last_close, atr, cash, costs)
        trades.extend(exit_trades)
        exposure = _exposure(index_bars, day)
        chosen = _choose_entries(day, today, signals, positions, consumed, last_close, int(max_positions))
        signature = (round(exposure, 2), tuple(sorted([*positions, *(str(signal["ts_code"]) for signal in chosen)])))
        if (positions or chosen) and applied != signature:
            cash, positions, resize_trades = _equalize(
                day, today, positions, chosen, exposure, cash, costs, last_close
            )
            trades.extend(resize_trades)
            _mark_filled(chosen, resize_trades, consumed)
            applied = (round(exposure, 2), tuple(sorted(positions)))
        for code, row in today.items():
            last_close[code] = _number(row, "close")
        curve.append({"trade_date": day, "cash": cash, "equity": cash + _market_value(positions, today, marks)})

    equity = pd.DataFrame(curve)
    return BacktestResult(equity, pd.DataFrame(trades) if trades else empty_trades, performance(equity))


def _exit_positions(
    day: pd.Timestamp,
    today: dict[str, pd.Series],
    positions: dict[str, _Position],
    last_close: dict[str, float],
    atr: pd.DataFrame,
    cash: float,
    costs: TradingCosts,
) -> tuple[float, dict[str, _Position], list[dict[str, object]]]:
    trades: list[dict[str, object]] = []
    for code in list(positions):
        row = today.get(code)
        if row is None:
            continue
        previous = last_close.get(code)
        if _limit_down(_number(row, "open"), previous):
            if previous is not None and previous < positions[code].defend:
                positions[code].broken = True
            continue
        position = positions[code]
        if previous is not None and previous < position.defend:
            position.broken = True
        if position.broken:
            price = _number(row, "open")
            cash += sell_cash(price, position.shares, costs)
            trades.append(_trade(day, code, "sell", position.shares, price, "structure"))
            del positions[code]
            continue
        level = stop_price(position.cost, position.peak, _atr_before(atr, code, day))
        raw = stop_fill(_number(row, "open"), _number(row, "low"), level)
        if raw is None:
            high = _number(row, "high")
            if pd.notna(high):
                position.peak = max(position.peak, float(high))
            continue
        cash += sell_cash(float(raw), position.shares, costs)
        trades.append(_trade(day, code, "sell", position.shares, float(raw), "stop"))
        del positions[code]
    return cash, positions, trades


def _choose_entries(
    day: pd.Timestamp,
    today: dict[str, pd.Series],
    signals: pd.DataFrame,
    positions: dict[str, _Position],
    consumed: set[tuple[str, str, pd.Timestamp]],
    last_close: dict[str, float],
    max_positions: int,
) -> list[dict[str, object]]:
    if signals.empty:
        return []
    pending = signals.loc[signals["confirm_date"].lt(day) & signals["ts_code"].isin(list(today))]
    chosen: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in pending.itertuples(index=False):
        key = (str(row.ts_code), str(row.kind), pd.Timestamp(row.structure_date))
        if key in consumed or key[0] in positions or key[0] in seen:
            continue
        bar = today[key[0]]
        opened = _number(bar, "open")
        defend = float(row.defend_price)
        if pd.isna(opened) or opened <= defend:
            consumed.add(key)
            continue
        if len(positions) + len(chosen) >= max_positions or _limit_up(opened, last_close.get(key[0])):
            continue
        chosen.append(
            {
                "ts_code": key[0],
                "kind": key[1],
                "structure_date": key[2],
                "defend_price": defend,
                "key": key,
            }
        )
        seen.add(key[0])
    return chosen


def _equalize(
    day: pd.Timestamp,
    today: dict[str, pd.Series],
    positions: dict[str, _Position],
    chosen: list[dict[str, object]],
    exposure: float,
    cash: float,
    costs: TradingCosts,
    last_close: dict[str, float],
) -> tuple[float, dict[str, _Position], list[dict[str, object]]]:
    new_codes = [str(item["ts_code"]) for item in chosen]
    names = list(dict.fromkeys([*positions, *new_codes]))
    active = [code for code in names if code in today and not _limit_down(_number(today[code], "open"), last_close.get(code))]
    if not active:
        return cash, positions, []
    prices = {code: _number(today[code], "open") for code in active}
    prices = {code: price for code, price in prices.items() if pd.notna(price) and price > 0}
    stuck = [code for code in positions if code not in prices]
    book = [code for code in names if code in prices or code in stuck]
    if not book:
        return cash, positions, []
    equity = cash + sum(positions[code].shares * prices[code] for code in positions if code in prices)
    equity += sum(positions[code].shares * last_close.get(code, positions[code].cost) for code in stuck)
    target_value = equity * float(exposure) / len(book)
    trades: list[dict[str, object]] = []
    remembered: dict[str, _Position] = {}
    for code in list(positions):
        price = prices.get(code)
        if price is None:
            continue
        target_shares = _lots(target_value / price)
        sell_shares = _lots(positions[code].shares - target_shares)
        if sell_shares < LOT_SIZE:
            continue
        cash += sell_cash(price, sell_shares, costs)
        left = positions[code].shares - sell_shares
        if left <= 0:
            remembered[code] = positions.pop(code)
        else:
            positions[code].shares = left
        trades.append(_trade(day, code, "sell", sell_shares, price, "rebalance"))
    incoming = {str(item["ts_code"]): item for item in chosen}
    for code in list(dict.fromkeys([*new_codes, *prices])):
        price = prices.get(code)
        if price is None or _limit_up(price, last_close.get(code)):
            continue
        held = positions[code].shares if code in positions else 0
        target_shares = _lots(target_value / execution_price(price, "buy", costs))
        buy_shares = _affordable(cash, price, target_shares - held, costs)
        if buy_shares < LOT_SIZE:
            continue
        spent, fill = buy_cash(price, buy_shares, costs)
        cash -= spent
        high = _number(today[code], "high")
        if code not in positions and code in incoming:
            signal = incoming[code]
            positions[code] = _Position(
                shares=buy_shares,
                cost=fill,
                peak=fill if pd.isna(high) else max(fill, float(high)),
                defend=float(signal["defend_price"]),
                kind=str(signal["kind"]),
                structure_date=pd.Timestamp(signal["structure_date"]),
            )
            trades.append(_trade(day, code, "buy", buy_shares, price, "entry"))
            continue
        if code not in positions and code in remembered:
            restored = remembered[code]
            restored.shares = 0
            positions[code] = restored
        if code not in positions:
            continue
        _add_shares(positions, code, buy_shares, fill, high)
        trades.append(_trade(day, code, "buy", buy_shares, price, "rebalance"))
    return cash, positions, trades


def _mark_filled(
    chosen: list[dict[str, object]],
    trades: list[dict[str, object]],
    consumed: set[tuple[str, str, pd.Timestamp]],
) -> None:
    filled = {str(row["ts_code"]) for row in trades if row["reason"] == "entry"}
    for item in chosen:
        if str(item["ts_code"]) in filled:
            consumed.add(item["key"])  # type: ignore[arg-type]


def _exposure(index_bars: pd.DataFrame | None, day: pd.Timestamp) -> float:
    if index_bars is None or index_bars.empty:
        return 1.0
    frame = index_bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    history = frame.loc[frame["trade_date"].lt(day)]
    if history.empty:
        return 1.0
    return float(index_exposure(history, day))


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    for name in ("open", "high", "low", "close"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame.dropna(subset=["ts_code", "trade_date"]).sort_values(["ts_code", "trade_date"])


def _prepare_events(events: pd.DataFrame | None, start: pd.Timestamp) -> pd.DataFrame:
    columns = ["ts_code", "kind", "structure_date", "confirm_date", "defend_price"]
    if events is None or events.empty:
        return pd.DataFrame(columns=columns)
    frame = events.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["kind"] = frame["kind"].astype(str)
    frame["structure_date"] = pd.to_datetime(frame["structure_date"]).dt.normalize()
    frame["confirm_date"] = pd.to_datetime(frame["confirm_date"]).dt.normalize()
    frame["defend_price"] = pd.to_numeric(frame["defend_price"], errors="coerce")
    if "tradable" in frame.columns:
        frame = frame.loc[frame["tradable"].astype(bool)]
    frame = frame.loc[frame["kind"].isin(["buy2", "buy3"]) & frame["confirm_date"].ge(start)]
    return frame.sort_values(["confirm_date", "ts_code", "structure_date"]).reset_index(drop=True)


def _closes_before(frame: pd.DataFrame, start: pd.Timestamp) -> dict[str, float]:
    prior = frame.loc[frame["trade_date"].lt(start)]
    closes: dict[str, float] = {}
    if prior.empty:
        return closes
    last = prior.groupby("ts_code", sort=False).tail(1)
    for row in last.itertuples(index=False):
        price = _number(row, "close")
        if pd.notna(price):
            closes[str(row.ts_code)] = float(price)
    return closes


def _affordable(cash: float, price: float, shares: int, costs: TradingCosts) -> int:
    lots = _lots(shares)
    while lots >= LOT_SIZE:
        spent, _fill = buy_cash(price, lots, costs)
        if spent <= cash + 1e-9:
            return lots
        lots -= LOT_SIZE
    return 0


def _add_shares(positions: dict[str, _Position], code: str, shares: int, fill: float, high: float) -> None:
    peak = fill if pd.isna(high) else max(fill, float(high))
    current = positions.get(code)
    if current is None:
        return
    total = current.shares + shares
    current.cost = (current.cost * current.shares + fill * shares) / total
    current.shares = total
    current.peak = max(current.peak, peak)


def _market_value(positions: dict[str, _Position], today: dict[str, pd.Series], marks: dict[str, float]) -> float:
    value = 0.0
    for code, position in positions.items():
        row = today.get(code)
        price = _number(row, "close") if row is not None else marks.get(code, float("nan"))
        if pd.isna(price):
            price = marks.get(code, float("nan"))
        if pd.notna(price):
            marks[code] = float(price)
            value += position.shares * float(price)
    return value


def _atr_before(atr: pd.DataFrame, code: str, day: pd.Timestamp) -> float | None:
    if atr.empty:
        return None
    history = atr.loc[(atr["ts_code"] == code) & (atr["trade_date"] < day) & atr["atr"].notna()]
    if history.empty:
        return None
    return float(history.iloc[-1]["atr"])


def _sessions(frame: pd.DataFrame) -> list[pd.Timestamp]:
    dates = pd.to_datetime(frame["trade_date"]).dt.normalize().dropna().drop_duplicates().sort_values()
    return list(dates)


def _day_map(frame: pd.DataFrame, day: pd.Timestamp) -> dict[str, pd.Series]:
    rows = frame.loc[frame["trade_date"].eq(day)]
    return {str(row.ts_code): row for row in rows.itertuples(index=False)}


def _limit_up(price: float, previous: float | None) -> bool:
    if previous is None or pd.isna(previous) or previous <= 0 or pd.isna(price):
        return False
    return float(price) >= round(float(previous) * _LIMIT_UP + 1e-8, 2) - 1e-8


def _limit_down(price: float, previous: float | None) -> bool:
    if previous is None or pd.isna(previous) or previous <= 0 or pd.isna(price):
        return False
    return float(price) <= round(float(previous) * _LIMIT_DOWN + 1e-8, 2) + 1e-8


def _lots(shares: float) -> int:
    if shares <= 0:
        return 0
    return int(float(shares) // LOT_SIZE) * LOT_SIZE


def _number(row: object, name: str) -> float:
    if row is None:
        return float("nan")
    value = getattr(row, name, float("nan"))
    return float(pd.to_numeric(value, errors="coerce"))


def _trade(day: pd.Timestamp, code: str, side: str, shares: int, price: float, reason: str) -> dict[str, object]:
    return {"trade_date": day, "ts_code": code, "side": side, "shares": int(shares), "price": float(price), "reason": reason}
