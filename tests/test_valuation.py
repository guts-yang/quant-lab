"""DCF 引擎、假设推导和 PIT 对齐的合成数据测试。不访问网络。"""

import math

import pandas as pd
import pytest

from valuation.assumptions import (
    CompanyFundamentals,
    IndustryStats,
    ValuationConfig,
    derive_base_inputs,
    derive_scenarios,
    select_template,
)
from valuation.data import annual_values, company_fundamentals, ttm_at, ttm_growth_per_code, ttm_per_code, visible_frame
from valuation.discount import cost_of_equity, estimate_beta
from valuation.model import (
    DcfInputs,
    average_growth,
    dcf_value,
    growth_path,
    margin_path,
    reverse_dcf,
    sensitivity_grid,
)
from valuation.scenario import forecast_table, security_label


def _inputs(**overrides) -> DcfInputs:
    base = {
        "revenue_base": 100.0,
        "growth_start": 0.10,
        "growth_terminal": 0.10,
        "margin_start": 0.10,
        "margin_terminal": 0.10,
        "cash_conversion": 1.0,
        "forecast_years": 2,
        "discount_rate": 0.10,
        "exit_multiple": 1.0,
        "exit_basis": "ps",
        "shares": 10.0,
    }
    return DcfInputs(**{**base, **overrides})


def test_growth_path_is_linear_between_endpoints():
    assert growth_path(0.2, 0.0, 3) == [pytest.approx(0.2), pytest.approx(0.1), pytest.approx(0.0)]
    assert growth_path(0.15, 0.03, 1) == [pytest.approx(0.15)]
    with pytest.raises(ValueError):
        growth_path(0.1, 0.0, 0)


def test_margin_path_lands_on_terminal_at_final_year():
    path = margin_path(0.5, 0.4, 5)
    assert path[0] == pytest.approx(0.48)
    assert path[-1] == pytest.approx(0.4)


def test_security_label_leads_with_the_company_name():
    assert security_label("中际旭创", "300308.SZ") == "中际旭创（300308.SZ）"
    assert security_label(None, "300308.SZ") == "300308.SZ"
    assert security_label("  ", "300308.SZ") == "300308.SZ"


def test_forecast_table_stacks_three_scenarios_and_keeps_base_numbers():
    bear = dcf_value(_inputs(growth_start=0.05))
    base = dcf_value(_inputs())
    bull = dcf_value(_inputs(growth_start=0.20))
    table = forecast_table({"base": base, "bull": bull, "bear": bear})
    assert list(table["scenario"].drop_duplicates()) == ["bear", "base", "bull"]
    base_rows = table.loc[table["scenario"].eq("base")].reset_index(drop=True)
    assert list(base_rows["revenue"]) == pytest.approx(list(base.projection["revenue"]))
    assert "scenario" in table.columns


def test_dcf_value_matches_hand_computation():
    # 收入 100，增速 10% 不变，净利率 10%，转换率 1，两年，折现率 10%，退出 1x P/S，10 股。
    # FCFE1 = 110×0.1 = 11，FCFE2 = 121×0.1 = 12.1，TV = 121×1 = 121
    result = dcf_value(_inputs())
    expected_pv = 11 / 1.1 + 12.1 / 1.21 + 121 / 1.21
    assert result.pv_explicit == pytest.approx(11 / 1.1 + 12.1 / 1.21)
    assert result.terminal_value == pytest.approx(121.0)
    assert result.equity_value == pytest.approx(expected_pv)
    assert result.per_share == pytest.approx(expected_pv / 10)
    assert result.terminal_weight == pytest.approx(100 / expected_pv)


def test_pe_exit_basis_uses_net_income():
    result = dcf_value(_inputs(exit_basis="pe", exit_multiple=8.0))
    assert result.terminal_value == pytest.approx(12.1 * 8.0)


def test_gordon_terminal_requires_discount_above_growth():
    result = dcf_value(_inputs(terminal_method="gordon", perpetuity_growth=0.03))
    assert result.terminal_value == pytest.approx(12.1 * 1.03 / 0.07)
    with pytest.raises(ValueError):
        dcf_value(_inputs(terminal_method="gordon", perpetuity_growth=0.10))


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        dcf_value(_inputs(shares=0))
    with pytest.raises(ValueError):
        dcf_value(_inputs(revenue_base=-1))
    with pytest.raises(ValueError):
        dcf_value(_inputs(exit_basis="ev"))


def test_reverse_dcf_recovers_implied_growth():
    target = _inputs(growth_start=0.25, growth_terminal=0.03, forecast_years=7)
    price = dcf_value(target).per_share
    implied = reverse_dcf(_inputs(growth_start=0.0, growth_terminal=0.03, forecast_years=7), price)
    assert implied.converged
    assert implied.growth_start == pytest.approx(0.25, abs=1e-6)
    assert implied.per_share == pytest.approx(price, rel=1e-6)


def test_reverse_dcf_flags_unconverged_at_boundary():
    implied = reverse_dcf(_inputs(), price=1e9)
    assert not implied.converged


def test_sensitivity_moves_the_right_direction():
    grid = sensitivity_grid(_inputs(), growth_deltas=(-0.05, 0.0, 0.05), discount_deltas=(-0.01, 0.0, 0.01))
    center = grid.loc[0.10, 0.10]
    assert grid.loc[0.10, 0.15] > center  # 增速越高价值越大
    assert grid.loc[0.11, 0.10] < center  # 折现率越高价值越小
    assert center == pytest.approx(dcf_value(_inputs()).per_share)


def _fundamentals(**overrides) -> CompanyFundamentals:
    base = {
        "ts_code": "600519.SH",
        "industry": "白酒",
        "comp_type": "1",
        "price": 100.0,
        "total_mv": 1e11,
        "shares": 1e9,
        "revenue_ttm": 5e10,
        "net_income_ttm": 2.5e10,
        "ocf_ttm": 2.6e10,
        "fcf_ttm": 2.2e10,
        "revenue_cagr_5y": 0.20,
        "margin_ttm": 0.50,
        "margin_mean_5y": 0.45,
        "fcf_conversion": 0.88,
    }
    return CompanyFundamentals(**{**base, **overrides})


def _industry(**overrides) -> IndustryStats:
    base = {
        "industry": "白酒",
        "peers": 12,
        "growth_median": 0.10,
        "growth_p25": 0.05,
        "growth_p75": 0.18,
        "margin_median": 0.30,
        "margin_p25": 0.20,
        "margin_p75": 0.40,
        "ps_median": 4.0,
        "ps_p25": 2.5,
        "ps_p75": 6.0,
        "pe_median": 15.0,
        "pe_p25": 10.0,
        "pe_p75": 22.0,
    }
    return IndustryStats(**{**base, **overrides})


def test_base_growth_is_capped_by_industry_base_rate():
    config = ValuationConfig()
    inputs = derive_base_inputs(_fundamentals(), _industry(), 0.10, config)
    # 公司 5 年 CAGR 20%，行业中位 10%×1.5 = 15%，取低。
    assert inputs.growth_start == pytest.approx(0.15)
    assert inputs.growth_terminal == pytest.approx(config.terminal_growth)
    # 终值净利率：max(公司 5 年均值 45%, 行业中位 30%)，再与当前 50% 取低 → 45%。
    assert inputs.margin_terminal == pytest.approx(0.45)
    assert inputs.cash_conversion == pytest.approx(0.88)
    assert inputs.exit_basis == "ps"
    assert inputs.exit_multiple == pytest.approx(4.0)


def test_estimates_blend_into_base_growth():
    config = ValuationConfig()
    inputs = derive_base_inputs(_fundamentals(), _industry(), 0.10, config, {"revenue_growth_next": 0.21})
    assert inputs.growth_start == pytest.approx(0.5 * 0.15 + 0.5 * 0.21)


def test_financial_template_uses_net_income_and_pe_exit():
    fundamentals = _fundamentals(comp_type="2")
    assert select_template(fundamentals) == "net_income"
    inputs = derive_base_inputs(fundamentals, _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion == 1.0
    assert inputs.exit_basis == "pe"
    assert inputs.exit_multiple == pytest.approx(15.0)


def test_scenarios_are_ordered_bear_below_base_below_bull():
    scenarios = derive_scenarios(derive_base_inputs(_fundamentals(), _industry(), 0.10, ValuationConfig()), _industry(), ValuationConfig())
    values = {name: dcf_value(inputs).per_share for name, inputs in scenarios.items()}
    assert values["bear"] < values["base"] < values["bull"]
    assert scenarios["bear"].discount_rate > scenarios["base"].discount_rate > scenarios["bull"].discount_rate


def test_missing_industry_falls_back_to_documented_constants():
    inputs = derive_base_inputs(_fundamentals(), None, 0.10, ValuationConfig())
    assert inputs.exit_multiple == pytest.approx(2.0)  # FALLBACK_EXIT_PS
    assert inputs.growth_start == pytest.approx(0.20)  # 只用公司历史 CAGR


def test_conversion_is_clamped():
    inputs = derive_base_inputs(_fundamentals(fcf_conversion=1.9), _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion == pytest.approx(1.2)


def _income_frame() -> pd.DataFrame:
    rows = [
        # 2023 年报：收入 100，归母净利 10（2024-04-01 公告）
        ("600519.SH", "2023-12-31", "2024-04-01", 100.0, 10.0),
        # 2024 一季报 YTD：收入 30（2024-04-25 公告）
        ("600519.SH", "2024-03-31", "2024-04-25", 30.0, 4.0),
        # 2024 中报 YTD：收入 70（2024-08-20 公告）
        ("600519.SH", "2024-06-30", "2024-08-20", 70.0, 9.0),
        # 2023 中报 YTD：收入 60（2023-08-15 公告）
        ("600519.SH", "2023-06-30", "2023-08-15", 60.0, 7.0),
        # 2023 一季报 YTD：收入 25（2023-04-20 公告）
        ("600519.SH", "2023-03-31", "2023-04-20", 25.0, 3.0),
        # 另一家同行业公司
        ("000858.SZ", "2023-12-31", "2024-04-01", 80.0, 8.0),
        ("000858.SZ", "2024-06-30", "2024-08-20", 50.0, 6.0),
        ("000858.SZ", "2023-06-30", "2023-08-15", 45.0, 5.0),
    ]
    frame = pd.DataFrame(rows, columns=["ts_code", "end_date", "ann_date", "total_revenue", "n_income_attr"])
    frame["end_date"] = pd.to_datetime(frame["end_date"])
    frame["ann_date"] = pd.to_datetime(frame["ann_date"])
    frame["report_type"] = "1"
    frame["comp_type"] = "1"
    frame["basic_eps"] = 1.0
    return frame


def test_ttm_at_uses_ytd_plus_fy_minus_same_quarter():
    frame = visible_frame(_income_frame(), pd.Timestamp("2024-09-01"))
    code_frame = frame.loc[frame["ts_code"].eq("600519.SH")]
    values = {row["end_date"]: row["total_revenue"] for _, row in code_frame.iterrows()}
    # 2024H1 TTM = 70 + 100 − 60 = 110
    assert ttm_at(values, pd.Timestamp("2024-06-30")) == pytest.approx(110.0)
    # 年报的 TTM 就是全年
    assert ttm_at(values, pd.Timestamp("2023-12-31")) == pytest.approx(100.0)


def test_ttm_per_code_picks_latest_announced_period():
    frame = visible_frame(_income_frame(), pd.Timestamp("2024-09-01"))
    table = ttm_per_code(frame, "total_revenue")
    row = table.loc[table["ts_code"].eq("600519.SH")].iloc[0]
    assert row["end_date"] == pd.Timestamp("2024-06-30")
    assert row["ttm"] == pytest.approx(110.0)


def test_ttm_growth_compares_four_quarters_back():
    frame = visible_frame(_income_frame(), pd.Timestamp("2024-09-01"))
    growth = ttm_growth_per_code(frame, "total_revenue")
    # 600519：2024H1 TTM 110 对 2023H1 TTM（60+? 2022 年报缺失 → 不可算）
    row = growth.loc[growth["ts_code"].eq("600519.SH")].iloc[0]
    assert pd.isna(row["growth"]) or row["growth"] is None


def test_visible_frame_blocks_announcements_after_asof():
    frame = visible_frame(_income_frame(), pd.Timestamp("2024-05-01"))
    ends = frame.loc[frame["ts_code"].eq("600519.SH"), "end_date"]
    assert pd.Timestamp("2024-06-30") not in set(ends)  # 8 月才公告，5 月看不到
    assert pd.Timestamp("2024-03-31") in set(ends)


def test_annual_values_only_counts_december_periods():
    annual = annual_values(visible_frame(_income_frame(), pd.Timestamp("2024-09-01")), "total_revenue")
    assert annual["600519.SH"] == {2023: 100.0}


def test_company_fundamentals_shares_derived_from_market_cap():
    income = visible_frame(_income_frame(), pd.Timestamp("2024-09-01"))
    cashflow = pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "ocf", "capex", "report_type", "comp_type"])
    instruments = pd.DataFrame(
        {
            "ts_code": ["600519.SH"],
            "list_date": [pd.Timestamp("2001-08-27")],
            "delist_date": [pd.NaT],
            "list_status": ["L"],
            "industry": ["白酒"],
            "name": ["贵州茅台"],
        }
    )
    daily_basic = pd.DataFrame(
        {"ts_code": ["600519.SH"], "trade_date": [pd.Timestamp("2024-08-30")], "close": [20.0], "total_mv": [2.2e10]}
    )
    fundamentals = company_fundamentals("600519.SH", income, cashflow, instruments, daily_basic)
    assert fundamentals.revenue_ttm == pytest.approx(110.0)
    assert fundamentals.margin_ttm == pytest.approx(12.0 / 110.0)  # NI TTM = 9+10−7 = 12
    assert fundamentals.shares == pytest.approx(2.2e10 / 20.0)
    assert fundamentals.industry == "白酒"
    assert fundamentals.name == "贵州茅台"
    assert fundamentals.comp_type == "1"


def _daily(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-02", periods=len(closes))
    return pd.DataFrame({"ts_code": "X", "trade_date": dates, "open": closes, "high": closes, "low": closes, "close": closes})


def test_beta_of_leveraged_index_copy_is_two_before_blume():
    # 日收益有正有负（常数序列会让周收益方差退化到浮点噪声）。
    rets = [0.0005 + 0.002 * math.sin(i / 7.0) for i in range(400)]
    index = _daily([100.0])
    closes = [100.0]
    for r in rets[1:]:
        closes.append(closes[-1] * (1.0 + r))
    index = _daily(closes)
    # 个股日收益恰为指数 2 倍，周收益复利后 beta 仍应接近 2。
    stock_close = [100.0]
    for r in rets[1:]:
        stock_close.append(stock_close[-1] * (1.0 + 2.0 * r))
    stock = _daily(stock_close)
    beta = estimate_beta(stock, index, blume=False)
    assert beta == pytest.approx(2.0, abs=0.05)
    blume = estimate_beta(stock, index, blume=True)
    assert blume == pytest.approx(0.67 * beta + 0.33, abs=0.05)


def test_beta_returns_none_when_history_is_short():
    frame = _daily([10.0 + 0.01 * i for i in range(60)])
    assert estimate_beta(frame, frame) is None


def test_cost_of_equity_is_clamped():
    assert cost_of_equity(0.018, 1.0, 0.055) == pytest.approx(0.073)
    assert cost_of_equity(0.018, 5.0, 0.055) == pytest.approx(0.15)  # 上限
    assert cost_of_equity(0.0, 0.5, 0.01) == pytest.approx(0.06)  # 下限
