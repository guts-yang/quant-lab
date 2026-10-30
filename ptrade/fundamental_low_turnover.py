"""低换手基本面多因子的 PTrade 日线脚本。

在 PTrade 客户端把本文件作为策略，频率选日线。选股仍由研究侧完成：
盘前读取研究目录中的 fundamental_book.json。本文件只做整手调仓、止损和涨跌停保护。

上海股票在 PTrade 中使用 .SS，研究侧使用 .SH。日线 handle_data 在盘后触发，
实盘委托可能到下一交易日才成交。回测印花税按柜台手册为卖出千分之一，
研究引擎使用万五，两套数字不能直接对比。
"""

from __future__ import annotations

import json
from datetime import datetime

TOP_N = 8
EXIT_RANK = 15
HARD_STOP = 0.08
ATR_WINDOW = 14
ATR_MULTIPLE = 2.0
FAST_MA = 20
SLOW_MA = 60
BENCHMARK = "000300.SS"
BOOK_NAME = "fundamental_book.json"


def to_ptrade(code: str) -> str:
    """研究代码转成 PTrade 代码。上海后缀 .SH 对应手册里的 .SS。"""
    text = str(code).strip().upper()
    if text.endswith(".SH"):
        return text[:-3] + ".SS"
    return text


def to_research(code: str) -> str:
    text = str(code).strip().upper()
    if text.endswith(".SS"):
        return text[:-3] + ".SH"
    return text


def round_limit_price(price: float) -> float:
    """股票限价保留两位小数，避免柜台拒单。"""
    return round(float(price) + 1e-8, 2)


def is_month_end(today: datetime, next_day: datetime) -> bool:
    return (today.year, today.month) != (next_day.year, next_day.month)


def index_exposure(closes: list[float]) -> float:
    """收盘低于一条均线时仓位 50%，两条都低于时 30%。"""
    if len(closes) < FAST_MA:
        return 1.0
    last = float(closes[-1])
    below_fast = last < sum(closes[-FAST_MA:]) / FAST_MA
    below_slow = len(closes) >= SLOW_MA and last < sum(closes[-SLOW_MA:]) / SLOW_MA
    if below_fast and below_slow:
        return 0.30
    if below_fast or below_slow:
        return 0.50
    return 1.0


def average_true_range(highs: list[float], lows: list[float], closes: list[float]) -> float | None:
    if len(closes) < ATR_WINDOW:
        return None
    ranges: list[float] = []
    for index, (high, low, close) in enumerate(zip(highs, lows, closes)):
        if index == 0:
            ranges.append(float(high) - float(low))
            continue
        previous = float(closes[index - 1])
        ranges.append(max(float(high) - float(low), abs(float(high) - previous), abs(float(low) - previous)))
    window = ranges[-ATR_WINDOW:]
    return sum(window) / ATR_WINDOW


def stop_level(cost: float, peak: float | None, atr: float | None) -> float:
    hard = float(cost) * (1.0 - HARD_STOP)
    if peak is None or atr is None:
        return hard
    return max(hard, float(peak) - ATR_MULTIPLE * float(atr))


def select_book(ranked: list[str], held: list[str], top_n: int = TOP_N, exit_rank: int = EXIT_RANK) -> list[str]:
    """跌出前 exit_rank 的持仓卖出，前 top_n 的新名字买入，其余持仓留下。"""
    rank = {code: index + 1 for index, code in enumerate(ranked)}
    kept = [code for code in held if rank.get(code, exit_rank + 1) <= exit_rank]
    kept = sorted(set(kept), key=lambda code: (rank[code], code))
    owned = set(kept)
    fresh = [code for code in ranked if rank[code] <= top_n and code not in owned]
    return kept + fresh


def lot_shares(target_value: float, price: float) -> int:
    if price <= 0 or target_value <= 0:
        return 0
    return int(float(target_value) / float(price) // 100) * 100


def plan_orders(
    ranked: list[str],
    positions: dict[str, int],
    prices: dict[str, float],
    portfolio_value: float,
    exposure: float,
    limit_state: dict[str, int] | None = None,
) -> list[tuple[str, int]]:
    """先卖后买。涨停不买，跌停不卖。返回 (代码, 目标股数)。"""
    book = select_book(ranked, [code for code, amount in positions.items() if amount > 0])
    count = len(book)
    target_value = portfolio_value * exposure / count if count else 0.0
    limits = limit_state or {}
    planned: list[tuple[str, int]] = []
    for code, amount in positions.items():
        if amount > 0 and code not in book and limits.get(code, 0) not in (-1, -2):
            planned.append((code, 0))
    for code in book:
        price = prices.get(code)
        if price is None or price <= 0:
            continue
        target = lot_shares(target_value, price)
        current = int(positions.get(code, 0))
        state = limits.get(code, 0)
        if target > current and state in (1, 2):
            continue
        if target < current and state in (-1, -2):
            continue
        if target != current:
            planned.append((code, target))
    return sorted(planned, key=lambda item: (item[1] >= positions.get(item[0], 0), item[0]))


def load_ranked(payload: dict) -> list[str]:
    rows = payload.get("ranked") or []
    ordered = sorted(rows, key=lambda row: (int(row.get("rank", 10**9)), str(row.get("ts_code", ""))))
    return [to_ptrade(row["ts_code"]) for row in ordered if row.get("ts_code")]


def initialize(context):
    g.top_n = TOP_N
    g.exit_rank = EXIT_RANK
    g.ranked = []
    g.peaks = {}
    g.last_order_day = ""
    set_benchmark(BENCHMARK)
    set_commission(commission_ratio=0.00025, min_commission=5.0, type="STOCK")
    set_slippage(slippage=0.002)
    set_universe(BENCHMARK)


def before_trading_start(context, data):
    try:
        path = get_research_path() + BOOK_NAME
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        g.ranked = load_ranked(payload)
        universe = list(dict.fromkeys([*g.ranked[: g.exit_rank], BENCHMARK]))
        if universe:
            set_universe(universe)
        log.info("book asof=%s names=%s" % (payload.get("asof"), len(g.ranked)))
    except Exception as exc:
        g.ranked = []
        log.info("没有读到选股文件，今天不新开仓: %s" % exc)


def handle_data(context, data):
    today = context.blotter.current_dt
    day_key = today.strftime("%Y%m%d")
    if g.last_order_day == day_key:
        return
    _update_peaks(context)
    _stop_positions(context, data)
    next_day = get_trading_day(1)
    if is_month_end(today, next_day):
        _rebalance(context, data)
    g.last_order_day = day_key


def after_trading_end(context, data):
    log.info("equity=%s cash=%s" % (context.portfolio.portfolio_value, context.portfolio.cash))


def _update_peaks(context):
    for code, position in context.portfolio.positions.items():
        if getattr(position, "amount", 0) <= 0:
            g.peaks.pop(code, None)
            continue
        history = get_history(1, "1d", "high", code, fq=None, include=False)
        if history is None or len(history) == 0:
            continue
        high = float(history["high"].iloc[-1])
        g.peaks[code] = max(float(g.peaks.get(code, high)), high)


def _stop_positions(context, data):
    for code, position in list(context.portfolio.positions.items()):
        amount = int(getattr(position, "amount", 0) or 0)
        if amount <= 0:
            continue
        history = get_history(ATR_WINDOW + 1, "1d", ["high", "low", "close"], code, fq=None, include=False)
        atr = None
        if history is not None and len(history) > 0:
            atr = average_true_range(list(history["high"]), list(history["low"]), list(history["close"]))
        cost = float(getattr(position, "cost_basis", 0) or 0)
        level = stop_level(cost, g.peaks.get(code), atr)
        bar = data[code]
        low = float(bar["low"])
        if low > level:
            continue
        state = check_limit(code).get(code, 0)
        if state in (-1, -2):
            log.info("%s 跌停，止损顺延" % code)
            continue
        open_price = float(bar["open"])
        fill = open_price if open_price < level else level
        order_target(code, 0, limit_price=round_limit_price(fill))
        g.peaks.pop(code, None)


def _rebalance(context, data):
    positions = {
        code: int(getattr(position, "amount", 0) or 0) for code, position in context.portfolio.positions.items()
    }
    prices = {}
    for code in list(dict.fromkeys([*g.ranked[:EXIT_RANK], *positions])):
        if code not in data:
            continue
        prices[code] = float(data[code]["close"])
    limits = check_limit(list(prices)) if prices else {}
    closes = _benchmark_closes()
    orders = plan_orders(
        g.ranked,
        positions,
        prices,
        float(context.portfolio.portfolio_value),
        index_exposure(closes),
        limits,
    )
    for code, amount in orders:
        order_target(code, int(amount), limit_price=round_limit_price(prices.get(code, data[code]["close"])))


def _benchmark_closes() -> list[float]:
    history = get_history(SLOW_MA, "1d", "close", BENCHMARK, fq=None, include=True)
    if history is None or len(history) == 0:
        return []
    return [float(value) for value in history["close"]]
