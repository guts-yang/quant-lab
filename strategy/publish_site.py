"""把已经算好的研究成品复制到网页目录。不访问网络，不读取券商 token。"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

SECRET_NAMES = {".env"}
SENSITIVE_KEYS = ("token", "password", "secret", "account")
RESEARCH_FILES = (
    "report.md",
    "summary.json",
    "diagnostics/rank_ic_summary.parquet",
    "walkforward/summary.parquet",
    "stress.parquet",
)
VALUATION_FILES = (
    "report.md",
    "scenarios.parquet",
    "sensitivity.parquet",
    "assumptions.json",
    "forecast.parquet",
)
CHAN_FILES = ("report.md", "summary.json", "walkforward/summary.parquet")


def default_web_root() -> Path:
    return Path(__file__).resolve().parents[1] / "web"


def publish_site(
    source: str | Path,
    web_root: str | Path | None = None,
    books: Sequence[str | Path] = (),
) -> list[Path]:
    """复制研究、估值、缠论和排名清单。源目录里出现 .env 时拒绝发布。"""
    root = Path(source)
    site = Path(web_root) if web_root is not None else default_web_root()
    _refuse_secrets([root, *[Path(path) for path in books]])
    copied: list[Path] = []
    if root.exists():
        for child in sorted(path for path in root.iterdir() if path.is_dir() and path.name != "cache"):
            kind = _kind(child)
            if kind == "valuation":
                copied.append(_copy_valuation(child, site / "valuations"))
            elif kind == "chan":
                copied.append(_copy_named(child, site / "chan" / child.name, CHAN_FILES))
                copied.extend(_copy_walkforward_backtests(child, site / "backtests"))
            elif kind == "research":
                copied.append(_copy_named(child, site / "research" / child.name, RESEARCH_FILES))
                copied.extend(_copy_walkforward_backtests(child, site / "backtests"))
    for book in books:
        copied.append(_copy_book(Path(book), site / "books"))
    return copied


def _kind(path: Path) -> str:
    if (path / "assumptions.json").exists() and (path / "scenarios.parquet").exists():
        return "valuation"
    summary = _read_json(path / "summary.json")
    gaps = " ".join(str(item) for item in summary.get("known_gaps") or [])
    if (path / "events.parquet").exists() or "笔" in gaps:
        return "chan"
    if (path / "report.md").exists() and (path / "diagnostics" / "rank_ic_summary.parquet").exists():
        return "research"
    return ""


def _copy_named(source: Path, destination: Path, relative_files: Sequence[str]) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in relative_files:
        origin = source / relative
        if not origin.exists():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.suffix == ".json":
            _write_json(target, _sanitize(_read_json(origin)))
        else:
            shutil.copy2(origin, target)
    for period in _period_names(source):
        for name in ("strategy_equity.parquet", "trades.parquet"):
            origin = source / "walkforward" / period / name
            if not origin.exists():
                continue
            target = destination / "walkforward" / period / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
    return destination


def _copy_valuation(source: Path, root: Path) -> Path:
    assumptions = _sanitize(_read_json(source / "assumptions.json"))
    code = str(assumptions.get("ts_code") or source.name.removeprefix("valuation_").replace("_", "."))
    asof = str(assumptions.get("asof") or "unknown")
    return _copy_named(source, root / f"{code}_{asof}", VALUATION_FILES)


def _copy_walkforward_backtests(source: Path, root: Path) -> list[Path]:
    summary_path = source / "walkforward" / "summary.parquet"
    if not summary_path.exists():
        return []
    summary = pd.read_parquet(summary_path)
    config = _read_json(source / "summary.json").get("config") or {}
    scenario = config.get("scenario") or {}
    capital = scenario.get("capital", config.get("capital", 40000))
    top_n = scenario.get("top_n", config.get("max_positions", 8))
    written: list[Path] = []
    for row in summary.itertuples(index=False):
        period = str(row.period)
        equity = source / "walkforward" / period / "strategy_equity.parquet"
        trades = source / "walkforward" / period / "trades.parquet"
        if not equity.exists() or not trades.exists():
            continue
        meta = config.get(period) or {}
        destination = root / f"{source.name}_{period}"
        destination.mkdir(parents=True, exist_ok=True)
        payload = {
            "sample": False,
            "start": str(meta.get("start") or ""),
            "end": str(meta.get("end") or ""),
            "capital": capital,
            "top_n": top_n,
            "cagr": _number(getattr(row, "strategy_cagr", None)),
            "max_drawdown": _number(getattr(row, "strategy_max_drawdown", None)),
            "sharpe": _number(getattr(row, "strategy_sharpe", None)),
            "calmar": _number(getattr(row, "strategy_calmar", None)),
            "note": f"价格收益，不含分红复权。来自 {source.name} 的{period}。",
        }
        _write_json(destination / "summary.json", payload)
        shutil.copy2(equity, destination / "equity.parquet")
        shutil.copy2(trades, destination / "trades.parquet")
        written.append(destination)
    return written


def _copy_book(source: Path, root: Path) -> Path:
    if source.name in SECRET_NAMES:
        raise RuntimeError("排名清单不能是 .env")
    payload = _sanitize(_read_json(source))
    asof = str(payload.get("asof") or source.stem)
    destination = root / f"{asof}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_json(destination, payload)
    return destination


def _period_names(source: Path) -> list[str]:
    walk = source / "walkforward"
    if not walk.exists():
        return []
    return sorted(path.name for path in walk.iterdir() if path.is_dir())


def _refuse_secrets(paths: Sequence[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else path.rglob("*")
        for candidate in candidates:
            if candidate.name in SECRET_NAMES:
                raise RuntimeError(f"发布范围包含密钥文件：{candidate}")


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in SENSITIVE_KEYS):
                continue
            cleaned[key] = _sanitize(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="把本机研究结果复制到网页目录")
    parser.add_argument("--from", dest="source", default="data/research", help="研究成品根目录")
    parser.add_argument("--web", default=None, help="网页根目录，默认 web/")
    parser.add_argument("--book", action="append", default=[], help="PTrade 排名清单 JSON，可重复")
    args = parser.parse_args(list(argv) if argv is not None else None)
    written = publish_site(args.source, args.web, args.book)
    for path in written:
        print(path)


if __name__ == "__main__":
    main()
