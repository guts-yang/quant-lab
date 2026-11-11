"""AlphaSpread 式 FCFE 折现现金流引擎。纯函数，不访问网络。

公式骨架（N 年预测期）：
    Rev_t  = Rev_{t-1} × (1 + g_t)，g_t 从 growth_start 线性收敛到 growth_terminal
    m_t    = margin_start + (margin_terminal − margin_start) × t/N
    FCFE_t = Rev_t × m_t × cash_conversion
    TV     = Rev_N × 退出 P/S（或 NI_N × 退出 P/E，或 Gordon 永续）
    V/share = [Σ FCFE_t/(1+r)^t + TV/(1+r)^N] / shares
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

EXIT_BASES = ("ps", "pe")
TERMINAL_METHODS = ("multiple", "gordon")

# 反向 DCF 二分法求解的增速边界。上限 500% 是为了能包住极端成长股。
REVERSE_GROWTH_FLOOR = -0.9
REVERSE_GROWTH_CAP = 5.0


@dataclass(frozen=True)
class DcfInputs:
    revenue_base: float  # TTM 收入，元
    growth_start: float  # 第 1 年收入增速
    growth_terminal: float  # 第 N 年收入增速（线性收敛目标）
    margin_start: float  # 当前净利率
    margin_terminal: float  # 第 N 年净利率
    cash_conversion: float  # FCFE / 净利润
    forecast_years: int  # 预测年数 N
    discount_rate: float  # 股权成本
    exit_multiple: float  # 退出倍数
    exit_basis: str  # "ps"（收入倍数）或 "pe"（利润倍数）
    shares: float  # 总股本，股
    terminal_method: str = "multiple"  # "multiple" 或 "gordon"
    perpetuity_growth: float = 0.025  # gordon 法的永续增速


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
    """从当前净利率线性衰减/爬升到终值净利率，第 N 年恰好等于终值。"""
    if years < 1:
        raise ValueError("forecast_years 至少为 1")
    step = (margin_terminal - margin_start) / years
    return [float(margin_start + step * t) for t in range(1, years + 1)]


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


def terminal_value(inputs: DcfInputs, projection: pd.DataFrame) -> float:
    """退出倍数或 Gordon 永续两种终值。"""
    _validate(inputs)
    last = projection.iloc[-1]
    if inputs.terminal_method == "gordon":
        r, g = inputs.discount_rate, inputs.perpetuity_growth
        if r <= g:
            raise ValueError(f"Gordon 终值要求折现率大于永续增速，得到 r={r}、g={g}")
        return float(last["fcfe"]) * (1.0 + g) / (r - g)
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
    return DcfResult(
        inputs=inputs,
        projection=projection,
        terminal_value=tv,
        pv_terminal=pv_terminal,
        pv_explicit=pv_explicit,
        equity_value=equity,
        per_share=per_share,
        terminal_weight=weight,
    )


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
