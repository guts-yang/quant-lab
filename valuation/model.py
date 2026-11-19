"""AlphaSpread 式 FCFE 折现现金流引擎。纯函数，不访问网络。

公式骨架（N 年预测期）：
    Rev_t  = Rev_{t-1} × (1 + g_t)，g_t 从 growth_start 线性收敛到 growth_terminal
    m_t    = margin_start + (margin_terminal − margin_start) × (t-1)/(N-1)，第 1 年等于起点
    FCFE_t = Rev_t × m_t × cash_conversion
    TV     = FCFE_{N+1} / (r − g)（Gordon 永续，默认）
             FCFE_{N+1} = Rev_N × (1+g) × margin_terminal × stable_cash_conversion
             stable_cash_conversion = 1 − g/ROE_stable（Damodaran 稳定期再投资一致性）
    V/share = [Σ FCFE_t/(1+r)^t + TV/(1+r)^N] / shares

终值默认用 Gordon 永续增长，不用行业中位市销率退出倍数：P/S = P/E × 净利率，
行业 P/S 隐含行业典型利润率，直接套到利润率差异大的公司会把估值打飞。
退出倍数法保留作交叉校验（terminal_method="multiple"）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

EXIT_BASES = ("ps", "pe")
TERMINAL_METHODS = ("multiple", "gordon")

# 反向 DCF 二分法求解的增速边界。收紧到 [-0.5, 1.0]：超出即标「未收敛」，
# 不再报 140% 这种无意义的隐含增速。
REVERSE_GROWTH_FLOOR = -0.5
REVERSE_GROWTH_CAP = 1.0


@dataclass(frozen=True)
class DcfInputs:
    revenue_base: float  # TTM 收入，元
    growth_start: float  # 第 1 年收入增速
    growth_terminal: float  # 第 N 年收入增速（线性收敛目标）
    margin_start: float  # 当前净利率
    margin_terminal: float  # 第 N 年净利率
    cash_conversion: float  # 高成长期 FCFE / 净利润
    forecast_years: int  # 预测年数 N
    discount_rate: float  # 股权成本
    exit_multiple: float  # 退出倍数（仅 multiple 法或交叉校验用）
    exit_basis: str  # "ps"（收入倍数）或 "pe"（利润倍数）
    shares: float  # 总股本，股
    terminal_method: str = "gordon"  # "gordon"（默认）或 "multiple"
    perpetuity_growth: float = 0.025  # gordon 法的永续增速
    stable_roe: float = 0.10  # 稳定期 ROE，用于推导再投资率 g/ROE
    stable_cash_conversion: float | None = None  # 稳定期 FCFE/NI；缺省按 1 - g/ROE 推导


@dataclass(frozen=True)
class DcfResult:
    inputs: DcfInputs
    projection: pd.DataFrame
    terminal_value: float
    pv_terminal: float
    pv_explicit: float
    equity_value: float
    per_share: float
    terminal_weight: float  # 终值现值占股权价值的比例
    terminal_warning: str | None = None  # 终值占比过高或稳定期不可持续时的提示


@dataclass(frozen=True)
class ImpliedGrowth:
    growth_start: float  # 反解出的第 1 年增速
    average_growth: float  # 对应的预测期平均增速
    per_share: float  # 用该增速复算的每股价值，应约等于市价
    converged: bool  # 二分法是否在边界内收敛


def growth_path(growth_start: float, growth_terminal: float, years: int) -> list[float]:
    """第 1 年等于 growth_start，第 N 年等于 growth_terminal，中间线性过渡。"""
    if years < 1:
        raise ValueError("forecast_years 至少为 1")
    if years == 1:
        return [float(growth_start)]
    step = (growth_terminal - growth_start) / (years - 1)
    return [float(growth_start + step * i) for i in range(years)]


def margin_path(margin_start: float, margin_terminal: float, years: int) -> list[float]:
    """从当前净利率线性衰减/爬升到终值净利率，第 1 年等于起点，第 N 年等于终值。"""
    if years < 1:
        raise ValueError("forecast_years 至少为 1")
    if years == 1:
        return [float(margin_start)]
    step = (margin_terminal - margin_start) / (years - 1)
    return [float(margin_start + step * t) for t in range(years)]


def average_growth(inputs: DcfInputs) -> float:
    """预测期平均增速，对应 AlphaSpread 页面展示的单个 Revenue Growth 数字。"""
    path = growth_path(inputs.growth_start, inputs.growth_terminal, inputs.forecast_years)
    return float(sum(path) / len(path))


def project_fcfe(inputs: DcfInputs) -> pd.DataFrame:
    """逐年的收入、净利率、净利润、FCFE、折现因子和 FCFE 现值。"""
    _validate(inputs)
    growths = growth_path(inputs.growth_start, inputs.growth_terminal, inputs.forecast_years)
    margins = margin_path(inputs.margin_start, inputs.margin_terminal, inputs.forecast_years)
    rows: list[dict[str, float]] = []
    revenue = float(inputs.revenue_base)
    for index, (growth, margin) in enumerate(zip(growths, margins, strict=True), start=1):
        revenue = revenue * (1.0 + growth)
        net_income = revenue * margin
        fcfe = net_income * inputs.cash_conversion
        discount_factor = 1.0 / (1.0 + inputs.discount_rate) ** index
        rows.append(
            {
                "year": index,
                "growth": growth,
                "revenue": revenue,
                "margin": margin,
                "net_income": net_income,
                "fcfe": fcfe,
                "discount_factor": discount_factor,
                "pv_fcfe": fcfe * discount_factor,
            }
        )
    return pd.DataFrame(rows)


def stable_conversion(inputs: DcfInputs) -> float:
    """稳定期 FCFE/净利润。Damodaran：稳定期再投资率 = g/ROE，故 FCFE/NI = 1 - g/ROE。

    显式给定的 stable_cash_conversion 优先；否则用 perpetuity_growth / stable_roe 推导，
    并夹到 [0, 1] 防止再投资率为负或超过 100%。
    """
    if inputs.stable_cash_conversion is not None:
        return float(inputs.stable_cash_conversion)
    if inputs.stable_roe <= 0:
        return 1.0
    reinvestment = inputs.perpetuity_growth / inputs.stable_roe
    return min(1.0, max(0.0, 1.0 - reinvestment))


def terminal_value(inputs: DcfInputs, projection: pd.DataFrame) -> float:
    """Gordon 永续（默认）或退出倍数两种终值。

    Gordon：TV = FCFE_{N+1} / (r - g)，其中 FCFE_{N+1} 用稳定期现金转换率，
    而不是高成长期的 cash_conversion，保证稳定期再投资与永续增速自洽。
    终值净利率非正时，永续期生意不可持续，终值按 0 计。
    """
    _validate(inputs)
    last = projection.iloc[-1]
    if inputs.terminal_method == "gordon":
        r, g = inputs.discount_rate, inputs.perpetuity_growth
        if r <= g:
            raise ValueError(f"Gordon 终值要求折现率大于永续增速，得到 r={r}、g={g}")
        if inputs.margin_terminal <= 0:
            return 0.0
        stable_rev = float(last["revenue"]) * (1.0 + g)
        stable_ni = stable_rev * inputs.margin_terminal
        stable_fcfe = stable_ni * stable_conversion(inputs)
        return float(stable_fcfe) / (r - g)
    if inputs.exit_basis == "ps":
        return float(last["revenue"]) * inputs.exit_multiple
    return float(last["net_income"]) * inputs.exit_multiple


def dcf_value(inputs: DcfInputs) -> DcfResult:
    """股权价值 = Σ PV(FCFE) + PV(终值)，每股价值 = 股权价值 / 总股本。"""
    projection = project_fcfe(inputs)
    tv = terminal_value(inputs, projection)
    discount_factor_n = float(projection["discount_factor"].iloc[-1])
    pv_terminal = tv * discount_factor_n
    pv_explicit = float(projection["pv_fcfe"].sum())
    equity = pv_explicit + pv_terminal
    per_share = equity / inputs.shares
    weight = pv_terminal / equity if equity else 0.0
    warning = _terminal_warning(inputs, weight, equity)
    return DcfResult(
        inputs=inputs,
        projection=projection,
        terminal_value=tv,
        pv_terminal=pv_terminal,
        pv_explicit=pv_explicit,
        equity_value=equity,
        per_share=per_share,
        terminal_weight=weight,
        terminal_warning=warning,
    )


def _terminal_warning(inputs: DcfInputs, weight: float, equity: float) -> str | None:
    """终值占比过高或稳定期不可持续时给出可审计的提示。"""
    if inputs.terminal_method == "gordon" and inputs.margin_terminal <= 0:
        return "稳定期净利率非正，永续终值按 0 计"
    if equity > 0 and weight > 0.85:
        return f"终值占股权价值 {weight:.0%}，估值几乎由永续假设决定"
    return None


def reverse_dcf(inputs: DcfInputs, price: float) -> ImpliedGrowth:
    """固定其他假设，反解使 DCF 价值等于市价的第 1 年增速（二分法）。"""
    if price <= 0:
        raise ValueError("price 必须为正")

    def gap(growth_start: float) -> float:
        return dcf_value(replace(inputs, growth_start=growth_start)).per_share - price

    lo, hi = REVERSE_GROWTH_FLOOR, REVERSE_GROWTH_CAP
    f_lo, f_hi = gap(lo), gap(hi)
    if f_lo >= 0:
        solved = replace(inputs, growth_start=lo)
        return ImpliedGrowth(lo, average_growth(solved), dcf_value(solved).per_share, converged=False)
    if f_hi <= 0:
        solved = replace(inputs, growth_start=hi)
        return ImpliedGrowth(hi, average_growth(solved), dcf_value(solved).per_share, converged=False)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if gap(mid) > 0:
            hi = mid
        else:
            lo = mid
    growth_start = (lo + hi) / 2.0
    solved = replace(inputs, growth_start=growth_start)
    return ImpliedGrowth(growth_start, average_growth(solved), dcf_value(solved).per_share, converged=True)


def sensitivity_grid(
    inputs: DcfInputs,
    growth_deltas: tuple[float, ...] = (-0.05, -0.025, 0.0, 0.025, 0.05),
    discount_deltas: tuple[float, ...] = (-0.02, -0.01, 0.0, 0.01, 0.02),
) -> pd.DataFrame:
    """每股价值对「起始增速 × 折现率」的敏感性矩阵。行是折现率，列是起始增速。"""
    rows: dict[float, dict[float, float]] = {}
    for dd in discount_deltas:
        rate = round(inputs.discount_rate + dd, 6)
        row: dict[float, float] = {}
        for dg in growth_deltas:
            growth = round(inputs.growth_start + dg, 6)
            varied = replace(inputs, growth_start=growth, discount_rate=rate)
            try:
                row[growth] = dcf_value(varied).per_share
            except ValueError:
                row[growth] = float("nan")
        rows[rate] = row
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    frame.index.name = "discount_rate"
    return frame


def _validate(inputs: DcfInputs) -> None:
    if inputs.revenue_base <= 0:
        raise ValueError("revenue_base 必须为正")
    if inputs.shares <= 0:
        raise ValueError("shares 必须为正")
    if inputs.discount_rate <= 0:
        raise ValueError("discount_rate 必须为正")
    if inputs.forecast_years < 1:
        raise ValueError("forecast_years 至少为 1")
    if inputs.exit_basis not in EXIT_BASES:
        raise ValueError(f"exit_basis 只能是 {EXIT_BASES}")
    if inputs.terminal_method not in TERMINAL_METHODS:
        raise ValueError(f"terminal_method 只能是 {TERMINAL_METHODS}")
