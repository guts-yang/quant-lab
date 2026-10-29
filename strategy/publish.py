"""把一次截面的排雷和打分写成网站可读的快照。"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from core.cleaner import REASON_ORDER
from strategy.scorer import (
    FACTOR_COLUMNS,
    GROWTH_WEIGHT,
    QUALITY_WEIGHT,
    VALUE_WEIGHT,
    assemble_factors,
    score_cross_section,
    select_holdings,
)

SCORE_COLUMNS = ["quality_score", "value_score", "growth_score", "composite"]
SNAPSHOT_COLUMNS = ["ts_code", "name", "industry", *SCORE_COLUMNS, *FACTOR_COLUMNS]
REJECTED_COLUMNS = ["ts_code", "name", "reasons"]


def default_snapshot_root() -> Path:
    return Path(__file__).resolve().parents[1] / "site" / "snapshots"


def publish(
    provider: object,
    asof: pd.Timestamp,
    top_n: int = 8,
    config: object | None = None,
    snapshot_root: str | Path | None = None,
    sample: bool = False,
) -> Path:
    """拉取一次截面并写入 YYYYMMDD 目录。已有同日文件会被覆盖。"""
    asof = pd.Timestamp(asof).normalize()
    holdings, scores, rejected, summary = build_frames(provider, asof, top_n=top_n, config=config)
    summary["sample"] = bool(sample)
    directory = Path(snapshot_root or default_snapshot_root()) / f"{asof:%Y%m%d}"
    write_snapshot(directory, summary, holdings, scores, rejected)
    return directory


def build_frames(
    provider: object,
    asof: pd.Timestamp,
    top_n: int = 8,
    config: object | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """返回持仓、通过排雷的全部得分、剔除名单和摘要。因子列保留原始口径。"""
    from core.provider import recent_period_ends
    from core.universe import build_universe

    asof = pd.Timestamp(asof).normalize()
    cleaned = build_universe(provider, asof, config)
    rejected = _rejected_frame(cleaned.rejected)
    if cleaned.kept.empty:
        scores = _empty_scores()
        holdings = _empty_scores()
        return holdings, scores, rejected, _summary(asof, 0, rejected, top_n)

    instruments = provider.get_instruments(asof)
    universe = cleaned.kept.copy()
    if instruments is not None and not instruments.empty and "industry" in instruments.columns:
        industry = instruments[["ts_code", "industry"]].copy()
        industry["ts_code"] = industry["ts_code"].astype(str)
        universe = universe.merge(industry.drop_duplicates("ts_code"), on="ts_code", how="left")

    periods = recent_period_ends(asof, n=12)
    codes = universe["ts_code"].astype(str).tolist()
    market = provider.get_daily_basic(asof)
    balance_sheets = provider.get_balance_sheets(periods)
    indicators = provider.get_factor_indicators(periods)
    dividends = provider.get_dividends(codes)
    prices = provider.get_daily_prices(asof - pd.Timedelta(days=160), asof)
    raw = assemble_factors(asof, universe, market, balance_sheets, indicators, dividends, prices)
    scored = score_cross_section(asof, universe, market, balance_sheets, indicators, dividends, prices)
    scores = _with_raw_factors(raw, scored, cleaned.kept)
    holdings = select_holdings(scores, top_n)
    return holdings, scores, rejected, _summary(asof, len(cleaned.kept), rejected, top_n)


def write_snapshot(
    directory: Path,
    summary: dict[str, object],
    holdings: pd.DataFrame,
    scores: pd.DataFrame,
    rejected: pd.DataFrame,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _frame(holdings, SNAPSHOT_COLUMNS).to_parquet(directory / "holdings.parquet", index=False)
    _frame(scores, SNAPSHOT_COLUMNS).to_parquet(directory / "scores.parquet", index=False)
    _frame(rejected, REJECTED_COLUMNS).to_parquet(directory / "rejected.parquet", index=False)


def _with_raw_factors(raw: pd.DataFrame, scored: pd.DataFrame, kept: pd.DataFrame) -> pd.DataFrame:
    names = kept[["ts_code", "name"]].copy()
    names["ts_code"] = names["ts_code"].astype(str)
    frame = raw.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    points = scored[["ts_code", *SCORE_COLUMNS]].copy()
    points["ts_code"] = points["ts_code"].astype(str)
    frame = frame.merge(points, on="ts_code", how="left")
    frame = frame.merge(names.drop_duplicates("ts_code"), on="ts_code", how="left")
    if "industry" not in frame.columns:
        frame["industry"] = pd.NA
    ordered = frame.sort_values(["composite", "ts_code"], ascending=[False, True], na_position="last")
    return _frame(ordered, SNAPSHOT_COLUMNS)


def _rejected_frame(rejected: pd.DataFrame | None) -> pd.DataFrame:
    if rejected is None or rejected.empty:
        return _frame(None, REJECTED_COLUMNS)
    return _frame(rejected, REJECTED_COLUMNS)


def _summary(asof: pd.Timestamp, kept: int, rejected: pd.DataFrame, top_n: int) -> dict[str, object]:
    counts = {reason: 0 for reason in REASON_ORDER}
    if not rejected.empty and "reasons" in rejected.columns:
        for reasons in rejected["reasons"]:
            for reason in str(reasons).split(","):
                if reason in counts:
                    counts[reason] += 1
    return {
        "asof": f"{pd.Timestamp(asof):%Y%m%d}",
        "kept": int(kept),
        "rejected": int(len(rejected)),
        "reasons": counts,
        "top_n": int(top_n),
        "weights": {"quality": QUALITY_WEIGHT, "value": VALUE_WEIGHT, "growth": GROWTH_WEIGHT},
    }


def _empty_scores() -> pd.DataFrame:
    return _frame(None, SNAPSHOT_COLUMNS)


def _frame(frame: pd.DataFrame | None, columns: list[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out.loc[:, columns].reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="把排雷和打分写成网站快照")
    parser.add_argument("--asof", required=True, help="截面日期，YYYYMMDD")
    parser.add_argument("--top", type=int, default=8, help="综合分最高的股票数，5 到 8")
    parser.add_argument("--out", default=None, help="快照根目录，默认 site/snapshots")
    args = parser.parse_args(list(argv) if argv is not None else None)

    from core.tushare_provider import TushareProvider

    directory = publish(TushareProvider(), args.asof, top_n=args.top, snapshot_root=args.out)
    print(directory)


if __name__ == "__main__":
    main()
