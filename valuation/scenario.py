"""三情景估值编排与产物输出。报告格式对齐 data/research/ 下既有研究产物。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from valuation.assumptions import (
    CompanyFundamentals,
    DerivedInputs,
    EstimatesProvider,
    IndustryStats,
    ValuationConfig,
    derive_base_inputs,
    derive_scenarios,
    select_template,
)
from valuation.data import ValuationData, load_valuation_inputs
from valuation.discount import cost_of_equity, estimate_beta
from valuation.fallbacks import (
    STATUS_APPROVED,
    STATUS_PENDING,
    FallbackItem,
    approvals_display,
    field_label,
    format_quantity,
)
from valuation.model import (
    DcfResult,
    ImpliedGrowth,
    dcf_value,
    growth_path,
    reverse_dcf,
    sensitivity_grid,
)

SCENARIO_NAMES = {"bear": "熊市", "base": "基准", "bull": "牛市"}
DEFAULT_BETA = 1.0


def security_label(name: str | None, code: str) -> str:
    """公司名称在前，代码放在括号里。没有名称时只留代码。"""
    company = str(name or "").strip()
    if company in {"", "None", "nan", "<NA>"}:
        return str(code)
    return f"{company}（{code}）"


@dataclass(frozen=True)
class ScenarioOutcome:
    inputs: DerivedInputs
    result: DcfResult | None

    @property
    def per_share(self) -> float | None:
        if self.result is None:
            return None
        return float(self.result.per_share)


@dataclass(frozen=True)
class ValuationReport:
    ts_code: str
    asof: pd.Timestamp
    template: str
    fundamentals: CompanyFundamentals
    industry: IndustryStats | None
    beta: float
    discount_rate: float
    scenarios: dict[str, ScenarioOutcome]
    implied: ImpliedGrowth | None
    sensitivity: pd.DataFrame
    config: ValuationConfig
    fallbacks: tuple[FallbackItem, ...]
    approvals_path: str

    @property
    def pending(self) -> tuple[FallbackItem, ...]:
        return tuple(item for item in self.fallbacks if item.status == STATUS_PENDING)


def value_derived(inputs: DerivedInputs) -> ScenarioOutcome:
    """关键输入未齐时不调用折现引擎，每股价值留空。"""
    model_inputs = inputs.to_dcf()
    if model_inputs is None:
        return ScenarioOutcome(inputs=inputs, result=None)
    return ScenarioOutcome(inputs=inputs, result=dcf_value(model_inputs))


def run_valuation(
    provider,
    ts_code: str,
    asof: pd.Timestamp,
    config: ValuationConfig | None = None,
    estimates: EstimatesProvider | None = None,
    approvals_path: str | Path | None = None,
) -> ValuationReport:
    """取数 → 假设 → 三情景。主路径仍有待审项时不算正式每股价值。"""
    config = config or ValuationConfig()
    asof = pd.Timestamp(asof).normalize()
    data: ValuationData = load_valuation_inputs(provider, ts_code, asof)

    beta = estimate_beta(data.stock_daily, data.index_daily)
    if beta is None:
        beta = DEFAULT_BETA
    rate = cost_of_equity(config.risk_free, beta, config.equity_risk_premium)

    company_estimates = estimates.get(str(ts_code)) if estimates is not None else None
    base = derive_base_inputs(
        data.fundamentals,
        data.industry,
        rate,
        config,
        company_estimates,
        approvals_path=approvals_path,
    )
    drafted = derive_scenarios(base, data.fundamentals, config, data.industry)
    scenarios = {name: value_derived(scenario_inputs) for name, scenario_inputs in drafted.items()}

    price = data.fundamentals.price
    base_model = base.to_dcf()
    implied = reverse_dcf(base_model, price) if base_model is not None and price else None
    sensitivity = sensitivity_grid(base_model) if base_model is not None else pd.DataFrame()
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
        fallbacks=_merge_fallbacks(drafted),
        approvals_path=approvals_display(approvals_path),
    )


def write_outputs(report: ValuationReport, out_dir: str | Path) -> None:
    """assumptions.json、forecast.parquet、scenarios.parquet、sensitivity.parquet、report.md。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "assumptions.json").write_text(_assumptions_json(report), encoding="utf-8")
    forecast_table(report.scenarios).to_parquet(out / "forecast.parquet", index=False)
    _scenarios_frame(report).to_parquet(out / "scenarios.parquet", index=False)
    _sensitivity_frame(report.sensitivity).to_parquet(out / "sensitivity.parquet")
    (out / "report.md").write_text(render_report(report), encoding="utf-8")


def forecast_table(scenarios: dict) -> pd.DataFrame:
    """熊、基准、牛三条预测叠成一张表。没有正式结果的情景不写进行列，避免画成正常曲线。"""
    frames: list[pd.DataFrame] = []
    for name in ("bear", "base", "bull"):
        result = _as_result(scenarios.get(name))
        if result is None:
            continue
        frame = result.projection.copy()
        frame.insert(0, "scenario", name)
        frames.append(frame)
    columns = ["scenario", "year", "growth", "revenue", "margin", "net_income", "fcfe"]
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def render_report(report: ValuationReport) -> str:
    fundamentals = report.fundamentals
    lines = [
        f"# DCF 估值 {security_label(fundamentals.name, report.ts_code)}（asof {report.asof:%Y-%m-%d}）",
        "",
        f"- 行业：{fundamentals.industry or '未知'}　模板：{report.template}",
        f"- 现价：{_fmt(fundamentals.price)} 元　总市值：{_fmt_yi(fundamentals.total_mv)}",
        (
            f"- TTM：收入 {_fmt_yi(fundamentals.revenue_ttm)}，归母净利 {_fmt_yi(fundamentals.net_income_ttm)}，"
            f"FCF {_fmt_yi(fundamentals.fcf_ttm)}"
        ),
        (
            f"- 历史：5 年收入 CAGR {_fmt_pct(fundamentals.revenue_cagr_5y)}，"
            f"TTM 净利率 {_fmt_pct(fundamentals.margin_ttm)}，"
            f"近 5 年合计净利率 {_fmt_pct(fundamentals.margin_mean_5y)}，"
            f"现金转换率中位数 {_fmt_pct(fundamentals.fcf_conversion)}"
        ),
        (
            f"- 折现率：{report.discount_rate:.2%}（β={report.beta:.2f}，rf={report.config.risk_free:.2%}，"
            f"ERP={report.config.equity_risk_premium:.1%}）。"
            "无风险利率、股权风险溢价和 6%–15% 夹取是宏观预设。"
        ),
        (
            f"- 股权再投资率 {_fmt_pct(fundamentals.equity_reinvestment_rate)}，"
            f"配对 ROE {_fmt_pct(fundamentals.roe_for_growth)}，"
            f"历史 ROE 中位数 {_fmt_pct(fundamentals.roe_median)}。"
            "起始增速 = 股权再投资率 × ROE。"
        ),
        "",
        "熊市、牛市的折现率点差（+1.5 / −1 个百分点）和永续增速点差是情景设计，不是财报估计。",
        f"审核文件：`{report.approvals_path}`。没有对应条目时，兜底不进入估值。",
        "",
    ]
    lines.extend(_fallback_section(report))
    lines.extend(_scenario_section(report))
    if report.scenarios["base"].per_share is None:
        lines += [
            "## 正式结果",
            "",
            "基准每股价值无法计算。主路径仍有待审输入，没有把空价值当成完整的估值曲线，也没有做反向 DCF 和敏感性。",
            "",
        ]
        return "\n".join(lines)

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
    if report.sensitivity.empty:
        lines.append("敏感性没有算出。")
    else:
        header = "| 折现率＼起始增速 | " + " | ".join(f"{column:.1%}" for column in report.sensitivity.columns) + " |"
        lines.append(header)
        lines.append("|" + " --- |" * (len(report.sensitivity.columns) + 1))
        for rate, row in report.sensitivity.iterrows():
            cells = " | ".join(f"{value:.2f}" for value in row)
            lines.append(f"| {rate:.1%} | {cells} |")
    base_result = report.scenarios["base"].result
    lines += ["", f"终值占股权价值 {base_result.terminal_weight:.0%}（基准）。", ""]
    if base_result.terminal_warning:
        lines.append(f"> ⚠ {base_result.terminal_warning}。")
        lines.append("")
    lines.append("完整预测见 forecast.parquet，假设见 assumptions.json。")
    lines.append("")
    return "\n".join(lines)


def _fallback_section(report: ValuationReport) -> list[str]:
    if not report.fallbacks:
        return ["本次没有待审兜底。公司级输入都来自该公司财报。", ""]
    lines = ["## 无法计算与兜底", ""]
    approved = [item for item in report.fallbacks if item.status == STATUS_APPROVED]
    if report.pending:
        lines.append("下列输入仍是待审。审核通过之前，它们不进入估值数字。")
        lines.append("")
    if approved:
        lines.append("下列输入使用了已审核兜底。")
        lines.append("")
    lines += [
        "| 字段 | 状态 | 财报算出的数 | 建议兜底 | 原因 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report.fallbacks:
        status = "已审核兜底" if item.status == STATUS_APPROVED else "待审"
        computed = format_quantity(item.field, item.computed)
        if item.status == STATUS_APPROVED:
            suggested = f"批准值 {format_quantity(item.field, item.approved_value)}"
        else:
            suggested = format_quantity(item.field, item.proposed) if item.proposed is not None else "无"
        lines.append(
            f"| {field_label(item.field)} | {status} | {computed} | {suggested} | {item.reason} |"
        )
    lines.append("")
    return lines


def _scenario_section(report: ValuationReport) -> list[str]:
    lines = [
        "## 三情景",
        "",
        "| 情景 | 平均增速 | 净利率路径 | 转换率 | 折现率 | 永续增速 | 稳定现金转换 | 每股价值 | 相对现价 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    price = report.fundamentals.price
    for name in ("bear", "base", "bull"):
        outcome = report.scenarios[name]
        inputs = outcome.inputs
        per_share = outcome.per_share
        upside = per_share / price - 1.0 if per_share is not None and price else None
        lines.append(
            f"| {SCENARIO_NAMES[name]} | {_fmt_pct(_average(inputs))} | "
            f"{_fmt_pct(inputs.margin_start)}→{_fmt_pct(inputs.margin_terminal)} | "
            f"{_fmt_pct(inputs.cash_conversion)} | {inputs.discount_rate:.1%} | "
            f"{inputs.perpetuity_growth:.1%} | {_fmt_pct(inputs.stable_cash_conversion)} | "
            f"{_fmt_per_share(per_share)} | {_fmt_pct(upside)} |"
        )
    note = _approved_note(report)
    if note:
        lines += ["", note]
    lines.append("")
    return lines


def _scenarios_frame(report: ValuationReport) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    price = report.fundamentals.price
    for name, outcome in report.scenarios.items():
        inputs = outcome.inputs
        result = outcome.result
        per_share = outcome.per_share
        rows.append(
            {
                "scenario": name,
                "growth_start": inputs.growth_start,
                "average_growth": _average(inputs),
                "growth_terminal": inputs.growth_terminal,
                "margin_start": inputs.margin_start,
                "margin_terminal": inputs.margin_terminal,
                "cash_conversion": inputs.cash_conversion,
                "discount_rate": inputs.discount_rate,
                "terminal_method": inputs.terminal_method,
                "perpetuity_growth": inputs.perpetuity_growth,
                "stable_roe": inputs.stable_roe,
                "stable_cash_conversion": inputs.stable_cash_conversion,
                "exit_multiple": inputs.exit_multiple,
                "exit_basis": inputs.exit_basis,
                "per_share": per_share,
                "equity_value": None if result is None else result.equity_value,
                "pv_explicit": None if result is None else result.pv_explicit,
                "pv_terminal": None if result is None else result.pv_terminal,
                "terminal_weight": None if result is None else result.terminal_weight,
                "terminal_warning": None if result is None else result.terminal_warning,
                "upside": per_share / price - 1.0 if per_share is not None and price else None,
            }
        )
    return pd.DataFrame(rows)


def _assumptions_json(report: ValuationReport) -> str:
    fundamentals = report.fundamentals
    payload = {
        "name": fundamentals.name,
        "ts_code": report.ts_code,
        "asof": report.asof.strftime("%Y%m%d"),
        "template": report.template,
        "status": "incomplete" if report.scenarios["base"].per_share is None else "complete",
        "approvals_path": report.approvals_path,
        "formulas": {
            "growth_start": "股权再投资率 × ROE",
            "equity_reinvestment_rate": "（净资本开支 + 运营资本变动 − 净债务发行）/ 归母净利",
            "roe": "归母净利 / 平均归属母公司股东权益",
            "margin_start": "TTM 归母净利 / TTM 收入",
            "margin_terminal": "min(TTM 净利率, 近 5 年合计净利率)",
            "cash_conversion": "近 5 个盈利年度 (经营现金流 − 资本开支) / 归母净利 的中位数",
            "stable_roe": "历史年度 ROE 的中位数",
            "stable_cash_conversion": "1 − 永续增速 / ROE_stable，再夹到 [0, 1]",
            "discount_rate": "rf + β × ERP，再夹到 6%–15%",
            "perpetuity_growth": "2.5%，且低于折现率 1 个百分点",
        },
        "fundamentals": {
            "name": fundamentals.name,
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
            "equity_reinvestment_rate": fundamentals.equity_reinvestment_rate,
            "reinvestment_reason": fundamentals.reinvestment_reason,
            "roe_for_growth": fundamentals.roe_for_growth,
            "roe_median": fundamentals.roe_median,
            "roe_reason": fundamentals.roe_reason,
            "revenue_growth_low": fundamentals.revenue_growth_low,
            "revenue_growth_high": fundamentals.revenue_growth_high,
            "revenue_growth_samples": fundamentals.revenue_growth_samples,
            "margin_low": fundamentals.margin_low,
            "margin_high": fundamentals.margin_high,
            "margin_samples": fundamentals.margin_samples,
        },
        "fallbacks": [item.to_dict() for item in report.fallbacks],
        "discount": {
            "beta": report.beta,
            "risk_free": report.config.risk_free,
            "equity_risk_premium": report.config.equity_risk_premium,
            "cost_of_equity": report.discount_rate,
        },
        "scenarios": {
            name: _scenario_payload(outcome)
            for name, outcome in report.scenarios.items()
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


def _scenario_payload(outcome: ScenarioOutcome) -> dict[str, object]:
    inputs = outcome.inputs
    result = outcome.result
    return {
        "growth_start": inputs.growth_start,
        "average_growth": _average(inputs),
        "growth_terminal": inputs.growth_terminal,
        "margin_start": inputs.margin_start,
        "margin_terminal": inputs.margin_terminal,
        "cash_conversion": inputs.cash_conversion,
        "discount_rate": inputs.discount_rate,
        "terminal_method": inputs.terminal_method,
        "perpetuity_growth": inputs.perpetuity_growth,
        "stable_roe": inputs.stable_roe,
        "stable_cash_conversion": inputs.stable_cash_conversion,
        "exit_multiple": inputs.exit_multiple,
        "exit_basis": inputs.exit_basis,
        "per_share": outcome.per_share,
        "terminal_weight": None if result is None else result.terminal_weight,
        "terminal_warning": None if result is None else result.terminal_warning,
        "approved_fallback": any(item.status == STATUS_APPROVED for item in inputs.fallbacks),
    }


def _merge_fallbacks(scenarios: dict[str, DerivedInputs]) -> tuple[FallbackItem, ...]:
    seen: set[tuple[str, str, str]] = set()
    items: list[FallbackItem] = []
    for name in ("base", "bear", "bull"):
        for item in scenarios[name].fallbacks:
            key = (item.field, item.status, item.reason)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
    return tuple(items)


def _as_result(item: object) -> DcfResult | None:
    if isinstance(item, DcfResult):
        return item
    if isinstance(item, ScenarioOutcome):
        return item.result
    return None


def _average(inputs: DerivedInputs) -> float | None:
    if inputs.growth_start is None:
        return None
    path = growth_path(inputs.growth_start, inputs.growth_terminal, inputs.forecast_years)
    return float(sum(path) / len(path))


def _sensitivity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["discount_rate"])
    return frame


def _approved_note(report: ValuationReport) -> str:
    approved = [item for item in report.fallbacks if item.status == STATUS_APPROVED]
    if not approved:
        return ""
    names = "、".join(field_label(item.field) for item in approved)
    return f"已审核兜底：{names}。这些数来自审核文件，不是模型直接算出的原值。"


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "缺失"


def _fmt_pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "无法计算"


def _fmt_per_share(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "无法计算"


def _fmt_yi(value: float | None) -> str:
    return f"{value / 1e8:.1f} 亿" if value is not None else "缺失"
