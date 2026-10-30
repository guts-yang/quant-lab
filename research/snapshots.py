"""冻结每个月末当时可见的候选池，供后续诊断和回测复用。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from research.config import ResearchConfig


SnapshotBuilder = Callable[[object, pd.Timestamp, int], tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]]
logger = logging.getLogger(__name__)


def month_end_dates(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """返回区间内、实际有行情的自然月最后一个交易日。"""
    if bars is None or bars.empty:
        return []
    dates = pd.to_datetime(bars["trade_date"], errors="coerce").dropna().dt.normalize()
    dates = dates[(dates >= pd.Timestamp(start).normalize()) & (dates <= pd.Timestamp(end).normalize())]
    frame = pd.DataFrame({"trade_date": dates.drop_duplicates()})
    if frame.empty:
        return []
    frame["month"] = frame["trade_date"].dt.to_period("M")
    return list(frame.groupby("month", sort=True)["trade_date"].max())


def freeze_snapshots(
    provider: object,
    config: ResearchConfig,
    root: str | Path,
    *,
    builder: SnapshotBuilder | None = None,
) -> pd.DataFrame:
    """写入每月完整得分、持仓和拒绝名单，返回可供诊断的快照索引。"""
    from strategy.publish import build_frames

    build = builder or (lambda source, asof, top: build_frames(source, asof, top_n=top))
    root = Path(root)
    bars = provider.get_ohlc(config.start, config.end)
    dates = month_end_dates(bars, config.start, config.end)
    records: list[dict[str, object]] = []
    for asof in dates:
        logger.info("冻结截面 %s", f"{asof:%Y%m%d}")
        directory = root / "snapshots" / f"{asof:%Y%m%d}"
        if all((directory / name).exists() for name in ("holdings.parquet", "scores.parquet", "rejected.parquet", "metadata.json")):
            holdings = pd.read_parquet(directory / "holdings.parquet")
            scores = pd.read_parquet(directory / "scores.parquet")
            rejected = pd.read_parquet(directory / "rejected.parquet")
            records.append(
                {
                    "asof": asof,
                    "directory": str(directory),
                    "candidates": int(len(scores)),
                    "holdings": int(len(holdings)),
                    "rejected": int(len(rejected)),
                }
            )
            continue
        directory.mkdir(parents=True, exist_ok=True)
        holdings, scores, rejected, summary = build(provider, asof, config.scenario.top_n)
        holdings.to_parquet(directory / "holdings.parquet", index=False)
        scores.to_parquet(directory / "scores.parquet", index=False)
        rejected.to_parquet(directory / "rejected.parquet", index=False)
        metadata = {
            "asof": f"{asof:%Y%m%d}",
            "config": config.as_dict(),
            "summary": summary,
            "price_return_only": True,
        }
        (directory / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records.append(
            {
                "asof": asof,
                "directory": str(directory),
                "candidates": int(len(scores)),
                "holdings": int(len(holdings)),
                "rejected": int(len(rejected)),
            }
        )
    index = pd.DataFrame(records, columns=["asof", "directory", "candidates", "holdings", "rejected"])
    root.mkdir(parents=True, exist_ok=True)
    index.to_parquet(root / "snapshot_index.parquet", index=False)
    (root / "research_config.json").write_text(json.dumps(config.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return index


def load_snapshots(root: str | Path) -> dict[pd.Timestamp, pd.DataFrame]:
    """读取冻结的完整候选池，避免诊断时重新计算或覆盖历史。"""
    index_path = Path(root) / "snapshot_index.parquet"
    if not index_path.exists():
        return {}
    index = pd.read_parquet(index_path)
    result: dict[pd.Timestamp, pd.DataFrame] = {}
    for row in index.itertuples(index=False):
        asof = pd.Timestamp(row.asof).normalize()
        result[asof] = pd.read_parquet(Path(row.directory) / "scores.parquet")
    return result
