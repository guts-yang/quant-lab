"""CLI：python -m valuation.run --code 600519.SH --asof YYYYMMDD [--rf 0.018] [--estimates file.json]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from core.tushare_provider import TushareProvider
from valuation.assumptions import FileEstimatesProvider, ValuationConfig
from valuation.fallbacks import field_label, format_quantity
from valuation.scenario import run_valuation, security_label, write_outputs

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="AlphaSpread 式 DCF 三情景估值（A 股）")
    parser.add_argument("--code", required=True, help="证券代码，如 600519.SH")
    parser.add_argument("--asof", required=True, help="截面日 YYYYMMDD，只用该日前已公告数据")
    parser.add_argument("--rf", type=float, default=ValuationConfig.risk_free, help="无风险利率，默认 %(default)s")
    parser.add_argument("--erp", type=float, default=ValuationConfig.equity_risk_premium, help="股权风险溢价，默认 %(default)s")
    parser.add_argument("--years", type=int, default=ValuationConfig.forecast_years, help="预测年数，默认 %(default)s")
    parser.add_argument("--estimates", default=None, help="分析师一致预期 JSON（可选）")
    parser.add_argument("--out", default=None, help="产物目录，默认 data/research/valuation_<code>")
    parser.add_argument("--history", action="store_true", help="额外写出三年股价与基准内在价值 history.parquet")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asof = pd.Timestamp(args.asof)
    config = ValuationConfig(forecast_years=args.years, risk_free=args.rf, equity_risk_premium=args.erp)
    estimates = FileEstimatesProvider(args.estimates) if args.estimates else None

    provider = TushareProvider()
    report = run_valuation(provider, args.code, asof, config, estimates)
    out_dir = Path(args.out) if args.out else Path("data/research") / f"valuation_{args.code.replace('.', '_')}"
    write_outputs(report, out_dir)
    if args.history:
        from valuation.history import build_history

        history = build_history(provider, args.code, asof, config, estimates)
        history.to_parquet(out_dir / "history.parquet", index=False)
        print(f"股价与内在价值 {len(history)} 个交易日，已写入 {out_dir / 'history.parquet'}")

    print(security_label(report.fundamentals.name, report.ts_code))
    print(f"行业 {report.fundamentals.industry}，模板 {report.template}，β {report.beta:.2f}，折现率 {report.discount_rate:.2%}")
    for name in ("bear", "base", "bull"):
        value = report.scenarios[name].per_share
        shown = "无法计算" if value is None else f"{value:.2f} 元/股"
        print(f"  {name}: {shown}")
    if report.pending:
        print("无法计算，未产出正式基准每股价值。待审兜底在审核通过前不进入估值：")
        for item in report.pending:
            print(
                f"- {field_label(item.field)}：{item.reason}"
                f" 财报算出的数 {format_quantity(item.field, item.computed)}；"
                f"建议兜底 {format_quantity(item.field, item.proposed)}。"
            )
        print(f"审核文件 {report.approvals_path}")
        print(f"产物已写入 {out_dir}")
        return 1
    if report.implied is not None:
        print(f"现价隐含平均增速 {report.implied.average_growth:.1%}")
    approved = [item for item in report.fallbacks if item.status == "approved"]
    if approved:
        print("已审核兜底：" + "、".join(field_label(item.field) for item in approved))
    base = report.scenarios["base"].per_share
    print(f"基准情景每股价值 {base:.2f} 元，产物已写入 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
