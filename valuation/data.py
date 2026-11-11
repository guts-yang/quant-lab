"""估值取数层：公告日对齐、YTD→TTM 换算、行业基准。provider 只给原始帧，换算都在这里。

利润表和现金流量表是年内累计值（YTD）：
- 年报（12-31）的 YTD 就是全年值
- 其他季度的 TTM = 本季 YTD + 上年年报 − 去年同期 YTD
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.pit import visible_history
from core.provider import HS300, recent_period_ends
from factors.fundamental.common import CONSOLIDATED_REPORTS, shift_quarter_end
from valuation.assumptions import CompanyFundamentals, IndustryStats, cagr

# 5 年 CAGR + TTM 需要 6 年出头的季度报告期。
LOOKBACK_QUARTERS = 28
BETA_LOOKBACK_DAYS = int(365 * 2.6)
ANNUAL_MONTH_DAY = (12, 31)


@dataclass(frozen=True)
class ValuationData:
    fundamentals: CompanyFundamentals
    industry: IndustryStats | None
    stock_daily: pd.DataFrame
    index_daily: pd.DataFrame


def visible_frame(frame: pd.DataFrame | None, asof: pd.Timestamp) -> pd.DataFrame:
    """只留 asof 之前已公告的合并报表记录。"""
    return visible_history(frame, asof, allowed_report_types=CONSOLIDATED_REPORTS)


def annual_values(frame: pd.DataFrame, column: str) -> dict[str, dict[int, float]]:
    """每个代码的年度值序列（12-31 报告期）。返回 {code: {year: value}}。"""
    result: dict[str, dict[int, float]] = {}
    if frame is None or frame.empty or column not in frame.columns:
        return result
    for code, group in frame.groupby("ts_code", sort=False):
        series: dict[int, float] = {}
        for _, row in group.iterrows():
            end = row["end_date"]
            if pd.isna(end) or (end.month, end.day) != ANNUAL_MONTH_DAY:
                continue
            value = row[column]
            if pd.notna(value):
                series[int(end.year)] = float(value)
        if series:
            result[str(code)] = series
    return result


def ttm_at(values: dict[pd.Timestamp, float], end: pd.Timestamp) -> float | None:
    """YTD 序列在指定报告期的 TTM。缺任何一个组成部分则返回 None。"""
    end = pd.Timestamp(end).normalize()
    if pd.isna(end) or end not in values:
        return None
    current = values[end]
    if pd.isna(current):
        return None
    if (end.month, end.day) == ANNUAL_MONTH_DAY:
        return float(current)
    prev_fy = pd.Timestamp(end.year - 1, 12, 31)
    prev_same = shift_quarter_end(end, -4)
    if pd.isna(prev_same) or prev_fy not in values or prev_same not in values:
        return None
    fy, same = values[prev_fy], values[prev_same]
    if pd.isna(fy) or pd.isna(same):
        return None
    return float(current + fy - same)


def ttm_per_code(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """每个代码在最近已公告报告期的 TTM。返回 ts_code、end_date、ttm 三列。"""
    columns = ["ts_code", "end_date", "ttm"]
    if frame is None or frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for code, group in frame.groupby("ts_code", sort=False):
        values = _ytd_map(group, column)
        if not values:
            continue
        latest = max(values)
        rows.append({"ts_code": str(code), "end_date": latest, "ttm": ttm_at(values, latest)})
    return pd.DataFrame(rows, columns=columns)


def ttm_growth_per_code(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """最近 TTM 相对四个季度前 TTM 的同比。基期不为正时记缺失。"""
    columns = ["ts_code", "growth"]
    if frame is None or frame.empty or column not in frame.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for code, group in frame.groupby("ts_code", sort=False):
        values = _ytd_map(group, column)
        if not values:
            continue
        latest = max(values)
        current = ttm_at(values, latest)
        previous = ttm_at(values, shift_quarter_end(latest, -4))
        growth = None
        if current is not None and previous is not None and previous > 0:
            growth = current / previous - 1.0
        rows.append({"ts_code": str(code), "growth": growth})
    return pd.DataFrame(rows, columns=columns)


def company_fundamentals(
    ts_code: str,
    income_visible: pd.DataFrame,
    cashflow_visible: pd.DataFrame,
    instruments: pd.DataFrame,
    daily_basic: pd.DataFrame,
) -> CompanyFundamentals:
    """单票估值所需的全部历史输入，全部来自 asof 前已公告数据。"""
    code = str(ts_code)
    income = income_visible.loc[income_visible["ts_code"].astype(str).eq(code)]
    cashflow = cashflow_visible.loc[cashflow_visible["ts_code"].astype(str).eq(code)]

    revenue_annual = annual_values(income, "total_revenue").get(code, {})
    income_annual = annual_values(income, "n_income_attr").get(code, {})
    ocf_annual = annual_values(cashflow, "ocf").get(code, {})
    capex_annual = annual_values(cashflow, "capex").get(code, {})

    revenue_ttm = _scalar_ttm(income, "total_revenue")
    net_income_ttm = _scalar_ttm(income, "n_income_attr")
    ocf_ttm = _scalar_ttm(cashflow, "ocf")
    capex_ttm = _scalar_ttm(cashflow, "capex")
    fcf_ttm = None
    if ocf_ttm is not None and capex_ttm is not None:
        fcf_ttm = ocf_ttm - capex_ttm

    margin_ttm = _ratio(net_income_ttm, revenue_ttm)
    margin_mean_5y = _margin_mean(income_annual, revenue_annual)
    revenue_cagr = _revenue_cagr(revenue_annual)
    conversion = _fcf_conversion(ocf_annual, capex_annual, income_annual)

    industry = _instrument_industry(instruments, code)
    name = _instrument_name(instruments, code)
    comp_type = _latest_comp_type(income)
    price, total_mv = _market_snapshot(daily_basic, code)
    shares = total_mv / price if total_mv is not None and price else None
    return CompanyFundamentals(
        ts_code=code,
        industry=industry,
        comp_type=comp_type,
        price=price,
        total_mv=total_mv,
        shares=shares,
        revenue_ttm=revenue_ttm,
        net_income_ttm=net_income_ttm,
        ocf_ttm=ocf_ttm,
        fcf_ttm=fcf_ttm,
        revenue_cagr_5y=revenue_cagr,
        margin_ttm=margin_ttm,
        margin_mean_5y=margin_mean_5y,
        fcf_conversion=conversion,
        name=name,
    )


def industry_stats(
    income_visible: pd.DataFrame,
    instruments: pd.DataFrame,
    daily_basic: pd.DataFrame,
    industry: str,
    *,
    min_peers: int = 5,
) -> IndustryStats | None:
    """同行业（剔退市）的 TTM 增速、利润率、P/S、P/E 的中位数与四分位。"""
    peers = _listed_peers(instruments, industry)
    if peers.empty:
        return None
    revenue = ttm_per_code(income_visible, "total_revenue").rename(columns={"ttm": "revenue_ttm"})
    net_income = ttm_per_code(income_visible, "n_income_attr").rename(columns={"ttm": "net_income_ttm"})
    growth = ttm_growth_per_code(income_visible, "total_revenue")
    market = daily_basic[["ts_code", "total_mv"]].copy()
    market["ts_code"] = market["ts_code"].astype(str)

    frame = peers[["ts_code"]].merge(revenue[["ts_code", "revenue_ttm"]], on="ts_code", how="left")
    frame = frame.merge(net_income[["ts_code", "net_income_ttm"]], on="ts_code", how="left")
    frame = frame.merge(growth, on="ts_code", how="left").merge(market, on="ts_code", how="left")
    frame["margin"] = frame["net_income_ttm"] / frame["revenue_ttm"].where(frame["revenue_ttm"] > 0)
    frame["ps"] = frame["total_mv"] / frame["revenue_ttm"].where(frame["revenue_ttm"] > 0)
    frame["pe"] = frame["total_mv"] / frame["net_income_ttm"].where(frame["net_income_ttm"] > 0)

    def quantiles(column: str) -> tuple[float | None, float | None, float | None]:
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        values = values[values > 0] if column in {"ps", "pe"} else values
        if len(values) < min_peers:
            return None, None, None
        return float(values.quantile(0.25)), float(values.quantile(0.5)), float(values.quantile(0.75))

    growth_p25, growth_median, growth_p75 = quantiles("growth")
    margin_p25, margin_median, margin_p75 = quantiles("margin")
    ps_p25, ps_median, ps_p75 = quantiles("ps")
    pe_p25, pe_median, pe_p75 = quantiles("pe")
    return IndustryStats(
        industry=str(industry),
        peers=int(len(frame)),
        growth_median=growth_median,
        growth_p25=growth_p25,
        growth_p75=growth_p75,
        margin_median=margin_median,
        margin_p25=margin_p25,
        margin_p75=margin_p75,
        ps_median=ps_median,
        ps_p25=ps_p25,
        ps_p75=ps_p75,
        pe_median=pe_median,
        pe_p25=pe_p25,
        pe_p75=pe_p75,
    )


def load_valuation_inputs(
    provider,
    ts_code: str,
    asof: pd.Timestamp,
    *,
    lookback_quarters: int = LOOKBACK_QUARTERS,
    index_code: str = HS300,
) -> ValuationData:
    """编排取数：报表（按报告期全市场缓存）+ 证券信息 + 市值 + 个股与指数日线。"""
    asof = pd.Timestamp(asof).normalize()
    code = str(ts_code)
    periods = recent_period_ends(asof, n=lookback_quarters)
    income_visible = visible_frame(provider.get_income_statements(periods), asof)
    cashflow_visible = visible_frame(provider.get_cash_flows(periods), asof)
    instruments = provider.get_instruments(asof)
    daily_basic = provider.get_daily_basic(asof)

    fundamentals = company_fundamentals(code, income_visible, cashflow_visible, instruments, daily_basic)
    industry = None
    if fundamentals.industry:
        industry = industry_stats(income_visible, instruments, daily_basic, fundamentals.industry)

    beta_start = asof - pd.Timedelta(days=BETA_LOOKBACK_DAYS)
    stock_daily = provider.get_stock_ohlc(code, beta_start, asof)
    index_daily = provider.get_index_daily(index_code, beta_start, asof)
    return ValuationData(fundamentals=fundamentals, industry=industry, stock_daily=stock_daily, index_daily=index_daily)


def _ytd_map(group: pd.DataFrame, column: str) -> dict[pd.Timestamp, float]:
    values: dict[pd.Timestamp, float] = {}
    for _, row in group.iterrows():
        end = row["end_date"]
        if pd.isna(end):
            continue
        value = row[column]
        values[pd.Timestamp(end).normalize()] = float(value) if pd.notna(value) else None
    return {key: value for key, value in values.items() if value is not None}


def _scalar_ttm(frame: pd.DataFrame, column: str) -> float | None:
    table = ttm_per_code(frame, column)
    if table.empty:
        return None
    value = table["ttm"].iloc[0]
    return float(value) if pd.notna(value) else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _margin_mean(income_annual: dict[int, float], revenue_annual: dict[int, float]) -> float | None:
    """最近 5 个年报年度的合计净利率（Σ利润/Σ收入），比逐年平均更抗异常年份。"""
    years = sorted(set(income_annual) & set(revenue_annual))[-5:]
    income = sum(income_annual[year] for year in years)
    revenue = sum(revenue_annual[year] for year in years)
    if not years or revenue <= 0:
        return None
    return income / revenue


def _revenue_cagr(revenue_annual: dict[int, float]) -> float | None:
    years = sorted(revenue_annual)[-6:]
    if len(years) < 2:
        return None
    first, last = years[0], years[-1]
    return cagr(revenue_annual[first], revenue_annual[last], last - first)


def _fcf_conversion(
    ocf_annual: dict[int, float],
    capex_annual: dict[int, float],
    income_annual: dict[int, float],
) -> float | None:
    """最近 5 个年报年度 FCF/归母净利的中位数，只统计利润为正的年份。"""
    ratios: list[float] = []
    for year in sorted(set(ocf_annual) & set(capex_annual) & set(income_annual))[-5:]:
        profit = income_annual[year]
        if profit > 0:
            ratios.append((ocf_annual[year] - capex_annual[year]) / profit)
    if not ratios:
        return None
    return float(pd.Series(ratios).median())


def _instrument_name(instruments: pd.DataFrame, code: str) -> str | None:
    if instruments is None or instruments.empty or "name" not in instruments.columns:
        return None
    row = instruments.loc[instruments["ts_code"].astype(str).eq(code)]
    if row.empty:
        return None
    name = row.iloc[0]["name"]
    if name is None or pd.isna(name):
        return None
    text = str(name).strip()
    return text or None


def _instrument_industry(instruments: pd.DataFrame, code: str) -> str | None:
    if instruments is None or instruments.empty or "industry" not in instruments.columns:
        return None
    row = instruments.loc[instruments["ts_code"].astype(str).eq(code)]
    if row.empty:
        return None
    industry = row.iloc[0]["industry"]
    return str(industry) if industry is not None and pd.notna(industry) else None


def _latest_comp_type(income: pd.DataFrame) -> str | None:
    """公司类型（1 工商业 2 银行 3 保险 4 证券）取最近一期利润表记录。"""
    if income is None or income.empty or "comp_type" not in income.columns:
        return None
    latest = income.sort_values("end_date", kind="mergesort").iloc[-1]
    comp_type = latest.get("comp_type")
    return str(comp_type) if comp_type is not None and pd.notna(comp_type) else None


def _market_snapshot(daily_basic: pd.DataFrame, code: str) -> tuple[float | None, float | None]:
    if daily_basic is None or daily_basic.empty:
        return None, None
    row = daily_basic.loc[daily_basic["ts_code"].astype(str).eq(code)]
    if row.empty:
        return None, None
    record = row.iloc[0]
    price = pd.to_numeric(pd.Series([record.get("close")]), errors="coerce").iloc[0]
    total_mv = pd.to_numeric(pd.Series([record.get("total_mv")]), errors="coerce").iloc[0]
    return (
        float(price) if pd.notna(price) else None,
        float(total_mv) if pd.notna(total_mv) else None,
    )


def _listed_peers(instruments: pd.DataFrame, industry: str) -> pd.DataFrame:
    if instruments is None or instruments.empty or "industry" not in instruments.columns:
        return pd.DataFrame(columns=["ts_code"])
    frame = instruments.copy()
    listed = frame["list_status"].astype("string").eq("L")
    if "delist_date" in frame.columns:
        listed &= frame["delist_date"].isna()
    same_industry = frame["industry"].astype("string").eq(str(industry))
    return frame.loc[listed & same_industry].reset_index(drop=True)
