"""CLI：python -m valuation.run --code 600519.SH --asof YYYYMMDD [--rf 0.018] [--estimates file.json]"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from core.tushare_provider import TushareProvider
from valuation.assumptions import FileEstimatesProvider, ValuationConfig
from valuation.scenario import run_valuation, security_label, write_outputs

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="AlphaSpread 式 DCF 三情景估值（A 股）")
    parser.add_argument("--code", required=True, help="证券代码，如 600519.SH")
    parser.add_argument("--asof", required=True, help="截面日 YYYYMMDD，只用该日前已公告数据")
    parser.add_argument("--rf", type=float, default=ValuationConfig.risk_free, help="无风险利率，默认 %(default)s")
    parser.add_argument("--erp", type=float, default=ValuationConfig.equity_risk_premium, help="股权风险溢价，默认 %(default)s")
    parser.add_argument("--years", type=int, default=ValuationConfig.forecast_years, help="预测年数，默认 %(default)s")
    parser.add_argument("--estimates", default=None, help="分析师一致预期 JSON（可选）")
    parser.add_argument("--out", default=None, help="产物目录，默认 data/research/valuation_<code>")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asof = pd.Timestamp(args.asof)
    config = ValuationConfig(forecast_years=args.years, risk_free=args.rf, equity_risk_premium=args.erp)
    estimates = FileEstimatesProvider(args.estimates) if args.estimates else None

    provider = TushareProvider()
    report = run_valuation(provider, args.code, asof, config, estimates)
    out_dir = Path(args.out) if args.out else Path("data/research") / f"valuation_{args.code.replace('.', '_')}"
    write_outputs(report, out_dir)

    base = report.scenarios["base"]
    print(security_label(report.fundamentals.name, report.ts_code))
    print(f"行业 {report.fundamentals.industry}，模板 {report.template}，β {report.beta:.2f}，折现率 {report.discount_rate:.2%}")
    for name in ("bear", "base", "bull"):
        print(f"  {name}: {report.scenarios[name].per_share:.2f} 元/股")
    if report.implied is not None:
        print(f"现价隐含平均增速 {report.implied.average_growth:.1%}")
    print(f"基准情景每股价值 {base.per_share:.2f} 元，产物已写入 {out_dir}")


if __name__ == "__main__":
    main()
