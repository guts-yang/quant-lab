"""从该公司财报推导 DCF 假设。宏观预设只留在 ValuationConfig。

公司自身的增速、净利率、现金转换率和稳定期再投资都来自这家公司的报表。
算不出来就记一条待审兜底，审核通过之前不进入每股价值。
行业中位数、10% 净利率、0.9 转换率、固定 10% ROE、−20%–50% 增速夹取、
熊/牛全局倍数，都不再进入主估值。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from core.provider import FINANCIAL_COMP_TYPES
from valuation.fallbacks import (
    STATUS_PENDING,
    FallbackItem,
    load_approvals,
    resolve_input,
)
from valuation.model import DcfInputs

TEMPLATES = ("fcfe", "net_income")

# 退出倍数只留给交叉校验，不进入 Gordon 主估值。
FALLBACK_EXIT_PS = 2.0
FALLBACK_EXIT_PE = 12.0
SCENARIO_MIN_SAMPLES = 3


@dataclass(frozen=True)
class ValuationConfig:
    forecast_years: int = 7
    risk_free: float = 0.018  # 10 年期国债收益率，可 CLI 覆盖
    equity_risk_premium: float = 0.055  # 中国股权风险溢价，Damodaran 口径
    terminal_growth: float = 0.025  # 第 N 年收入增速的收敛目标
    perpetuity_growth: float = 0.025  # Gordon 终值的永续增速
    bear_discount_spread: float = 0.015  # 情景设计，不是财报估计
    bull_discount_spread: float = -0.01
    exit_basis: str = "ps"  # 交叉校验用的退出倍数口径
    bear_perpetuity_spread: float = -0.01  # 情景设计，不是财报估计
    bull_perpetuity_spread: float = 0.005
    min_industry_peers: int = 5


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
    fcf_conversion: float | None  # 近 5 个盈利年度 (OCF−Capex)/归母净利的中位数，保留负值和大于 1 的值
    name: str | None = None
    equity_reinvestment_rate: float | None = None
    reinvestment_reason: str | None = None
    roe_for_growth: float | None = None
    roe_median: float | None = None
    roe_reason: str | None = None
    revenue_growth_low: float | None = None
    revenue_growth_high: float | None = None
    revenue_growth_samples: int = 0
    margin_low: float | None = None
    margin_high: float | None = None
    margin_samples: int = 0


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


class EstimatesProvider:
    """分析师一致预期不再进入主估值。接口保留，避免旧调用中断。"""

    def get(self, ts_code: str) -> dict[str, float] | None:
        return None


@dataclass(frozen=True)
class FileEstimatesProvider(EstimatesProvider):
    """从 JSON 文件读取手工填入的一致预期。主估值不使用这些数。"""

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
        return {key: float(value) for key, value in entry.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}


@dataclass(frozen=True)
class DerivedInputs:
    """一个情景的输入。关键字段为 None，或仍有待审兜底时，不能当成正式估值。"""

    revenue_base: float | None
    growth_start: float | None
    growth_terminal: float
    margin_start: float | None
    margin_terminal: float | None
    cash_conversion: float | None
    forecast_years: int
    discount_rate: float
    exit_multiple: float
    exit_basis: str
    shares: float | None
    terminal_method: str
    perpetuity_growth: float
    stable_roe: float | None
    stable_cash_conversion: float | None
    fallbacks: tuple[FallbackItem, ...] = ()
    template: str = "fcfe"

    @property
    def pending(self) -> tuple[FallbackItem, ...]:
        return tuple(item for item in self.fallbacks if item.status == STATUS_PENDING)

    @property
    def complete(self) -> bool:
        if self.pending:
            return False
        required = (
            self.revenue_base,
            self.growth_start,
            self.margin_start,
            self.margin_terminal,
            self.cash_conversion,
            self.shares,
            self.stable_cash_conversion,
        )
        if any(value is None for value in required):
            return False
        if self.revenue_base <= 0 or self.shares <= 0 or self.discount_rate <= 0:
            return False
        if self.discount_rate <= self.perpetuity_growth:
            return False
        return True

    def to_dcf(self) -> DcfInputs | None:
        if not self.complete:
            return None
        return DcfInputs(
            revenue_base=float(self.revenue_base),
            growth_start=float(self.growth_start),
            growth_terminal=float(self.growth_terminal),
            margin_start=float(self.margin_start),
            margin_terminal=float(self.margin_terminal),
            cash_conversion=float(self.cash_conversion),
            forecast_years=int(self.forecast_years),
            discount_rate=float(self.discount_rate),
            exit_multiple=float(self.exit_multiple),
            exit_basis=self.exit_basis,
            shares=float(self.shares),
            terminal_method=self.terminal_method,
            perpetuity_growth=float(self.perpetuity_growth),
            stable_roe=float(self.stable_roe) if self.stable_roe is not None else 0.0,
            stable_cash_conversion=float(self.stable_cash_conversion),
        )


def select_template(fundamentals: CompanyFundamentals) -> str:
    """金融业（银行/保险/证券）资本结构特殊，用净利润模板；其余用 FCFE。"""
    if fundamentals.comp_type in FINANCIAL_COMP_TYPES:
        return "net_income"
    return "fcfe"


def cagr(first: float, last: float, years: float) -> float | None:
    if first is None or last is None or first <= 0 or years <= 0:
        return None
    return float((last / first) ** (1.0 / years) - 1.0)


def equity_reinvestment_rate(
    net_income: float | None,
    net_capex: float | None,
    delta_nwc: float | None,
    net_debt_issued: float | None,
) -> float | None:
    """股权再投资率 =（净资本开支 + 运营资本变动 − 净债务发行）/ 归母净利。

    归母净利非正，或三项里缺任何一项，返回 None。不拿别的口径顶上。
    """
    if net_income is None or net_capex is None or delta_nwc is None or net_debt_issued is None:
        return None
    if net_income <= 0:
        return None
    return (float(net_capex) + float(delta_nwc) - float(net_debt_issued)) / float(net_income)


def fundamental_growth(reinvestment_rate: float | None, roe: float | None) -> float | None:
    """Damodaran：预期增速 = 股权再投资率 × ROE。缺一不可。"""
    if reinvestment_rate is None or roe is None:
        return None
    return float(reinvestment_rate) * float(roe)


def stable_cash_conversion(perpetuity_growth: float, stable_roe: float | None) -> float | None:
    """稳定期再投资率 = 永续增速 / ROE_stable，现金转换 = 1 − 再投资率，再夹到 [0, 1]。

    ROE_stable 缺失或非正时返回 None，不再按 1 计。
    """
    if stable_roe is None or stable_roe <= 0:
        return None
    reinvestment = perpetuity_growth / stable_roe
    return min(1.0, max(0.0, 1.0 - reinvestment))


def derive_base_inputs(
    fundamentals: CompanyFundamentals,
    industry: IndustryStats | None,
    discount_rate: float,
    config: ValuationConfig,
    estimates: dict[str, float] | None = None,
    approvals_path: str | Path | None = None,
) -> DerivedInputs:
    """基准情景。estimates 不进入主估值。approvals_path 缺省读仓库里的审核文件。"""
    del estimates  # 分析师预期不是该公司财报，不混进主路径
    approvals = load_approvals(approvals_path)
    template = select_template(fundamentals)
    code = fundamentals.ts_code
    items: list[FallbackItem] = []

    revenue, revenue_item = resolve_input(
        "revenue_base",
        fundamentals.revenue_ttm,
        reason="TTM 收入无法计算。没有另一条收入口径可以代替。",
        proposed=None,
        ts_code=code,
        approvals=approvals,
    )
    _keep(items, revenue_item)

    shares, shares_item = resolve_input(
        "shares",
        fundamentals.shares,
        reason="总股本无法从总市值和现价算出。",
        proposed=None,
        ts_code=code,
        approvals=approvals,
    )
    _keep(items, shares_item)

    growth_model = fundamental_growth(fundamentals.equity_reinvestment_rate, fundamentals.roe_for_growth)
    growth, growth_item = resolve_input(
        "growth_start",
        growth_model,
        reason=_growth_reason(fundamentals),
        proposed=fundamentals.revenue_cagr_5y,
        ts_code=code,
        approvals=approvals,
    )
    _keep(items, growth_item)

    margin_start, margin_item = resolve_input(
        "margin_start",
        fundamentals.margin_ttm,
        reason="TTM 净利率无法计算。不用 10%，也不用行业中位数。",
        proposed=fundamentals.margin_mean_5y,
        ts_code=code,
        approvals=approvals,
    )
    _keep(items, margin_item)

    terminal_model = _terminal_margin(fundamentals.margin_ttm, fundamentals.margin_mean_5y)
    margin_terminal, terminal_item = resolve_input(
        "margin_terminal",
        terminal_model,
        reason=_terminal_margin_reason(fundamentals),
        proposed=_terminal_margin_proposal(fundamentals),
        ts_code=code,
        approvals=approvals,
    )
    _keep(items, terminal_item)

    conversion, conversion_item = _resolve_conversion(fundamentals, template, approvals)
    _keep(items, conversion_item)

    stable_roe, stable_conv, stable_item = _resolve_stable(
        fundamentals,
        config.perpetuity_growth,
        approvals,
    )
    _keep(items, stable_item)

    perpetuity = _capped_growth(config.perpetuity_growth, discount_rate)
    if stable_roe is not None and stable_roe > 0:
        stable_conv = stable_cash_conversion(perpetuity, stable_roe)

    return DerivedInputs(
        revenue_base=revenue,
        growth_start=growth,
        growth_terminal=_capped_growth(config.terminal_growth, discount_rate),
        margin_start=margin_start,
        margin_terminal=margin_terminal,
        cash_conversion=conversion,
        forecast_years=config.forecast_years,
        discount_rate=discount_rate,
        exit_multiple=_exit_multiple(industry, "pe" if template == "net_income" else config.exit_basis),
        exit_basis="pe" if template == "net_income" else config.exit_basis,
        shares=shares,
        terminal_method="gordon",
        perpetuity_growth=perpetuity,
        stable_roe=stable_roe,
        stable_cash_conversion=stable_conv,
        fallbacks=tuple(items),
        template=template,
    )


def derive_scenarios(
    base: DerivedInputs,
    fundamentals: CompanyFundamentals,
    config: ValuationConfig,
    industry: IndustryStats | None = None,
) -> dict[str, DerivedInputs]:
    """熊/牛的增速和净利率用本公司历史低位/高位。折现率和永续增速的点差是情景设计。"""
    bear = _scenario(
        base,
        fundamentals,
        config,
        industry,
        growth=fundamentals.revenue_growth_low,
        growth_samples=fundamentals.revenue_growth_samples,
        growth_field="bear_growth_start",
        margin=fundamentals.margin_low,
        margin_samples=fundamentals.margin_samples,
        margin_field="bear_margin_terminal",
        discount=base.discount_rate + config.bear_discount_spread,
        perpetuity_target=max(0.0, config.perpetuity_growth + config.bear_perpetuity_spread),
        exit_quantile="p25",
    )
    bull_discount = max(0.01, base.discount_rate + config.bull_discount_spread)
    bull = _scenario(
        base,
        fundamentals,
        config,
        industry,
        growth=fundamentals.revenue_growth_high,
        growth_samples=fundamentals.revenue_growth_samples,
        growth_field="bull_growth_start",
        margin=fundamentals.margin_high,
        margin_samples=fundamentals.margin_samples,
        margin_field="bull_margin_terminal",
        discount=bull_discount,
        perpetuity_target=config.perpetuity_growth + config.bull_perpetuity_spread,
        exit_quantile="p75",
    )
    return {"bear": bear, "base": base, "bull": bull}


def _scenario(
    base: DerivedInputs,
    fundamentals: CompanyFundamentals,
    config: ValuationConfig,
    industry: IndustryStats | None,
    *,
    growth: float | None,
    growth_samples: int,
    growth_field: str,
    margin: float | None,
    margin_samples: int,
    margin_field: str,
    discount: float,
    perpetuity_target: float,
    exit_quantile: str,
) -> DerivedInputs:
    fallbacks = [item for item in base.fallbacks if item.field not in {"growth_start", "margin_terminal"}]
    growth_start = growth if growth_samples >= SCENARIO_MIN_SAMPLES and growth is not None else None
    if growth_start is None:
        fallbacks.append(
            FallbackItem(
                field=growth_field,
                reason=(
                    f"本公司历史年度收入增速只有 {growth_samples} 个样本，少于 {SCENARIO_MIN_SAMPLES} 个，"
                    "熊/牛增速无法计算。不再用全局倍数去改基准增速。"
                ),
                proposed=None,
            )
        )
    margin_terminal = margin if margin_samples >= SCENARIO_MIN_SAMPLES and margin is not None else None
    if margin_terminal is None:
        fallbacks.append(
            FallbackItem(
                field=margin_field,
                reason=(
                    f"本公司历史年度净利率只有 {margin_samples} 个样本，少于 {SCENARIO_MIN_SAMPLES} 个，"
                    "熊/牛净利率无法计算。不再用行业分位或全局倍数。"
                ),
                proposed=None,
            )
        )
    perpetuity = min(perpetuity_target, discount - 0.01)
    stable_conv = base.stable_cash_conversion
    if base.stable_roe is not None and base.stable_roe > 0:
        stable_conv = stable_cash_conversion(perpetuity, base.stable_roe)
    return replace(
        base,
        growth_start=growth_start,
        growth_terminal=_capped_growth(config.terminal_growth, discount),
        margin_terminal=margin_terminal,
        discount_rate=discount,
        perpetuity_growth=perpetuity,
        stable_cash_conversion=stable_conv,
        exit_multiple=_exit_multiple(industry, base.exit_basis, quantile=exit_quantile),
        fallbacks=tuple(fallbacks),
    )


def _resolve_conversion(fundamentals, template: str, approvals):
    if template == "net_income":
        return 1.0, None
    historical = fundamentals.fcf_conversion
    if historical is None:
        return resolve_input(
            "cash_conversion",
            None,
            reason="近 5 个盈利年度凑不出 (经营现金流−资本开支)/归母净利，高成长期现金转换率无法计算。不用 0.9，也不再夹到 0.5–1.2。",
            proposed=None,
            ts_code=fundamentals.ts_code,
            approvals=approvals,
        )
    if historical < 0:
        return resolve_input(
            "cash_conversion",
            historical,
            reason=(
                f"近 5 个盈利年度 (经营现金流−资本开支)/归母净利 的中位数为 {historical:.1%}。"
                "这个负值按财报保留，不再夹到 50%。"
                "若不愿把现金流出外推到预测期，可在审核文件里把 cash_conversion 批准为 1.0 或 0；"
                "若接受外推，把批准值写成这个中位数。"
                "1.0 和 0 不是财报里的另一条口径，建议兜底留空。审核通过前，负值也不进入估值。"
            ),
            proposed=None,
            ts_code=fundamentals.ts_code,
            approvals=approvals,
            block_computed=True,
        )
    return resolve_input(
        "cash_conversion",
        historical,
        reason="",
        proposed=None,
        ts_code=fundamentals.ts_code,
        approvals=approvals,
    )


def _resolve_stable(fundamentals, perpetuity: float, approvals):
    median = fundamentals.roe_median
    if median is not None and median > 0:
        return median, stable_cash_conversion(perpetuity, median), None
    reason = _stable_reason(fundamentals)
    approved_roe = resolve_input(
        "stable_roe",
        None,
        reason=reason,
        proposed=None,
        ts_code=fundamentals.ts_code,
        approvals=approvals,
        block_computed=True,
    )
    if approved_roe[0] is not None and approved_roe[0] > 0:
        roe = approved_roe[0]
        return roe, stable_cash_conversion(perpetuity, roe), approved_roe[1]
    approved_conv = resolve_input(
        "stable_cash_conversion",
        None,
        reason=reason,
        proposed=None,
        ts_code=fundamentals.ts_code,
        approvals=approvals,
    )
    # computed 记在最终待审条目上，这里不把非正的历史中位数当成已经可用的转换率。
    if approved_conv[0] is not None:
        return None, approved_conv[0], approved_conv[1]
    return None, None, FallbackItem(
        field="stable_cash_conversion",
        reason=reason,
        proposed=None,
        computed=median,
    )


def _stable_reason(fundamentals: CompanyFundamentals) -> str:
    median = fundamentals.roe_median
    if median is None:
        detail = fundamentals.roe_reason or "历史 ROE 无法计算。没有编造股东权益，也不再用固定的 10%。"
    else:
        detail = f"历史 ROE 中位数为 {median:.1%}，不是正数，稳定期再投资率 = 永续增速 / ROE 没有定义。"
    return detail + "稳定期现金转换率无法计算。建议兜底留空。"


def _growth_reason(fundamentals: CompanyFundamentals) -> str:
    if fundamental_growth(fundamentals.equity_reinvestment_rate, fundamentals.roe_for_growth) is not None:
        return ""
    parts: list[str] = []
    if fundamentals.equity_reinvestment_rate is None:
        parts.append(fundamentals.reinvestment_reason or "股权再投资率无法计算。")
    if fundamentals.roe_for_growth is None:
        parts.append(fundamentals.roe_reason or "ROE 无法计算。没有编造股东权益。")
    parts.append("起始增速 = 股权再投资率 × ROE。缺了其中一项就不能算，不用行业中位数，也不用 2.5%。")
    if fundamentals.revenue_cagr_5y is None:
        parts.append("本公司收入复合增速也算不出，建议兜底留空。")
    else:
        parts.append("建议兜底改用本公司收入复合增速，审核通过前不进入估值。")
    return "".join(parts)


def _terminal_margin(margin_ttm: float | None, margin_mean_5y: float | None) -> float | None:
    """终值净利率不超过当前：min(TTM 净利率, 近 5 年合计净利率)。不再向行业中位数靠拢。"""
    if margin_ttm is None or margin_mean_5y is None:
        return None
    return min(float(margin_ttm), float(margin_mean_5y))


def _terminal_margin_reason(fundamentals: CompanyFundamentals) -> str:
    if fundamentals.margin_ttm is None and fundamentals.margin_mean_5y is None:
        return "TTM 净利率和近 5 年合计净利率都无法计算，终值净利率无法计算。不用 10%，也不用行业中位数。"
    if fundamentals.margin_mean_5y is None:
        return "近 5 年合计净利率无法计算，终值净利率无法计算。不再向行业中位数靠拢。"
    return "TTM 净利率无法计算，终值净利率无法计算。不用 10%，也不用行业中位数。"


def _terminal_margin_proposal(fundamentals: CompanyFundamentals) -> float | None:
    if fundamentals.margin_mean_5y is None:
        return fundamentals.margin_ttm
    if fundamentals.margin_ttm is None:
        return fundamentals.margin_mean_5y
    return None


def _capped_growth(target: float, discount_rate: float) -> float:
    """长期增速必须低于折现率 1 个百分点。"""
    return min(float(target), float(discount_rate) - 0.01)


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


def _keep(items: list[FallbackItem], item: FallbackItem | None) -> None:
    if item is not None:
        items.append(item)
