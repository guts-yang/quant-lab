"""PTrade 脚本里的纯决策。不调用券商运行时。"""

from datetime import datetime

import pandas as pd

from ptrade.fundamental_low_turnover import (
    index_exposure,
    is_month_end,
    load_ranked,
    plan_orders,
    round_limit_price,
    select_book,
    stop_level,
    to_ptrade,
    to_research,
)
from strategy.risk_control import month_end_book


def test_shanghai_suffix_matches_ptrade_manual():
    assert to_ptrade("600570.SH") == "600570.SS"
    assert to_ptrade("000001.SZ") == "000001.SZ"
    assert to_research("600570.SS") == "600570.SH"
    assert round_limit_price(10.239) == 10.24


def test_book_buffer_matches_research_rule():
    ranked = [f"{i:06d}.SZ" for i in range(1, 18)]
    held = ["000016.SZ", "000010.SZ"]
    assert select_book(ranked, held) == ["000010.SZ", *[f"{i:06d}.SZ" for i in range(1, 9)]]
    scores = pd.DataFrame({"ts_code": ranked, "composite": list(range(len(ranked), 0, -1))})
    assert month_end_book(scores, held) == select_book(ranked, held)


def test_orders_sell_before_buy_and_skip_limit_moves():
    ranked = ["600001.SS", "000002.SZ"]
    positions = {"600001.SS": 1000, "000009.SZ": 500}
    prices = {"600001.SS": 10.0, "000002.SZ": 20.0, "000009.SZ": 8.0}
    orders = plan_orders(ranked, positions, prices, portfolio_value=40_000, exposure=1.0)
    assert orders[0][0] == "000009.SZ"
    assert orders[0][1] == 0
    blocked = plan_orders(ranked, positions, prices, 40_000, 1.0, {"000002.SZ": 1, "000009.SZ": -1})
    assert all(code != "000002.SZ" for code, _ in blocked)
    assert all(code != "000009.SZ" for code, _ in blocked)


def test_month_end_exposure_and_stop_use_prior_rules():
    assert is_month_end(datetime(2024, 1, 31), datetime(2024, 2, 1))
    assert not is_month_end(datetime(2024, 1, 30), datetime(2024, 1, 31))
    assert index_exposure([100.0] * 59 + [90.0]) == 0.3
    assert stop_level(10.0, 12.0, 0.5) == 11.0
    ranked = load_ranked({"ranked": [{"ts_code": "600000.SH", "rank": 2}, {"ts_code": "000001.SZ", "rank": 1}]})
    assert ranked == ["000001.SZ", "600000.SS"]
