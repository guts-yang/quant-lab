"""沪深300成数阶梯的纯决策。不调用券商运行时。"""

from ptrade.hs300_tenths_ladder import (
    band_ratio,
    entry_shares,
    etf_limit_price,
    order_delta,
    should_open_base,
    target_shares,
    update_extremes,
)


def test_profit_bands_hold_through_twenty_percent():
    assert band_ratio(0.10, 0.0, 0.10) == 1.0
    assert band_ratio(0.20, 0.0, 0.20) == 1.0
    assert band_ratio(0.29, 0.0, 0.29) == 1.0
    assert band_ratio(0.30, 0.0, 0.30) == 0.9


def test_loss_bands_hold_through_ten_percent():
    assert band_ratio(0.0, -0.10, -0.10) == 1.0
    assert band_ratio(0.0, -0.20, -0.20) == 1.1
    assert band_ratio(0.0, -0.30, -0.30) == 1.3
    assert band_ratio(0.0, -0.50, -0.50) == 1.5
    assert band_ratio(0.0, -1.0, -1.0) == 2.0


def test_higher_profit_band_replaces_the_lower_one():
    assert band_ratio(0.40, 0.0, 0.40) == 0.8
    assert band_ratio(0.50, 0.0, 0.50) == 0.7
    assert band_ratio(0.60, 0.0, 0.60) == 0.6
    assert band_ratio(0.60, 0.0, 0.40) == 0.6
    assert band_ratio(1.0, -0.50, 1.0) == 0.0
    assert band_ratio(1.2, -0.50, -0.20) == 0.0


def test_pullback_after_a_sale_does_not_buy_back():
    assert band_ratio(0.40, 0.0, 0.20) == 0.8


def test_add_then_thirty_percent_profit_cuts_back_to_point_nine():
    assert band_ratio(0.0, -0.30, -0.30) == 1.3
    assert band_ratio(0.30, -0.30, -0.05) == 1.3
    assert band_ratio(0.30, -0.30, 0.30) == 0.9


def test_above_anchor_after_both_sides_uses_only_the_reduce_band():
    assert band_ratio(0.40, -0.30, 0.05) == 0.8


def test_target_shares_round_down_to_a_lot_and_clear_is_zero():
    assert target_shares(1_000, 0.9) == 900
    assert target_shares(1_000, 1.1) == 1_100
    assert target_shares(1_000, 0.0) == 0
    assert target_shares(150, 1.1) == 100
    assert target_shares(100, 0.9) == 0


def test_order_delta_caps_sells_and_buys():
    assert order_delta(1_000, 1_000, 900, 0.0, 4.0) == -100
    assert order_delta(1_000, 100, 800, 0.0, 4.0) == -100
    assert order_delta(250, 250, 0, 0.0, 4.0) == -250
    assert order_delta(1_000, 0, 0, 0.0, 4.0) == 0
    assert order_delta(0, 0, 1_000, 10_000.0, 4.0) == 1_000
    assert order_delta(0, 0, 1_000, 1_000.0, 4.0) == 200
    assert order_delta(1_000, 1_000, 1_000, 10_000.0, 4.0) == 0


def test_new_base_waits_until_the_day_after_a_clear():
    assert should_open_base(False, "20240102", "20240102") is False
    assert should_open_base(False, "20240102", "20240103") is True
    assert should_open_base(True, "20240102", "20240103") is False
    assert should_open_base(False, "", "20240103") is True


def test_entry_size_is_half_the_equity_in_lots():
    assert entry_shares(100_000.0, 4.0) == 12_500
    assert entry_shares(100_000.0, 3.0) == 16_600
    high, low = update_extremes(0.2, -0.1, 0.4)
    assert (high, low) == (0.4, -0.1)
    assert update_extremes(None, None, -0.2) == (-0.2, -0.2)
    assert etf_limit_price(1.2345) == 1.235
    assert etf_limit_price(4.2) == 4.2
