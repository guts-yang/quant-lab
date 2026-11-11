"""网页成品只从本地结果复制，不读取密钥。"""

import json
from pathlib import Path

import pandas as pd
import pytest

from backtest.engine import BacktestResult, write_backtest
from strategy.publish_site import publish_site

ROOT = Path(__file__).resolve().parents[1]


def test_backtest_bundle_is_readable_by_the_site(tmp_path):
    result = BacktestResult(
        pd.DataFrame({"trade_date": [pd.Timestamp("2024-01-31")], "equity": [40000.0]}),
        pd.DataFrame(columns=["trade_date", "ts_code", "side", "shares", "price", "reason"]),
        {"cagr": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": float("nan")},
    )
    destination = write_backtest(result, "20240101", "20240131", tmp_path / "run", capital=40000, top_n=8)
    summary = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert summary["start"] == "20240101"
    assert summary["calmar"] is None
    assert (destination / "equity.parquet").exists()
    assert (destination / "trades.parquet").exists()


def test_publish_site_copies_results_and_refuses_env(tmp_path):
    source = tmp_path / "research"
    _research(source / "baseline")
    _valuation(source / "valuation_002594_SZ")
    _chan(source / "chan_v1")
    book = tmp_path / "fundamental_book.json"
    book.write_text(
        json.dumps({"asof": "20261029", "top_n": 8, "ranked": [{"ts_code": "000001.SZ", "rank": 1}], "token": "hidden"}),
        encoding="utf-8",
    )
    web = tmp_path / "web"
    written = publish_site(source, web, [book])

    assert (web / "research" / "baseline" / "report.md").exists()
    assert not (web / "research" / "baseline" / "diagnostics" / "forward_labels.parquet").exists()
    research_summary = json.loads((web / "research" / "baseline" / "summary.json").read_text(encoding="utf-8"))
    assert "token" not in research_summary
    assert (web / "backtests" / "baseline_holdout" / "equity.parquet").exists()
    assert (web / "valuations" / "002594.SZ_20261110" / "scenarios.parquet").exists()
    assert (web / "chan" / "chan_v1" / "walkforward" / "holdout" / "strategy_equity.parquet").exists()
    published_book = json.loads((web / "books" / "20261029.json").read_text(encoding="utf-8"))
    assert "token" not in published_book
    assert published_book["ranked"][0]["ts_code"] == "000001.SZ"
    assert written

    secret = source / "baseline" / ".env"
    secret.write_text("SXSC_TUSHARE_TOKEN=secret\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=".env"):
        publish_site(source, web, [book])
    assert not (web / ".env").exists()


def test_pages_do_not_reference_the_broker_token():
    for name in ("app.py", "load.py", "labels.py"):
        text = (ROOT / "web" / name).read_text(encoding="utf-8")
        assert "TushareProvider" not in text
        assert "SXSC_TUSHARE_TOKEN" not in text
    publisher = (ROOT / "strategy" / "publish_site.py").read_text(encoding="utf-8")
    assert "TushareProvider" not in publisher


def test_loaders_read_published_sections(tmp_path):
    source = tmp_path / "research"
    _research(source / "baseline")
    _valuation(source / "valuation_002594_SZ")
    _chan(source / "chan_v1")
    book = tmp_path / "book.json"
    book.write_text(json.dumps({"asof": "20261029", "top_n": 5, "ranked": []}), encoding="utf-8")
    web = tmp_path / "web"
    publish_site(source, web, [book])

    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("quantlab_web_load_site", ROOT / "web" / "load.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    research = module.list_research(web / "research")
    assert research[0].summary["decision"] == "continue_validation"
    assert list(research[0].rank_ic["factor"]) == ["composite"]
    valuations = module.list_valuations(web / "valuations")
    assert valuations[0].code == "002594.SZ"
    assert valuations[0].asof == "20261110"
    chan = module.list_chan(web / "chan")
    assert list(chan[0].equity["equity"]) == [100.0]
    books = module.list_books(web / "books")
    assert books[0].label == "2026-10-29"


def _research(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "report.md").write_text("# 研究\n", encoding="utf-8")
    (path / "summary.json").write_text(
        json.dumps(
            {
                "decision": "continue_validation",
                "reasons": ["通过"],
                "token": "hidden",
                "config": {
                    "holdout": {"start": "20240101", "end": "20241231"},
                    "scenario": {"capital": 40000, "top_n": 8},
                },
                "known_gaps": ["价格收益不含分红复权。"],
            }
        ),
        encoding="utf-8",
    )
    diagnostics = path / "diagnostics"
    diagnostics.mkdir()
    pd.DataFrame({"factor": ["composite"], "rank_ic": [0.2]}).to_parquet(diagnostics / "rank_ic_summary.parquet", index=False)
    pd.DataFrame({"factor": ["composite"], "rank_ic": [0.2]}).to_parquet(diagnostics / "forward_labels.parquet", index=False)
    pd.DataFrame({"name": ["cost"], "cagr": [0.01]}).to_parquet(path / "stress.parquet", index=False)
    _period(path, "holdout")


def _valuation(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "report.md").write_text("# DCF\n", encoding="utf-8")
    (path / "assumptions.json").write_text(
        json.dumps({"ts_code": "002594.SZ", "asof": "20261110", "password": "hidden"}),
        encoding="utf-8",
    )
    pd.DataFrame({"scenario": ["base"], "per_share": [79.24]}).to_parquet(path / "scenarios.parquet", index=False)
    pd.DataFrame({"0.06": [80.0]}, index=pd.Index([0.077], name="discount_rate")).to_parquet(path / "sensitivity.parquet")


def _chan(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "report.md").write_text("# 缠论\n", encoding="utf-8")
    (path / "summary.json").write_text(
        json.dumps(
            {
                "decision": "evidence_insufficient",
                "reasons": ["样本短"],
                "known_gaps": ["买卖点使用笔级简化口径。"],
                "config": {"holdout": {"start": "20240101", "end": "20241231"}, "capital": 40000},
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame({"event": ["buy"]}).to_parquet(path / "events.parquet", index=False)
    _period(path, "holdout")


def _period(path: Path, name: str) -> None:
    walk = path / "walkforward" / name
    walk.mkdir(parents=True)
    pd.DataFrame(
        {
            "period": [name],
            "strategy_cagr": [0.05],
            "strategy_max_drawdown": [-0.1],
            "strategy_sharpe": [0.4],
            "strategy_calmar": [0.5],
        }
    ).to_parquet(path / "walkforward" / "summary.parquet", index=False)
    pd.DataFrame({"trade_date": [pd.Timestamp("2024-12-31")], "equity": [100.0]}).to_parquet(walk / "strategy_equity.parquet", index=False)
    pd.DataFrame({"trade_date": [pd.Timestamp("2024-12-31")], "ts_code": ["000001.SZ"], "side": ["buy"], "shares": [100]}).to_parquet(
        walk / "trades.parquet", index=False
    )
