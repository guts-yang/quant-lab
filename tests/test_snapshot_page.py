"""网站只读取快照，不导入券商数据包。"""

import builtins
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

from core.cleaner import REASON_ORDER
from strategy.publish import REJECTED_COLUMNS, SNAPSHOT_COLUMNS, write_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "web" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"quantlab_web_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_reason_labels_cover_every_cleaner_reason():
    labels = _load("labels")
    missing = [reason for reason in REASON_ORDER if reason not in labels.REASON_LABELS]
    assert missing == []


def test_score_and_missing_factor_use_plain_language():
    labels = _load("labels")
    assert labels.format_score(0.4) == "40"
    assert labels.format_factor("ep_ttm", 0.08) == "8.0%"
    assert labels.format_factor("earnings_gap", None) == "数据不足"
    assert labels.factor_note("gross_margin_var") == "越低越好"
    assert labels.reason_text("st,leverage") == "名称含 ST、资产负债率超过 80%"


def test_loader_reads_snapshot_without_broker_sdk(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "sxsc_tushare":
            raise AssertionError("加载快照不应导入 sxsc_tushare")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    _write_example(tmp_path / "snapshots" / "20240630", sample=True)
    _write_example(tmp_path / "snapshots" / "20241231", sample=False)
    load = _load("load")
    visible = load.visible_snapshots(tmp_path / "snapshots")
    assert [item.asof for item in visible] == ["20241231"]
    loaded = load.read_snapshot(tmp_path / "snapshots" / "20240630")
    assert loaded.sample
    assert loaded.holdings.iloc[0]["name"] == "示例甲"
    outside = load.outside_holdings(loaded)
    assert list(outside["ts_code"]) == ["110006.SZ"]
    found = load.filter_rows(loaded.rejected, "st示例")
    assert list(found["ts_code"]) == ["110008.SZ"]


def test_committed_example_is_marked_as_sample():
    summary = json.loads((ROOT / "web" / "snapshots" / "20240630" / "summary.json").read_text(encoding="utf-8"))
    assert summary["sample"] is True


def _write_example(directory: Path, sample: bool) -> None:
    holdings = _scores().head(5)
    write_snapshot(
        directory,
        {
            "asof": directory.name,
            "kept": 6,
            "rejected": 2,
            "reasons": {"st": 1, "listed_lt_180": 1},
            "top_n": 5,
            "weights": {"quality": 0.4, "value": 0.3, "growth": 0.3},
            "sample": sample,
        },
        holdings,
        _scores(),
        pd.DataFrame(
            {
                "ts_code": ["110008.SZ", "110009.SZ"],
                "name": ["*ST示例", "次新示例"],
                "reasons": ["st,leverage", "listed_lt_180"],
            }
        )[REJECTED_COLUMNS],
    )


def _scores() -> pd.DataFrame:
    rows = []
    composites = [0.91, 0.84, 0.76, 0.71, 0.66, 0.42]
    for index, composite in enumerate(composites, start=1):
        row = {column: pd.NA for column in SNAPSHOT_COLUMNS}
        row.update(
            {
                "ts_code": f"11000{index}.SZ",
                "name": ["示例甲", "示例乙", "示例丙", "示例丁", "示例戊", "示例己"][index - 1],
                "industry": "食品",
                "quality_score": composite,
                "value_score": composite - 0.05,
                "growth_score": composite - 0.08,
                "composite": composite,
                "ep_ttm": 0.08,
                "bp": 0.55,
                "dividend_yield": 0.031,
                "roe_ttm": 0.16,
                "roic": 14.2,
                "gross_margin_var": 0.8,
                "q_profit_yoy": 0.22,
                "q_sales_yoy": 11.0,
                "earnings_gap": None if index == 3 else 0.031,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)[SNAPSHOT_COLUMNS]
