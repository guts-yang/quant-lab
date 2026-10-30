"""一键生成冻结快照、因子诊断、样本外回测和压力测试报告。"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd

from research.config import ResearchConfig, make_config
from research.diagnostics import ic_summary, quantile_returns, rank_ic, forward_labels
from research.snapshots import freeze_snapshots, load_snapshots
from research.stress import run_stress
from research.walkforward import persist_walkforward, run_walkforward


def run_research(provider: object, config: ResearchConfig, root: str | Path) -> dict[str, pd.DataFrame | str]:
    """运行一次不可覆盖的研究。调用方负责选择独立的 run_id 目录。"""
    root = Path(root)
    if (root / "report.md").exists():
        raise FileExistsError(f"研究报告已存在：{root / 'report.md'}")
    root.mkdir(parents=True, exist_ok=True)
    snapshot_index = freeze_snapshots(provider, config, root)
    snapshots = load_snapshots(root)
    warmup = config.start - pd.Timedelta(days=120)
    bars = provider.get_ohlc(warmup, config.end)
    index_bars = provider.get_index_daily(config.benchmark_code, warmup, config.end)

    labels = forward_labels(snapshots, bars, config.holding_sessions)
    ic = rank_ic(labels)
    ic_stats = ic_summary(ic)
    quantiles = quantile_returns(labels, quantiles=config.quantiles)
    diagnostics = root / "diagnostics"
    diagnostics.mkdir(exist_ok=True)
    labels.to_parquet(diagnostics / "forward_labels.parquet", index=False)
    ic.to_parquet(diagnostics / "rank_ic.parquet", index=False)
    ic_stats.to_parquet(diagnostics / "rank_ic_summary.parquet", index=False)
    quantiles.to_parquet(diagnostics / "quantile_returns.parquet", index=False)

    walkforward = run_walkforward(bars, index_bars, snapshots, config)
    walk_summary = persist_walkforward(walkforward, root)
    stress = run_stress(bars, index_bars, snapshots, config)
    stress.to_parquet(root / "stress.parquet", index=False)
    decision, reasons = decision_gate(config, walk_summary, stress)
    summary = {
        "config": config.as_dict(),
        "decision": decision,
        "reasons": reasons,
        "price_return_only": True,
        "known_gaps": [
            "基准和标签使用收盘价价格收益，未计入复权、分红和公司行动。",
            "日线回测未建模涨跌停、停牌排队和市场冲击。",
            "行业字段当前没有历史时点版本，行业切片只用于探索。",
        ],
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = render_report(summary, snapshot_index, ic_stats, walk_summary, stress)
    (root / "report.md").write_text(report, encoding="utf-8")
    return {
        "snapshot_index": snapshot_index,
        "rank_ic_summary": ic_stats,
        "walkforward_summary": walk_summary,
        "stress": stress,
        "decision": decision,
    }


def decision_gate(config: ResearchConfig, walk_summary: pd.DataFrame, stress: pd.DataFrame) -> tuple[str, list[str]]:
    """只给出继续验证、拒绝或证据不足；不把一次回测当作实盘批准。"""
    holdout = walk_summary.loc[walk_summary["period"].eq("holdout")]
    if holdout.empty:
        return "evidence_insufficient", ["没有锁定样本外净值。"]
    row = holdout.iloc[0]
    months = max(
        (config.holdout.end.year - config.holdout.start.year) * 12
        + config.holdout.end.month
        - config.holdout.start.month
        + 1,
        0,
    )
    reasons: list[str] = []
    if months < config.min_holdout_months:
        reasons.append(f"锁定样本外仅 {months} 个月，少于门槛 {config.min_holdout_months} 个月。")
    if pd.isna(row["strategy_calmar"]) or row["strategy_calmar"] < config.min_holdout_calmar:
        reasons.append("锁定样本外卡玛未达到预先设定门槛。")
    if pd.isna(row["strategy_max_drawdown"]) or row["strategy_max_drawdown"] < config.max_holdout_drawdown:
        reasons.append("锁定样本外最大回撤超过预先设定门槛。")
    baseline = stress.loc[stress["scenario"].eq("baseline")]
    if baseline.empty or float(baseline.iloc[0]["estimated_cost_to_average_equity"]) > config.max_cost_to_average_equity:
        reasons.append("基线成本占平均资产比例超过预先设定门槛，或成本结果缺失。")
    if reasons:
        return ("evidence_insufficient" if months < config.min_holdout_months else "reject_current_definition"), reasons
    return "continue_validation", ["数据点时、成本和锁定样本外门槛均通过；仍需复权和成交可行性验证。"]


def render_report(
    summary: dict[str, object],
    snapshots: pd.DataFrame,
    ic: pd.DataFrame,
    walkforward: pd.DataFrame,
    stress: pd.DataFrame,
) -> str:
    decision = str(summary["decision"])
    reasons = "\n".join(f"- {reason}" for reason in summary["reasons"])
    return f"""# 低换手基本面多因子研究报告

## 结论

`{decision}`

{reasons}

## 研究规格

```json
{json.dumps(summary["config"], ensure_ascii=False, indent=2)}
```

## 数据与快照

- 冻结月末快照数：{len(snapshots)}
- 价格收益口径：是。基准与标签未计入复权、分红和公司行动。

## Rank IC 汇总

{_frame_text(ic, "没有足够的未来价格生成 Rank IC。")}

## Walk-forward 汇总

{_frame_text(walkforward, "没有回测结果。")}

## 小账户压力测试

{_frame_text(stress, "没有压力测试结果。")}

## 已知缺口

""" + "\n".join(f"- {item}" for item in summary["known_gaps"]) + "\n"


def _frame_text(frame: pd.DataFrame, empty_text: str) -> str:
    """CSV 代码块不依赖可选的 tabulate 包。"""
    if frame.empty:
        return empty_text
    return "```csv\n" + frame.to_csv(index=False) + "```"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="生成低换手基本面多因子的研究决策报告")
    parser.add_argument("--start", required=True, help="开发期开始，YYYYMMDD")
    parser.add_argument("--development-end", required=True, help="开发期结束，YYYYMMDD")
    parser.add_argument("--validation-end", required=True, help="验证期结束，YYYYMMDD")
    parser.add_argument("--end", required=True, help="锁定样本外结束，YYYYMMDD")
    parser.add_argument("--out", default="data/research", help="研究结果根目录")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d_%H%M%S"), help="不可覆盖的运行编号")
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from core.tushare_provider import TushareProvider

    config = make_config(args.start, args.development_end, args.validation_end, args.end)
    root = Path(args.out) / args.run_id
    result = run_research(TushareProvider(), config, root)
    print(f"research={root} decision={result['decision']}")


if __name__ == "__main__":
    main()
