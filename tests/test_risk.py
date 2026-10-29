"""月末轮换、止损和指数仓位。不访问网络。"""

import pandas as pd
import pytest

from strategy.risk_control import average_true_range, index_exposure, month_end_book, stop_fill, stop_price


def test_names_outside_the_top_15_are_sold_and_new_leaders_are_bought():
    scores = pd.DataFrame(
        {
            "ts_code": [f"{i:06d}.SZ" for i in range(1, 18)],
            "composite": list(range(17, 0, -1)),
        }
    )
    book = month_end_book(scores, held=["000016.SZ", "000010.SZ"], top_n=8)
    assert book == ["000010.SZ", *[f"{i:06d}.SZ" for i in range(1, 9)]]


def test_hard_stop_triggers_at_an_8_percent_loss_and_not_before():
    cost = 10.0
    level = stop_price(cost, peak=10.0, atr=None)
    assert level == pytest.approx(9.2)
    assert stop_fill(10.0, 9.3, level) is None
    assert stop_fill(10.0, 9.2, level) == pytest.approx(9.2)
    assert stop_fill(9.0, 8.8, level) == pytest.approx(9.0)


def test_tighter_atr_trail_replaces_the_hard_stop():
    level = stop_price(cost=10.0, peak=12.0, atr=0.5)
    assert level == 11.0


def test_fourteen_day_true_range_of_two_is_the_atr():
    rows = [
        {
            "ts_code": "000001.SZ",
            "trade_date": pd.Timestamp("2024-01-02") + pd.Timedelta(days=i),
            "high": 11.0,
            "low": 9.0,
            "close": 10.0,
        }
        for i in range(14)
    ]
    atr = average_true_range(pd.DataFrame(rows))
    assert atr["atr"].iloc[-1] == pytest.approx(2.0)
    assert pd.isna(atr["atr"].iloc[12])


def test_index_below_one_average_cuts_exposure_in_half():
    old = [10.0] * 40
    recent = [100.0] * 19 + [95.0]
    bars = pd.DataFrame({"trade_date": pd.bdate_range("2024-01-02", periods=60), "close": old + recent})
    assert index_exposure(bars, bars["trade_date"].iloc[-1]) == 0.5


def test_index_below_both_averages_cuts_exposure_to_30_percent():
    closes = [100.0] * 59 + [90.0]
    bars = pd.DataFrame({"trade_date": pd.bdate_range("2024-01-02", periods=60), "close": closes})
    assert index_exposure(bars, bars["trade_date"].iloc[-1]) == 0.3
