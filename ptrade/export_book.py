"""把研究侧月末排名写成 PTrade 盘前读取的 fundamental_book.json。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from strategy.risk_control import rank_scores


def export_book(provider: object, asof: pd.Timestamp, path: str | Path, top_n: int = 8) -> Path:
    """保存完整排名。PTrade 再用当前持仓决定前 15 名缓冲。"""
    from strategy.scorer import score_candidates

    asof = pd.Timestamp(asof).normalize()
    ranked = rank_scores(score_candidates(provider, asof))
    if not ranked.empty:
        ranked = ranked.head(max(top_n, 30))
    payload = {
        "asof": f"{asof:%Y%m%d}",
        "top_n": int(top_n),
        "ranked": [
            {"ts_code": str(row.ts_code), "rank": int(row.rank), "composite": None if pd.isna(row.composite) else float(row.composite)}
            for row in ranked.itertuples(index=False)
        ],
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="导出 PTrade 盘前读取的排名文件")
    parser.add_argument("--asof", required=True, help="截面日期，YYYYMMDD")
    parser.add_argument("--top", type=int, default=8, help="目标新开仓数量，5 到 8")
    parser.add_argument("--out", default="fundamental_book.json", help="输出路径")
    args = parser.parse_args(list(argv) if argv is not None else None)
    from core.tushare_provider import TushareProvider

    path = export_book(TushareProvider(), args.asof, args.out, top_n=args.top)
    print(path)


if __name__ == "__main__":
    main()
