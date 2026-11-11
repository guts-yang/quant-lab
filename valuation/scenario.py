"""三情景估值编排与产物输出。报告格式对齐 data/research/ 下既有研究产物。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from valuation.assumptions import (
    CompanyFundamentals,
    EstimatesProvider,
    IndustryStats,
    ValuationConfig,
    derive_base_inputs,
    derive_scenarios,
    select_template,
)
from valuation.data import ValuationData, load_valuation_inputs
from valuation.discount import cost_of_equity, estimate_beta
from valuation.model import (
    DcfResult,
    ImpliedGrowth,
    average_growth,
    dcf_value,
    reverse_dcf,
    sensitivity_grid,
)

SCENARIO_NAMES = {"bear": "熊市", "base": "基准", "bull": "牛市"}
DEFAULT_BETA = 1.0


@dataclass(frozen=True)
class ValuationReport:
    ts_code: str
    asof: pd.Timestamp
    template: str
    fundamentals: CompanyFundamentals
    industry: IndustryStats | None
    beta: float
    discount_rate: float
    scenarios: dict[str, DcfResult]
    implied: ImpliedGrowth | None
    sensitivity: pd.DataFrame
    config: ValuationConfig


def run_valuation(
    provider,
    ts_code: str,
    asof: pd.Timestamp,
    config: ValuationConfig | None = None,
    estimates: EstimatesProvider | None = None,
) -> ValuationReport:
    """取数 → 假设 → 三情景估值 → 反向 DCF → 敏感性。"""
    config = config or ValuationConfig()
    asof = pd.Timestamp(asof).normalize()
    data: ValuationData = load_valuation_inputs(provider, ts_code, asof)

    beta = estimate_beta(data.stock_daily, data.index_daily)
    if beta is None:
        beta = DEFAULT_BETA
    rate = cost_of_equity(config.risk_free, beta, config.equity_risk_premium)

    company_estimates = estimates.get(str(ts_code)) if estimates is not None else None
    base = derive_base_inputs(data.fundamentals, data.industry, rate, config, company_estimates)
    inputs = derive_scenarios(base, data.industry, config)
    scenarios = {name: dcf_value(scenario_inputs) for name, scenario_inputs in inputs.items()}

    price = data.fundamentals.price
    implied = reverse_dcf(base, price) if price else None
    sensitivity = sensitivity_grid(base)
    return ValuationReport(
        ts_code=str(ts_code),
        asof=asof,
        template=select_template(data.fundamentals),
        fundamentals=data.fundamentals,
        industry=data.industry,
        beta=beta,
        discount_rate=rate,
        scenarios=scenarios,
        implied=implied,
        sensitivity=sensitivity,
        config=config,
    )


def write_outputs(report: ValuationReport, out_dir: str | Path) -> None:
    """assumptions.json、forecast.parquet、scenarios.parquet、sensitivity.parquet、report.md。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "assumptions.json").write_text(_assumptions_json(report), encoding="utf-8")
    report.scenarios["base"].projection.to_parquet(out / "forecast.parquet", index=False)
    _scenarios_frame(report).to_parquet(out / "scenarios.parquet", index=False)
    report.sensitivity.to_parquet(out / "sensitivity.parquet")
    (out / "report.md").write_text(render_report(report), encoding="utf-8")


def render_report(report: ValuationReport) -> str:
    fundamentals = report.fundamentals
    lines = [
        f"# DCF 估值 {report.ts_code}（asof {report.asof:%Y-%m-%d}）",
        "",
        f"- 行业：{fundamentals.industry or '未知'}　模板：{report.template}",
        f"- 现价：{_fmt(fundamentals.price)} 元　总市值：{_fmt_yi(fundamentals.total_mv)}",
        (
            f"- TTM：收入 {_fmt_yi(fundamentals.revenue_ttm)}，归母净利 {_fmt_yi(fundamentals.net_income_ttm)}，"
            f"FCF {_fmt_yi(fundamentals.fcf_ttm)}"
        ),
        (
            f"- 历史：5 年收入 CAGR {_fmt_pct(fundamentals.revenue_cagr_5y)}，"
            f"TTM 净利率 {_fmt_pct(fundamentals.margin_ttm)}，FCF 转换率 {_fmt_pct(fundamentals.fcf_conversion)}"
        ),
        (
            f"- 折现率：{report.discount_rate:.2%}（β={report.beta:.2f}，rf={report.config.risk_free:.2%}，"
            f"ERP={report.config.equity_risk_premium:.1%}）"
        ),
        "",
        "## 三情景",
        "",
        "| 情景 | 平均增速 | 净利率路径 | 转换率 | 折现率 | 退出倍数 | 每股价值 | 相对现价 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    price = fundamentals.price
    for name in ("bear", "base", "bull"):
        result = report.scenarios[name]
        inputs = result.inputs
        upside = result.per_share / price - 1.0 if price else None
        lines.append(
            f"| {SCENARIO_NAMES[name]} | {average_growth(inputs):.1%} | "
            f"{inputs.margin_start:.1%}→{inputs.margin_terminal:.1%} | {inputs.cash_conversion:.0%} | "
            f"{inputs.discount_rate:.1%} | {inputs.exit_multiple:.1f}x {inputs.exit_basis.upper()} | "
            f"{result.per_share:.2f} | {_fmt_pct(upside)} |"
        )
    lines += ["", "## 反向 DCF", ""]
    if report.implied is not None:
        suffix = "" if report.implied.converged else "（触及求解边界，未收敛）"
        lines.append(
            f"现价隐含第 1 年增速 {report.implied.growth_start:.1%}、"
            f"预测期平均增速 {report.implied.average_growth:.1%}{suffix}。"
        )
    else:
        lines.append("缺少现价，未计算。")
    lines += ["", "## 敏感性（每股价值，基准情景）", ""]
    header = "| 折现率＼起始增速 | " + " | ".join(f"{column:.1%}" for column in report.sensitivity.columns) + " |"
    lines.append(header)
    lines.append("|" + " --- |" * (len(report.sensitivity.columns) + 1))
    for rate, row in report.sensitivity.iterrows():
        cells = " | ".join(f"{value:.2f}" for value in row)
        lines.append(f"| {rate:.1%} | {cells} |")
    base_result = report.scenarios["base"]
    lines += [
        "",
        f"终值占股权价值 {base_result.terminal_weight:.0%}（基准）。",
        "完整预测见 forecast.parquet，假设见 assumptions.json。",
        "",
    ]
    return "\n".join(lines)


def _scenarios_frame(report: ValuationReport) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    price = report.fundamentals.price
    for name, result in report.scenarios.items():
        inputs = result.inputs
        rows.append(
            {
                "scenario": name,
                "growth_start": inputs.growth_start,
                "average_growth": average_growth(inputs),
                "growth_terminal": inputs.growth_terminal,
                "margin_start": inputs.margin_start,
                "margin_terminal": inputs.margin_terminal,
                "cash_conversion": inputs.cash_conversion,
                "discount_rate": inputs.discount_rate,
                "exit_multiple": inputs.exit_multiple,
                "exit_basis": inputs.exit_basis,
                "per_share": result.per_share,
                "equity_value": result.equity_value,
                "pv_explicit": result.pv_explicit,
                "pv_terminal": result.pv_terminal,
                "terminal_weight": result.terminal_weight,
                "upside": result.per_share / price - 1.0 if price else None,
            }
        )
    return pd.DataFrame(rows)


def _assumptions_json(report: ValuationReport) -> str:
    fundamentals = report.fundamentals
    payload = {
        "ts_code": report.ts_code,
        "asof": report.asof.strftime("%Y%m%d"),
        "template": report.template,
        "fundamentals": {
            "industry": fundamentals.industry,
            "comp_type": fundamentals.comp_type,
            "price": fundamentals.price,
            "total_mv": fundamentals.total_mv,
            "shares": fundamentals.shares,
            "revenue_ttm": fundamentals.revenue_ttm,
            "net_income_ttm": fundamentals.net_income_ttm,
            "ocf_ttm": fundamentals.ocf_ttm,
            "fcf_ttm": fundamentals.fcf_ttm,
            "revenue_cagr_5y": fundamentals.revenue_cagr_5y,
            "margin_ttm": fundamentals.margin_ttm,
            "margin_mean_5y": fundamentals.margin_mean_5y,
            "fcf_conversion": fundamentals.fcf_conversion,
        },
        "discount": {
            "beta": report.beta,
            "risk_free": report.config.risk_free,
            "equity_risk_premium": report.config.equity_risk_premium,
            "cost_of_equity": report.discount_rate,
        },
        "scenarios": {
            name: {
                "growth_start": result.inputs.growth_start,
                "average_growth": average_growth(result.inputs),
                "growth_terminal": result.inputs.growth_terminal,
                "margin_start": result.inputs.margin_start,
                "margin_terminal": result.inputs.margin_terminal,
                "cash_conversion": result.inputs.cash_conversion,
                "discount_rate": result.inputs.discount_rate,
                "exit_multiple": result.inputs.exit_multiple,
                "exit_basis": result.inputs.exit_basis,
                "per_share": result.per_share,
            }
            for name, result in report.scenarios.items()
        },
        "implied_growth": (
            {
                "growth_start": report.implied.growth_start,
                "average_growth": report.implied.average_growth,
                "converged": report.implied.converged,
            }
            if report.implied is not None
            else None
        ),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "缺失"


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "缺失"


def _fmt_yi(value: float | None) -> str:
    return f"{value / 1e8:.1f} 亿" if value is not None else "缺失"
