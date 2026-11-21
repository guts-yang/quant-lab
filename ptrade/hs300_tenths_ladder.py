"""沪深300成数阶梯。仅供参考，不作投资建议。

把「涨了按档减仓、跌了按档补仓」写成日线脚本，只做华泰柏瑞沪深300ETF（510300.SS）。
指数 000300.SS 只作为回测基准，不下单。

一成是本轮初始股数的 10%。锚价是第一笔底仓的成交成本，补仓后不改。
涨 10%、涨 20%、跌 10% 不交易。减仓看本轮最高涨幅，补仓看本轮最大跌幅，
高档覆盖低档，已经减过的不在更低涨幅上买回。涨到 100% 清仓，下一交易日再买五成。

在 PTrade 客户端把本文件作为策略，频率选日线。日线 handle_data 在盘后触发，
实盘委托可能到下一交易日才成交。
"""

from __future__ import annotations

import json

SECURITY = "510300.SS"
BENCHMARK = "000300.SS"
STATE_NAME = "hs300_tenths_ladder_state.json"
BASE_WEIGHT = 0.50
CASH_BUFFER = 0.98
LOT = 100


def band_ratio(max_return: float, min_return: float, current_return: float) -> float:
    """本轮最高、最低涨跌幅对应的目标股数比例。1.0 是初始股数。"""
    if max_return >= 1.0:
        return 0.0
    if max_return >= 0.30:
        reduced = _reduce_ratio(max_return)
        if current_return < 0:
            return max(reduced, _add_ratio(min_return))
        return reduced
    return _add_ratio(min_return)


def target_shares(base_shares: int, ratio: float) -> int:
    """按初始股数换算目标股数。清仓为 0，其余向下取整到 100 股。"""
    if ratio <= 0 or base_shares <= 0:
        return 0
    return int(float(base_shares) * float(ratio) // LOT) * LOT


def order_delta(current: int, enable: int, target: int, cash: float, price: float) -> int:
    """目标股数和现仓的差额。卖出不超过可卖数量，买入留下约 2% 现金。"""
    current = int(current)
    enable = max(0, min(int(enable), current))
    target = max(0, int(target))
    if target == current:
        return 0
    if target < current:
        if target == 0:
            return -enable
        sell = min(current - target, enable)
        sell = int(sell // LOT) * LOT
        return -sell
    if price <= 0 or cash <= 0:
        return 0
    buy = int((target - current) // LOT) * LOT
    affordable = int((float(cash) * CASH_BUFFER) / float(price) // LOT) * LOT
    return max(0, min(buy, affordable))


def should_open_base(has_position: bool, cleared_day: str, today: str) -> bool:
    """空仓才开底仓。清仓当天不再开新的一轮。"""
    if has_position:
        return False
    return str(cleared_day or "") != str(today)


def entry_shares(portfolio_value: float, price: float, weight: float = BASE_WEIGHT) -> int:
    """用总权益的一部分买底仓，股数向下取整到 100。"""
    if price <= 0 or portfolio_value <= 0 or weight <= 0:
        return 0
    return int(float(portfolio_value) * float(weight) / float(price) // LOT) * LOT


def update_extremes(
    max_return: float | None,
    min_return: float | None,
    current_return: float,
) -> tuple[float, float]:
    current = float(current_return)
    high = current if max_return is None else max(float(max_return), current)
    low = current if min_return is None else min(float(min_return), current)
    return high, low


def etf_limit_price(price: float) -> float:
    """ETF 限价保留三位小数，避免柜台拒单。"""
    return round(float(price) + 1e-8, 3)


def _reduce_ratio(max_return: float) -> float:
    if max_return >= 1.0:
        return 0.0
    if max_return >= 0.60:
        return 0.60
    if max_return >= 0.50:
        return 0.70
    if max_return >= 0.40:
        return 0.80
    if max_return >= 0.30:
        return 0.90
    return 1.0


def _add_ratio(min_return: float) -> float:
    if min_return <= -1.0:
        return 2.0
    if min_return <= -0.50:
        return 1.5
    if min_return <= -0.30:
        return 1.3
    if min_return <= -0.20:
        return 1.1
    return 1.0


def initialize(context):
    g.security = SECURITY
    g.anchor = None
    g.base_shares = None
    g.max_return = None
    g.min_return = None
    g.last_order_day = ""
    g.round_id = 0
    g.cleared_day = ""
    g.pending_entry = False
    set_benchmark(BENCHMARK)
    set_commission(commission_ratio=0.0008, min_commission=5.0, type="ETF")
    set_slippage(slippage=0.002)
    set_universe(SECURITY)


def before_trading_start(context, data):
    try:
        path = get_research_path() + STATE_NAME
        with open(path, encoding="utf-8") as handle:
            _apply_state(json.load(handle))
        log.info("ladder round=%s anchor=%s base=%s" % (g.round_id, g.anchor, g.base_shares))
    except Exception as exc:
        log.info("没有读到阶梯状态，今天按空仓或当前持仓处理: %s" % exc)


def handle_data(context, data):
    today = context.blotter.current_dt
    day_key = today.strftime("%Y%m%d")
    if g.last_order_day == day_key:
        return
    price = _bar_close(data)
    if price is None or price <= 0:
        log.info("没有 %s 的收盘价，今天不调仓" % SECURITY)
        return
    amount, enable, cost = _read_position()
    cash = float(context.portfolio.cash)
    if amount <= 0:
        _open_base(day_key, price, float(context.portfolio.portfolio_value), cash)
    else:
        _manage_position(day_key, price, amount, enable, cost, cash)
    g.last_order_day = day_key
    _save_state()


def after_trading_end(context, data):
    _save_state()
    log.info("equity=%s cash=%s round=%s" % (context.portfolio.portfolio_value, context.portfolio.cash, g.round_id))


def _open_base(day_key: str, price: float, equity: float, cash: float) -> None:
    if not should_open_base(False, g.cleared_day, day_key):
        log.info("今天已清仓，下一交易日再开底仓")
        return
    g.anchor = None
    g.base_shares = None
    g.max_return = None
    g.min_return = None
    g.pending_entry = False
    limit_flag = _limit_flag()
    if limit_flag is None:
        return
    if limit_flag in (1, 2):
        log.info("%s 涨停，底仓顺延" % SECURITY)
        return
    shares = entry_shares(equity, price)
    delta = order_delta(0, 0, shares, cash, price)
    if delta <= 0:
        log.info("权益或现金不够买一手 %s，今天不开仓" % SECURITY)
        return
    g.pending_entry = True
    g.last_order_day = day_key
    _save_state()
    order(SECURITY, int(delta), limit_price=etf_limit_price(price))
    if delta < shares:
        log.info("开底仓现金不够买到 %s 股，这笔先买 %s" % (shares, delta))
    else:
        log.info("开底仓，委托 %s 股" % delta)


def _manage_position(day_key: str, price: float, amount: int, enable: int, cost: float, cash: float) -> None:
    if not g.base_shares or not g.anchor:
        if cost <= 0:
            log.info("持仓成本还没有返回，今天不调仓")
            return
        seeded = not g.pending_entry
        g.anchor = float(cost)
        g.base_shares = int(amount)
        g.max_return = 0.0
        g.min_return = 0.0
        g.pending_entry = False
        g.cleared_day = ""
        g.round_id = int(g.round_id or 0) + 1
        if seeded:
            log.info(
                "没有阶梯状态，按当前成本 %s 和 %s 股重新钉一轮，此前高低点无法还原"
                % (g.anchor, g.base_shares)
            )
        else:
            log.info("锁定第 %s 轮底仓，成本 %s，股数 %s" % (g.round_id, g.anchor, g.base_shares))
    current_return = float(price) / float(g.anchor) - 1.0
    g.max_return, g.min_return = update_extremes(g.max_return, g.min_return, current_return)
    ratio = band_ratio(float(g.max_return), float(g.min_return), current_return)
    if ratio <= 0:
        target = 0
        g.cleared_day = day_key
    else:
        target = target_shares(int(g.base_shares), ratio)
        # 比例还没到清仓，但目标不足一手时留在现仓，避免被当成卖光。
        if target == 0:
            target = int(amount)
    limit_flag = _limit_flag()
    if limit_flag is None:
        return
    delta = order_delta(amount, enable, target, cash, price)
    if delta > 0 and limit_flag in (1, 2):
        log.info("%s 涨停，买入顺延" % SECURITY)
        return
    if delta < 0 and limit_flag in (-1, -2):
        log.info("%s 跌停，卖出顺延" % SECURITY)
        return
    if delta == 0:
        if target < amount:
            log.info("%s 可卖不足，调仓顺延" % SECURITY)
        elif target > amount:
            log.info("%s 现金不够或不足一手，买入顺延" % SECURITY)
        return
    g.last_order_day = day_key
    _save_state()
    order(SECURITY, int(delta), limit_price=etf_limit_price(price))
    log.info(
        "%s 目标 %s 股，委托 %+d，相对锚价 %.1f%%"
        % (SECURITY, target, int(delta), current_return * 100.0)
    )
    if delta > 0 and target - amount - delta >= LOT:
        log.info("%s 现金不够买到目标 %s 股，这笔先买 %s" % (SECURITY, target, delta))


def _bar_close(data) -> float | None:
    try:
        return float(data[SECURITY]["close"])
    except Exception:
        return None


def _read_position() -> tuple[int, int, float]:
    try:
        position = get_position(SECURITY)
    except Exception:
        position = None
    if position is None:
        return 0, 0, 0.0
    amount = int(getattr(position, "amount", 0) or 0)
    enable = int(getattr(position, "enable_amount", 0) or 0)
    cost = float(getattr(position, "cost_basis", 0) or 0)
    return amount, max(0, min(enable, amount)), cost


def _limit_flag() -> int | None:
    try:
        return int(check_limit(SECURITY).get(SECURITY, 0))
    except Exception as exc:
        log.info("涨跌停状态不可用，今天不调仓: %s" % exc)
        return None


def _apply_state(state: dict) -> None:
    anchor = state.get("anchor")
    g.anchor = float(anchor) if anchor else None
    base = state.get("base_shares")
    g.base_shares = int(base) if base else None
    max_return = state.get("max_return")
    g.max_return = None if max_return is None else float(max_return)
    min_return = state.get("min_return")
    g.min_return = None if min_return is None else float(min_return)
    g.last_order_day = str(state.get("last_order_day") or "")
    g.round_id = int(state.get("round_id") or 0)
    g.cleared_day = str(state.get("cleared_day") or "")
    g.pending_entry = bool(state.get("pending_entry"))


def _dump_state() -> dict:
    return {
        "anchor": g.anchor,
        "base_shares": g.base_shares,
        "max_return": g.max_return,
        "min_return": g.min_return,
        "last_order_day": g.last_order_day,
        "round_id": g.round_id,
        "cleared_day": g.cleared_day,
        "pending_entry": bool(g.pending_entry),
    }


def _save_state() -> None:
    try:
        path = get_research_path() + STATE_NAME
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_dump_state(), handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        log.info("阶梯状态没有写入: %s" % exc)
