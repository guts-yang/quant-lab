"""从公司历史和行业基准推导熊/基准/牛三情景的 DCF 假设。

AlphaSpread 的思路：历史回归外推 + 行业基准（base rates）+ 分析师预期三者收敛，
且基准情景相对历史要保守（NVDA 基准增速低于 5 年历史和华尔街一致预期）。
本模块把这套思路参数化，全部规则显式可查。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import pandas as pd

from core.provider import FINANCIAL_COMP_TYPES
from valuation.model import DcfInputs

TEMPLATES = ("fcfe", "net_income")

# 缺少行业基准时的兜底倍数。文档化参数，不从市价反推，避免循环论证。
FALLBACK_EXIT_PS = 2.0
FALLBACK_EXIT_PE = 12.0
GROWTH_FLOOR = -0.2
GROWTH_CAP = 0.5


@dataclass(frozen=True)
class ValuationConfig:
    forecast_years: int = 7
    risk_free: float = 0.018  # 10 年期国债收益率，可 CLI 覆盖
    equity_risk_premium: float = 0.055  # 中国股权风险溢价，Damodaran 口径
    terminal_growth: float = 0.025  # 预测期末收敛到的增速
    perpetuity_growth: float = 0.025  # Gordon 终值的永续增速
    conversion_floor: float = 0.5
    conversion_cap: float = 1.2
    growth_industry_cap: float = 1.5  # 起始增速相对行业中位增速的上限倍数
    bear_growth_scale: float = 0.6
    bull_growth_scale: float = 1.25
    bear_discount_spread: float = 0.015
    bull_discount_spread: float = -0.01
    exit_basis: str = "ps"  # 非金融业的默认退出倍数口径
    min_industry_peers: int = 5  # 行业分位数统计的最小样本数


@dataclass(frozen=True)
class CompanyFundamentals:
    ts_code: str
    industry: str | None
    comp_type: str | None
    price: float | None
    total_mv: float | None  # 元
    shares: float | None  # 股
    revenue_ttm: float | None
    net_income_ttm: float | None
    ocf_ttm: float | None
    fcf_ttm: float | None
    revenue_cagr_5y: float | None
    margin_ttm: float | None
    margin_mean_5y: float | None
    fcf_conversion: float | None  # 历年 FCF/归母净利的中位数


@dataclass(frozen=True)
class IndustryStats:
    industry: str
    peers: int
    growth_median: float | None
    growth_p25: float | None
    growth_p75: float | None
    margin_median: float | None
    margin_p25: float | None
    margin_p75: float | None
    ps_median: float | None
    ps_p25: float | None
    ps_p75: float | None
    pe_median: float | None
    pe_p25: float | None
    pe_p75: float | None


class EstimatesProvider(Protocol):
    """分析师一致预期的适配接口。免费数据源拿不到时返回 None，自动回退历史+行业基准。"""

    def get(self, ts_code: str) -> dict[str, float] | None:
        """返回 {"revenue_growth_next": …, "net_margin_next": …}，缺省返回 None。"""
        ...


@dataclass(frozen=True)
class FileEstimatesProvider:
    """从 JSON 文件读取手工填入的一致预期，格式 {code: {"revenue_growth_next": …}}。"""

    path: str | Path

    def get(self, ts_code: str) -> dict[str, float] | None:
        path = Path(self.path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        entry = payload.get(str(ts_code))
        if not isinstance(entry, dict):
            return None
        return {key: float(value) for key, value in entry.items() if isinstance(value, (int, float))}


def select_template(fundamentals: CompanyFundamentals) -> str:
    """金融业（银行/保险/证券）资本结构特殊，用 Net Income 模板；其余用 FCFE。"""
    if fundamentals.comp_type in FINANCIAL_COMP_TYPES:
        return "net_income"
    return "fcfe"


def cagr(first: float, last: float, years: float) -> float | None:
    if first is None or last is None or first <= 0 or years <= 0:
        return None
    return float((last / first) ** (1.0 / years) - 1.0)


def derive_base_inputs(
    fundamentals: CompanyFundamentals,
    industry: IndustryStats | None,
    discount_rate: float,
    config: ValuationConfig,
    estimates: dict[str, float] | None = None,
) -> DcfInputs:
    """基准情景。缺失输入按「公司历史 → 行业基准 → 保守常数」的顺序回退。"""
    template = select_template(fundamentals)

    growth = _base_growth(fundamentals, industry, config)
    estimate_growth = (estimates or {}).get("revenue_growth_next")
    if estimate_growth is not None:
        growth = 0.5 * growth + 0.5 * _clamp_growth(estimate_growth)

    margin_start = _first_valid(fundamentals.margin_ttm, fundamentals.margin_mean_5y, _industry(industry, "margin_median"), 0.1)
    margin_terminal = _terminal_margin(fundamentals, industry, margin_start)
    estimate_margin = (estimates or {}).get("net_margin_next")
    if estimate_margin is not None and estimate_margin > 0:
        margin_start = 0.5 * margin_start + 0.5 * float(estimate_margin)

    if template == "net_income":
        conversion = 1.0
        exit_basis = "pe"
    else:
        conversion = _clamp(fundamentals.fcf_conversion, config.conversion_floor, config.conversion_cap, default=0.9)
        exit_basis = config.exit_basis

    terminal_growth = min(config.terminal_growth, discount_rate - 0.01)
    return DcfInputs(
        revenue_base=_require(fundamentals.revenue_ttm, "revenue_ttm"),
        growth_start=growth,
        growth_terminal=terminal_growth,
        margin_start=margin_start,
        margin_terminal=margin_terminal,
        cash_conversion=conversion,
        forecast_years=config.forecast_years,
        discount_rate=discount_rate,
        exit_multiple=_exit_multiple(industry, exit_basis),
        exit_basis=exit_basis,
        shares=_require(fundamentals.shares, "shares"),
        terminal_method="multiple",
        perpetuity_growth=config.perpetuity_growth,
    )


def derive_scenarios(
    base: DcfInputs,
    industry: IndustryStats | None,
    config: ValuationConfig,
) -> dict[str, DcfInputs]:
    """熊市压增速、压终值利润率、加折现率、压退出倍数；牛市反向。"""
    bear_margin = _industry(industry, "margin_p25")
    bull_margin = _industry(industry, "margin_p75")
    bear_exit = _exit_multiple(industry, base.exit_basis, quantile="p25")
    bull_exit = _exit_multiple(industry, base.exit_basis, quantile="p75")
    bear = replace(
        base,
        growth_start=_clamp_growth(base.growth_start * config.bear_growth_scale),
        margin_terminal=bear_margin if bear_margin is not None else base.margin_terminal * 0.8,
        discount_rate=base.discount_rate + config.bear_discount_spread,
        exit_multiple=bear_exit,
    )
    bull = replace(
        base,
        growth_start=_clamp_growth(base.growth_start * config.bull_growth_scale),
        margin_terminal=bull_margin if bull_margin is not None else min(base.margin_terminal * 1.1, base.margin_start),
        discount_rate=max(0.01, base.discount_rate + config.bull_discount_spread),
        exit_multiple=bull_exit,
    )
    return {"bear": bear, "base": base, "bull": bull}


def _base_growth(fundamentals: CompanyFundamentals, industry: IndustryStats | None, config: ValuationConfig) -> float:
    """起始增速 = min(公司 5 年 CAGR, 行业中位增速 × 上限倍数)，缺失时退到行业或永续增速。"""
    industry_median = _industry(industry, "growth_median")
    history = fundamentals.revenue_cagr_5y
    if history is None and industry_median is None:
        return config.terminal_growth
    if history is None:
        return _clamp_growth(industry_median)
    if industry_median is None or industry_median <= 0:
        return _clamp_growth(history)
    return _clamp_growth(min(history, industry_median * config.growth_industry_cap))


def _terminal_margin(
    fundamentals: CompanyFundamentals,
    industry: IndustryStats | None,
    margin_start: float,
) -> float:
    """终值净利率不超当前水平：在公司 5 年均值与行业中位之间取高者，再与当前取低。

    不用行业分位封顶：高护城河公司的利润率可以持续高于行业（AlphaSpread 对 NVDA
    的处理也是如此），行业分位只用于熊/牛情景的锚。
    """
    anchor = _first_valid(fundamentals.margin_mean_5y, _industry(industry, "margin_median"), margin_start)
    if fundamentals.margin_mean_5y is not None and _industry(industry, "margin_median") is not None:
        anchor = max(fundamentals.margin_mean_5y, _industry(industry, "margin_median"))
    return max(0.0, min(margin_start, anchor))


def _exit_multiple(industry: IndustryStats | None, basis: str, quantile: str = "median") -> float:
    name = f"{'ps' if basis == 'ps' else 'pe'}_{quantile}"
    value = _industry(industry, name)
    if value is None or value <= 0:
        value = _industry(industry, f"{'ps' if basis == 'ps' else 'pe'}_median")
    if value is None or value <= 0:
        return FALLBACK_EXIT_PS if basis == "ps" else FALLBACK_EXIT_PE
    return float(value)


def _industry(industry: IndustryStats | None, field: str) -> float | None:
    if industry is None:
        return None
    value = getattr(industry, field, None)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _first_valid(*values: float | None) -> float:
    for value in values:
        if value is not None and not pd.isna(value):
            return float(value)
    raise ValueError("没有可用的数值")


def _clamp(value: float | None, floor: float, cap: float, *, default: float) -> float:
    if value is None or pd.isna(value):
        return default
    return min(cap, max(floor, float(value)))


def _clamp_growth(value: float) -> float:
    return min(GROWTH_CAP, max(GROWTH_FLOOR, float(value)))


def _require(value: float | None, name: str) -> float:
    if value is None or pd.isna(value):
        raise ValueError(f"缺少 {name}，无法估值")
    return float(value)
