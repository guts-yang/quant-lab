"""读取网站快照。这里不连接数据源。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

SNAPSHOT_FILES = ("summary.json", "holdings.parquet", "scores.parquet", "rejected.parquet")


@dataclass(frozen=True)
class Snapshot:
    asof: str
    summary: dict
    holdings: pd.DataFrame
    scores: pd.DataFrame
    rejected: pd.DataFrame

    @property
    def sample(self) -> bool:
        return bool(self.summary.get("sample"))

    @property
    def label(self) -> str:
        text = f"{self.asof[:4]}-{self.asof[4:6]}-{self.asof[6:8]}"
        if self.sample:
            return f"{text}（示例）"
        return text


def snapshot_root() -> Path:
    return Path(__file__).resolve().parent / "snapshots"


def backtest_root() -> Path:
    return Path(__file__).resolve().parent / "backtests"


def list_snapshots(root: str | Path | None = None) -> list[Snapshot]:
    base = Path(root) if root is not None else snapshot_root()
    if not base.exists():
        return []
    found: list[Snapshot] = []
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        if not all((directory / name).exists() for name in SNAPSHOT_FILES):
            continue
        found.append(read_snapshot(directory))
    return found


def visible_snapshots(root: str | Path | None = None) -> list[Snapshot]:
    """有真实截面时不再把示例放进日期列表。"""
    snapshots = list_snapshots(root)
    real = [item for item in snapshots if not item.sample]
    return real or snapshots


def read_snapshot(directory: str | Path) -> Snapshot:
    path = Path(directory)
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    asof = str(summary.get("asof") or path.name)
    return Snapshot(
        asof=asof,
        summary=summary,
        holdings=pd.read_parquet(path / "holdings.parquet"),
        scores=pd.read_parquet(path / "scores.parquet"),
        rejected=pd.read_parquet(path / "rejected.parquet"),
    )


def outside_holdings(snapshot: Snapshot) -> pd.DataFrame:
    """通过排雷、但没有进入前几名的股票。"""
    if snapshot.scores.empty:
        return snapshot.scores
    if snapshot.holdings.empty or "ts_code" not in snapshot.holdings.columns:
        return snapshot.scores
    chosen = set(snapshot.holdings["ts_code"].astype(str))
    scores = snapshot.scores.copy()
    scores["ts_code"] = scores["ts_code"].astype(str)
    return scores.loc[~scores["ts_code"].isin(chosen)].reset_index(drop=True)


@dataclass(frozen=True)
class BacktestView:
    name: str
    summary: dict
    equity: pd.DataFrame
    trades: pd.DataFrame

    @property
    def sample(self) -> bool:
        return bool(self.summary.get("sample"))

    @property
    def label(self) -> str:
        start = str(self.summary.get("start") or "")
        end = str(self.summary.get("end") or self.name)
        text = f"{start}–{end}" if start else self.name
        if self.sample:
            return f"{text}（示例）"
        return text


def list_backtests(root: str | Path | None = None) -> list[BacktestView]:
    base = Path(root) if root is not None else backtest_root()
    if not base.exists():
        return []
    found: list[BacktestView] = []
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        summary_path = directory / "summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        equity = pd.read_parquet(directory / "equity.parquet") if (directory / "equity.parquet").exists() else pd.DataFrame()
        trades = pd.read_parquet(directory / "trades.parquet") if (directory / "trades.parquet").exists() else pd.DataFrame()
        found.append(BacktestView(directory.name, summary, equity, trades))
    real = [item for item in found if not item.sample]
    return real or found


@dataclass(frozen=True)
class ResearchView:
    name: str
    summary: dict
    report: str
    rank_ic: pd.DataFrame
    stress: pd.DataFrame


@dataclass(frozen=True)
class ValuationView:
    name: str
    code: str
    asof: str
    report: str
    scenarios: pd.DataFrame
    sensitivity: pd.DataFrame
    forecast: pd.DataFrame
    assumptions: dict


@dataclass(frozen=True)
class ChanView:
    name: str
    summary: dict
    report: str
    periods: pd.DataFrame
    equity: pd.DataFrame


@dataclass(frozen=True)
class BookView:
    name: str
    payload: dict

    @property
    def label(self) -> str:
        asof = str(self.payload.get("asof") or self.name)
        if len(asof) == 8 and asof.isdigit():
            return f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}"
        return asof


def research_root() -> Path:
    return Path(__file__).resolve().parent / "research"


def valuation_root() -> Path:
    return Path(__file__).resolve().parent / "valuations"


def chan_root() -> Path:
    return Path(__file__).resolve().parent / "chan"


def book_root() -> Path:
    return Path(__file__).resolve().parent / "books"


def list_research(root: str | Path | None = None) -> list[ResearchView]:
    return [_read_research(path) for path in _directories(root, research_root)]


def list_valuations(root: str | Path | None = None) -> list[ValuationView]:
    return [_read_valuation(path) for path in _directories(root, valuation_root)]


def list_chan(root: str | Path | None = None) -> list[ChanView]:
    return [_read_chan(path) for path in _directories(root, chan_root)]


def list_books(root: str | Path | None = None) -> list[BookView]:
    base = Path(root) if root is not None else book_root()
    if not base.exists():
        return []
    found = []
    for path in sorted(base.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "ranked" in payload:
            found.append(BookView(path.stem, payload))
    return found


def _directories(root: str | Path | None, default) -> list[Path]:
    base = Path(root) if root is not None else default()
    if not base.exists():
        return []
    return sorted(path for path in base.iterdir() if path.is_dir())


def _read_research(path: Path) -> ResearchView:
    return ResearchView(
        name=path.name,
        summary=_json(path / "summary.json"),
        report=_text(path / "report.md"),
        rank_ic=_table(path / "diagnostics" / "rank_ic_summary.parquet"),
        stress=_table(path / "stress.parquet"),
    )


def _read_valuation(path: Path) -> ValuationView:
    assumptions = _json(path / "assumptions.json")
    code, _, asof = path.name.rpartition("_")
    return ValuationView(
        name=path.name,
        code=str(assumptions.get("ts_code") or code),
        asof=str(assumptions.get("asof") or asof),
        report=_text(path / "report.md"),
        scenarios=_table(path / "scenarios.parquet"),
        sensitivity=_table(path / "sensitivity.parquet"),
        forecast=_table(path / "forecast.parquet"),
        assumptions=assumptions,
    )


def _read_chan(path: Path) -> ChanView:
    periods = _table(path / "walkforward" / "summary.parquet")
    equity = pd.DataFrame()
    preferred = ["holdout", "validation", "development"]
    names = preferred + [name for name in _period_dirs(path) if name not in preferred]
    for name in names:
        candidate = path / "walkforward" / name / "strategy_equity.parquet"
        if candidate.exists():
            equity = _table(candidate)
            break
    return ChanView(path.name, _json(path / "summary.json"), _text(path / "report.md"), periods, equity)


def _period_dirs(path: Path) -> list[str]:
    walk = path / "walkforward"
    if not walk.exists():
        return []
    return sorted(item.name for item in walk.iterdir() if item.is_dir())


def _json(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def filter_rows(frame: pd.DataFrame, query: str) -> pd.DataFrame:
    text = query.strip().lower()
    if not text or frame is None or frame.empty:
        return frame
    matched = pd.Series(False, index=frame.index)
    if "ts_code" in frame.columns:
        matched = matched | frame["ts_code"].astype(str).str.lower().str.contains(text, regex=False)
    if "name" in frame.columns:
        names = frame["name"].fillna("").astype(str).str.lower()
        matched = matched | names.str.contains(text, regex=False)
    return frame.loc[matched].reset_index(drop=True)
