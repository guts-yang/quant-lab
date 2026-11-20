"""DCF 引擎、假设推导和 PIT 对齐的合成数据测试。不访问网络。"""

import json
import math

import pandas as pd
import pytest

from valuation.assumptions import (
    CompanyFundamentals,
    IndustryStats,
    ValuationConfig,
    derive_base_inputs,
    derive_scenarios,
    equity_reinvestment_rate,
    fundamental_growth,
    select_template,
)
from valuation.fallbacks import STATUS_APPROVED, STATUS_PENDING
from valuation.data import annual_values, company_fundamentals, ttm_at, ttm_growth_per_code, ttm_per_code, visible_frame
from valuation.discount import cost_of_equity, estimate_beta
from valuation.history import align_history, announcement_dates, build_history, valuation_knots
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


def test_margin_path_starts_at_start_and_lands_on_terminal():
    path = margin_path(0.5, 0.4, 5)
    assert path[0] == pytest.approx(0.5)  # 第 1 年等于起点
    assert path[-1] == pytest.approx(0.4)  # 第 N 年等于终值
    assert len(path) == 5


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


def test_dcf_value_matches_hand_computation_gordon():
    # 收入 100，增速 10% 不变，净利率 10%，转换率 1，两年，折现率 10%，10 股。
    # 默认终值用 Gordon 永续：稳定期再投资率 = g/ROE = 0.03/0.10 = 30%，稳定现金转换 = 70%。
    # FCFE1 = 110×0.1×1 = 11，FCFE2 = 121×0.1×1 = 12.1
    # TV = 121×1.03×0.10×0.70 / (0.10-0.03) = 121×1.03×0.07/0.07 = 121×1.03 = 124.63
    inputs = _inputs(perpetuity_growth=0.03, stable_roe=0.10)
    result = dcf_value(inputs)
    expected_pv_explicit = 11 / 1.1 + 12.1 / 1.21
    expected_tv = 121.0 * 1.03 * 0.10 * 0.70 / 0.07
    expected_equity = expected_pv_explicit + expected_tv / 1.21
    assert result.pv_explicit == pytest.approx(expected_pv_explicit)
    assert result.terminal_value == pytest.approx(expected_tv)
    assert result.equity_value == pytest.approx(expected_equity)
    assert result.per_share == pytest.approx(expected_equity / 10)


def test_pe_exit_basis_uses_net_income():
    result = dcf_value(_inputs(terminal_method="multiple", exit_basis="pe", exit_multiple=8.0))
    assert result.terminal_value == pytest.approx(12.1 * 8.0)


def test_gordon_terminal_requires_discount_above_growth():
    result = dcf_value(_inputs(terminal_method="gordon", perpetuity_growth=0.03, stable_roe=0.10))
    # 稳定期现金转换 = 1 - 0.03/0.10 = 0.70
    assert result.terminal_value == pytest.approx(121.0 * 1.03 * 0.10 * 0.70 / 0.07)
    with pytest.raises(ValueError):
        dcf_value(_inputs(terminal_method="gordon", perpetuity_growth=0.10))


def test_gordon_zero_terminal_when_margin_nonpositive():
    # 终值净利率非正时，永续生意不可持续，终值按 0 计。
    result = dcf_value(_inputs(margin_terminal=-0.01, terminal_method="gordon"))
    assert result.terminal_value == 0.0
    assert result.terminal_warning is not None


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


def _complete(**overrides) -> CompanyFundamentals:
    """财报模型所需的公司数都齐，且转换率、ROE 为正。"""
    fields = {
        "equity_reinvestment_rate": 0.50,
        "roe_for_growth": 0.20,
        "roe_median": 0.10,
        "revenue_growth_low": 0.02,
        "revenue_growth_high": 0.30,
        "revenue_growth_samples": 4,
        "margin_low": 0.20,
        "margin_high": 0.55,
        "margin_samples": 4,
    }
    fields.update(overrides)
    return _fundamentals(**fields)


def test_industry_median_does_not_enter_growth_or_margin():
    config = ValuationConfig()
    inputs = derive_base_inputs(_complete(equity_reinvestment_rate=0.80, roe_for_growth=0.25), _industry(), 0.10, config)
    # 0.80 × 0.25 = 20%，高于行业中位 10% 的 1.5 倍，也不再夹到 50%。
    assert inputs.growth_start == pytest.approx(0.20)
    assert inputs.growth_terminal == pytest.approx(config.terminal_growth)
    # 终值净利率只在本公司 TTM 50% 和 5 年合计 45% 之间取较低者。
    assert inputs.margin_start == pytest.approx(0.50)
    assert inputs.margin_terminal == pytest.approx(0.45)
    assert inputs.cash_conversion == pytest.approx(0.88)
    assert inputs.stable_roe == pytest.approx(0.10)
    assert inputs.stable_cash_conversion == pytest.approx(1.0 - config.perpetuity_growth / 0.10)
    assert inputs.complete
    assert inputs.exit_basis == "ps"
    assert inputs.exit_multiple == pytest.approx(4.0)


def test_estimates_do_not_blend_into_company_growth():
    inputs = derive_base_inputs(_complete(), _industry(), 0.10, ValuationConfig(), {"revenue_growth_next": 0.21})
    assert inputs.growth_start == pytest.approx(0.50 * 0.20)


def test_financial_template_uses_net_income_and_pe_exit():
    fundamentals = _complete(comp_type="2", fcf_conversion=-0.4)
    assert select_template(fundamentals) == "net_income"
    inputs = derive_base_inputs(fundamentals, _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion == 1.0
    assert inputs.exit_basis == "pe"
    assert inputs.exit_multiple == pytest.approx(15.0)
    assert inputs.pending == ()


def test_scenarios_use_company_history_not_global_multiples():
    fundamentals = _complete()
    config = ValuationConfig()
    base = derive_base_inputs(fundamentals, _industry(), 0.10, config)
    scenarios = derive_scenarios(base, fundamentals, config, _industry())
    assert scenarios["base"].growth_start == pytest.approx(0.10)
    assert scenarios["bear"].growth_start == pytest.approx(0.02)
    assert scenarios["bull"].growth_start == pytest.approx(0.30)
    assert scenarios["bear"].growth_start != pytest.approx(0.10 * 0.6)
    assert scenarios["bear"].margin_terminal == pytest.approx(0.20)
    assert scenarios["bull"].margin_terminal == pytest.approx(0.55)
    assert scenarios["bear"].discount_rate == pytest.approx(0.115)
    assert scenarios["bull"].discount_rate == pytest.approx(0.09)
    values = {name: dcf_value(inputs.to_dcf()).per_share for name, inputs in scenarios.items()}
    assert values["bear"] < values["base"] < values["bull"]


def test_terminal_margin_does_not_exceed_current_company_margin():
    inputs = derive_base_inputs(_complete(margin_ttm=0.04, margin_mean_5y=0.08), _industry(), 0.10, ValuationConfig())
    assert inputs.margin_start == pytest.approx(0.04)
    assert inputs.margin_terminal == pytest.approx(0.04)


def test_missing_industry_keeps_exit_multiple_out_of_the_growth_model():
    inputs = derive_base_inputs(_complete(), None, 0.10, ValuationConfig())
    assert inputs.exit_multiple == pytest.approx(2.0)
    assert inputs.growth_start == pytest.approx(0.10)


def test_conversion_above_one_is_kept():
    inputs = derive_base_inputs(_complete(fcf_conversion=1.9), _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion == pytest.approx(1.9)
    assert inputs.complete


def test_nonpositive_stable_roe_does_not_silently_convert_at_one():
    with pytest.raises(ValueError, match="无法计算"):
        dcf_value(_inputs(stable_roe=-0.01, stable_cash_conversion=None))


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


def test_valuation_knots_keep_the_prior_announcement_and_the_asof():
    dates = pd.to_datetime(["2020-04-30", "2021-08-28", "2024-04-30", "2026-11-16"])
    knots = valuation_knots(dates, pd.Timestamp("2021-11-16"), pd.Timestamp("2026-11-16"))
    assert knots == [
        pd.Timestamp("2021-08-28"),
        pd.Timestamp("2024-04-30"),
        pd.Timestamp("2026-11-16"),
    ]


def test_align_history_leaves_days_before_the_first_value_blank():
    prices = pd.DataFrame({"trade_date": ["2024-01-02", "2024-04-30", "2024-05-06"], "close": [10.0, 11.0, 12.0]})
    knots = pd.DataFrame({"trade_date": ["2024-04-30"], "intrinsic": [8.0]})
    out = align_history(prices, knots)
    assert pd.isna(out.loc[0, "intrinsic"])
    assert out.loc[1, "intrinsic"] == 8.0
    assert out.loc[2, "intrinsic"] == 8.0


def test_equity_reinvestment_rate_uses_net_capex_nwc_and_net_debt():
    rate = equity_reinvestment_rate(net_income=100.0, net_capex=30.0, delta_nwc=20.0, net_debt_issued=10.0)
    assert rate == pytest.approx(0.40)
    assert fundamental_growth(rate, 0.25) == pytest.approx(0.10)
    assert equity_reinvestment_rate(0.0, 30.0, 20.0, 10.0) is None
    assert equity_reinvestment_rate(100.0, None, 20.0, 10.0) is None


def test_missing_conversion_does_not_produce_official_value():
    inputs = derive_base_inputs(_complete(fcf_conversion=None), _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion is None
    assert not inputs.complete
    assert inputs.to_dcf() is None
    item = next(item for item in inputs.pending if item.field == "cash_conversion")
    assert item.proposed is None
    assert item.computed is None
    assert item.status == STATUS_PENDING


def test_negative_conversion_stays_pending_instead_of_being_clamped():
    inputs = derive_base_inputs(_complete(fcf_conversion=-0.477), _industry(), 0.10, ValuationConfig())
    assert inputs.cash_conversion is None
    assert not inputs.complete
    item = next(item for item in inputs.pending if item.field == "cash_conversion")
    assert item.computed == pytest.approx(-0.477)
    assert item.proposed is None
    assert "1.0" in item.reason
    assert "50%" in item.reason


def test_missing_roe_does_not_produce_official_value_or_invent_equity():
    inputs = derive_base_inputs(
        _complete(equity_reinvestment_rate=None, roe_for_growth=None, roe_median=None, roe_reason="没有读到资产负债表，归属母公司股东权益无法计算。"),
        _industry(),
        0.10,
        ValuationConfig(),
    )
    assert inputs.growth_start is None
    assert inputs.stable_roe is None
    assert inputs.stable_cash_conversion is None
    assert inputs.stable_roe != pytest.approx(0.10)
    assert not inputs.complete
    growth = next(item for item in inputs.pending if item.field == "growth_start")
    assert growth.proposed == pytest.approx(0.20)
    stable = next(item for item in inputs.pending if item.field == "stable_cash_conversion")
    assert stable.proposed is None
    assert "股东权益" in stable.reason


def test_fallback_is_used_only_after_approval(tmp_path):
    path = tmp_path / "fallback_approvals.json"
    fundamentals = _complete(fcf_conversion=-0.477)
    blocked = derive_base_inputs(fundamentals, _industry(), 0.10, ValuationConfig(), approvals_path=path)
    assert blocked.cash_conversion is None
    assert not blocked.complete

    path.write_text(
        json.dumps({"approvals": [{"ts_code": "600519.SH", "field": "cash_conversion", "value": -0.477}]}),
        encoding="utf-8",
    )
    released = derive_base_inputs(fundamentals, _industry(), 0.10, ValuationConfig(), approvals_path=path)
    assert released.cash_conversion == pytest.approx(-0.477)
    assert released.complete
    item = next(item for item in released.fallbacks if item.field == "cash_conversion")
    assert item.status == STATUS_APPROVED
    assert item.approved_value == pytest.approx(-0.477)


def test_complete_company_matches_hand_computed_gordon():
    config = ValuationConfig(forecast_years=2, terminal_growth=0.03, perpetuity_growth=0.03)
    fundamentals = _complete(
        revenue_ttm=100.0,
        shares=10.0,
        margin_ttm=0.10,
        margin_mean_5y=0.10,
        fcf_conversion=1.0,
        equity_reinvestment_rate=0.50,
        roe_for_growth=0.20,
        roe_median=0.10,
    )
    inputs = derive_base_inputs(fundamentals, None, 0.10, config)
    assert inputs.complete
    assert inputs.growth_start == pytest.approx(0.10)
    assert inputs.growth_terminal == pytest.approx(0.03)
    assert inputs.cash_conversion == pytest.approx(1.0)
    assert inputs.stable_cash_conversion == pytest.approx(0.70)
    result = dcf_value(inputs.to_dcf())
    revenue_1 = 100.0 * 1.10
    revenue_2 = revenue_1 * 1.03
    fcfe_1 = revenue_1 * 0.10
    fcfe_2 = revenue_2 * 0.10
    terminal = revenue_2 * 1.03 * 0.10 * 0.70 / (0.10 - 0.03)
    equity = fcfe_1 / 1.10 + fcfe_2 / 1.21 + terminal / 1.21
    assert result.per_share == pytest.approx(equity / 10.0)


def test_short_history_marks_bear_and_bull_uncalculable():
    fundamentals = _complete(revenue_growth_samples=2, revenue_growth_low=None, revenue_growth_high=None)
    base = derive_base_inputs(fundamentals, _industry(), 0.10, ValuationConfig())
    scenarios = derive_scenarios(base, fundamentals, ValuationConfig(), _industry())
    assert scenarios["base"].complete
    assert scenarios["bear"].growth_start is None
    assert not scenarios["bear"].complete
    assert any(item.field == "bear_growth_start" and item.status == STATUS_PENDING for item in scenarios["bear"].pending)


def test_balance_sheet_roe_is_used_and_missing_reinvestment_is_not_invented():
    income = _annual_statement(
        "600519.SH",
        [
            ("2022-12-31", "2023-04-01", 100.0, 10.0),
            ("2023-12-31", "2024-04-01", 110.0, 12.0),
            ("2024-12-31", "2025-04-01", 121.0, 13.0),
        ],
        ["total_revenue", "n_income_attr"],
    )
    cashflow = _annual_statement(
        "600519.SH",
        [
            ("2022-12-31", "2023-04-01", 8.0, 2.0),
            ("2023-12-31", "2024-04-01", 9.0, 2.0),
            ("2024-12-31", "2025-04-01", 10.0, 2.0),
        ],
        ["ocf", "capex"],
    )
    balance = _annual_statement(
        "600519.SH",
        [
            ("2022-12-31", "2023-04-01", 100.0),
            ("2023-12-31", "2024-04-01", 120.0),
            ("2024-12-31", "2025-04-01", 140.0),
        ],
        ["equity"],
    )
    instruments = pd.DataFrame(
        {"ts_code": ["600519.SH"], "list_date": [pd.Timestamp("2001-08-27")], "delist_date": [pd.NaT], "list_status": ["L"], "industry": ["白酒"], "name": ["贵州茅台"]}
    )
    daily_basic = pd.DataFrame({"ts_code": ["600519.SH"], "trade_date": [pd.Timestamp("2025-04-30")], "close": [20.0], "total_mv": [2000.0]})
    fundamentals = company_fundamentals("600519.SH", income, cashflow, instruments, daily_basic, balance)
    # 2024 ROE = 13 / ((120+140)/2) = 0.1；2023 ROE = 12 / ((100+120)/2) = 0.10909
    assert fundamentals.roe_for_growth == pytest.approx(13.0 / 130.0)
    assert fundamentals.roe_median == pytest.approx(pd.Series([12.0 / 110.0, 13.0 / 130.0]).median())
    assert fundamentals.equity_reinvestment_rate is None
    assert "净资本开支" in fundamentals.reinvestment_reason
    assert "2.5%" in fundamentals.reinvestment_reason
    inputs = derive_base_inputs(fundamentals, None, 0.10, ValuationConfig())
    assert inputs.growth_start is None
    assert not inputs.complete
    assert inputs.stable_roe == pytest.approx(fundamentals.roe_median)
    assert inputs.stable_cash_conversion == pytest.approx(1.0 - 0.025 / fundamentals.roe_median)


def _annual_statement(code: str, rows: list[tuple], value_columns: list[str]) -> pd.DataFrame:
    records = []
    for row in rows:
        end, ann, *values = row
        record = {
            "ts_code": code,
            "end_date": pd.Timestamp(end),
            "ann_date": pd.Timestamp(ann),
            "report_type": "1",
            "comp_type": "1",
        }
        for name, value in zip(value_columns, values, strict=True):
            record[name] = value
        records.append(record)
    return pd.DataFrame(records)


def test_reinvestment_columns_produce_fundamental_growth():
    income = _annual_statement(
        "600519.SH",
        [("2023-12-31", "2024-04-01", 100.0, 10.0), ("2024-12-31", "2025-04-01", 110.0, 20.0)],
        ["total_revenue", "n_income_attr"],
    )
    cashflow = _annual_statement(
        "600519.SH",
        [("2023-12-31", "2024-04-01", 1.0, 1.0, 1.0), ("2024-12-31", "2025-04-01", 4.0, 3.0, 1.0)],
        ["net_capex", "delta_nwc", "net_debt_issued"],
    )
    balance = _annual_statement(
        "600519.SH",
        [("2023-12-31", "2024-04-01", 80.0), ("2024-12-31", "2025-04-01", 120.0)],
        ["equity"],
    )
    instruments = pd.DataFrame(
        {"ts_code": ["600519.SH"], "list_date": [pd.Timestamp("2001-08-27")], "delist_date": [pd.NaT], "list_status": ["L"], "industry": ["白酒"], "name": ["贵州茅台"]}
    )
    daily_basic = pd.DataFrame({"ts_code": ["600519.SH"], "trade_date": [pd.Timestamp("2025-04-30")], "close": [10.0], "total_mv": [1000.0]})
    fundamentals = company_fundamentals("600519.SH", income, cashflow, instruments, daily_basic, balance)
    # 再投资率 = (4 + 3 − 1) / 20 = 0.30；ROE = 20 / ((80+120)/2) = 0.20；g = 0.06
    assert fundamentals.equity_reinvestment_rate == pytest.approx(0.30)
    assert fundamentals.roe_for_growth == pytest.approx(0.20)
    assert fundamental_growth(fundamentals.equity_reinvestment_rate, fundamentals.roe_for_growth) == pytest.approx(0.06)


def test_no_balance_sheet_does_not_invent_equity():
    income = visible_frame(_income_frame(), pd.Timestamp("2024-09-01"))
    cashflow = pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "ocf", "capex", "report_type", "comp_type"])
    instruments = pd.DataFrame(
        {"ts_code": ["600519.SH"], "list_date": [pd.Timestamp("2001-08-27")], "delist_date": [pd.NaT], "list_status": ["L"], "industry": ["白酒"], "name": ["贵州茅台"]}
    )
    daily_basic = pd.DataFrame({"ts_code": ["600519.SH"], "trade_date": [pd.Timestamp("2024-08-30")], "close": [20.0], "total_mv": [2.2e10]})
    fundamentals = company_fundamentals("600519.SH", income, cashflow, instruments, daily_basic, None)
    assert fundamentals.roe_median is None
    assert fundamentals.roe_for_growth is None
    assert "股东权益" in fundamentals.roe_reason
    assert "编造" in fundamentals.roe_reason


def test_incomplete_report_writes_null_per_share(tmp_path):
    from valuation.scenario import render_report, run_valuation, write_outputs

    class _Provider:
        def get_income_statements(self, periods):
            return _income_frame()

        def get_cash_flows(self, periods):
            return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "ocf", "capex", "report_type", "comp_type"])

        def get_balance_sheets(self, periods):
            return pd.DataFrame(columns=["ts_code", "ann_date", "end_date", "equity", "report_type", "comp_type"])

        def get_instruments(self, asof):
            return pd.DataFrame(
                {"ts_code": ["600519.SH"], "list_date": [pd.Timestamp("2001-08-27")], "delist_date": [pd.NaT], "list_status": ["L"], "industry": ["白酒"], "name": ["贵州茅台"]}
            )

        def get_daily_basic(self, asof):
            return pd.DataFrame({"ts_code": ["600519.SH"], "trade_date": [pd.Timestamp("2024-08-30")], "close": [20.0], "total_mv": [2.2e10]})

        def get_stock_ohlc(self, code, start, end):
            return _daily([10.0 + 0.01 * i for i in range(80)])

        def get_index_daily(self, code, start, end):
            return _daily([10.0 + 0.01 * i for i in range(80)])

    report = run_valuation(_Provider(), "600519.SH", "2024-09-01", ValuationConfig(), approvals_path=tmp_path / "missing.json")
    assert report.scenarios["base"].per_share is None
    assert report.pending
    text = render_report(report)
    assert "无法计算" in text
    assert "fallback_approvals.json" in text or "missing.json" in text
    assert "4.86" not in text
    write_outputs(report, tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "assumptions.json").read_text(encoding="utf-8"))
    assert payload["scenarios"]["base"]["per_share"] is None
    assert payload["status"] == "incomplete"
    assert payload["fallbacks"]
    assert pd.read_parquet(tmp_path / "out" / "forecast.parquet").empty


def test_history_skips_a_failed_announcement_and_ignores_unconsolidated_reports():
    income = pd.DataFrame(
        {
            "ts_code": ["600900.SH"] * 4,
            "ann_date": ["2021-04-30", "2023-08-30", "2024-04-30", "2025-04-30"],
            "end_date": ["2020-12-31", "2023-06-30", "2023-12-31", "2024-12-31"],
            "report_type": ["1", "2", "1", "1"],
        }
    )
    prices = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2024-04-29", "2024-05-06", "2025-04-30", "2026-11-16"]),
            "close": [10.0, 12.0, 13.0, 30.49],
        }
    )

    class _Provider:
        def get_income_statements(self, periods):
            return income

        def get_stock_ohlc(self, code, start, end):
            dates = pd.to_datetime(prices["trade_date"])
            return prices.loc[dates.between(start, end)].reset_index(drop=True)

    seen: list[pd.Timestamp] = []

    def valuer(day):
        seen.append(day)
        if day == pd.Timestamp("2024-04-30"):
            raise ValueError("缺少 shares，无法估值")
        return {
            pd.Timestamp("2021-04-30"): 8.0,
            pd.Timestamp("2025-04-30"): 18.0,
            pd.Timestamp("2026-11-16"): 18.19,
        }[day]

    history = build_history(_Provider(), "600900.SH", "2026-11-16", valuer=valuer)
    assert pd.Timestamp("2023-08-30") not in seen
    assert list(history["intrinsic"]) == [8.0, 8.0, 18.0, 18.19]
    assert announcement_dates(income, "600900.SH", "2026-11-16").tolist() == [
        pd.Timestamp("2021-04-30"),
        pd.Timestamp("2024-04-30"),
        pd.Timestamp("2025-04-30"),
    ]
