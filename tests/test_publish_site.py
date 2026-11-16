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
    assert (web / "valuations" / "002594.SZ_20261110" / "history.parquet").exists()
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
    assert set(valuations[0].forecast["scenario"]) == {"bear", "base", "bull"}
    assert valuations[0].assumptions["ts_code"] == "002594.SZ"
    assert valuations[0].assumptions["name"] == "比亚迪"
    assert list(valuations[0].history["close"]) == [90.0]
    _assert_chart_frames(valuations[0])
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
        json.dumps(
            {
                "name": "比亚迪",
                "ts_code": "002594.SZ",
                "asof": "20261110",
                "password": "hidden",
                "fundamentals": {"price": 90.0, "revenue_cagr_5y": 0.2},
                "scenarios": {"base": {"average_growth": 0.08}},
                "implied_growth": {"average_growth": 0.12},
            }
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "scenario": ["bear", "base", "bull"],
            "per_share": [40.0, 79.24, 120.0],
            "pv_explicit": [1e10, 2e10, 3e10],
            "pv_terminal": [4e10, 8e10, 12e10],
        }
    ).to_parquet(path / "scenarios.parquet", index=False)
    pd.DataFrame(
        {
            "scenario": ["bear", "base", "bull"],
            "year": [1, 1, 1],
            "revenue": [1.0e10, 2.0e10, 3.0e10],
            "margin": [0.1, 0.2, 0.3],
            "fcfe": [1.0e9, 2.0e9, 3.0e9],
        }
    ).to_parquet(path / "forecast.parquet", index=False)
    pd.DataFrame({"0.06": [80.0]}, index=pd.Index([0.077], name="discount_rate")).to_parquet(path / "sensitivity.parquet")
    pd.DataFrame(
        {"trade_date": pd.to_datetime(["2026-11-10"]), "close": [90.0], "intrinsic": [70.0]}
    ).to_parquet(path / "history.parquet", index=False)


def test_history_percentile_stays_on_three_years():
    module = _chart_module()
    history = pd.DataFrame(
        {
            "trade_date": ["2020-11-16", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"],
            "close": [59.66, 10.0, 10.0, 10.0, 10.0, 59.66],
            "intrinsic": [1.0, 9.0, 8.5, 8.0, 7.5, 39.36],
        }
    )
    position = module.history_position(history, "2026-11-16")
    assert position["premium"] == pytest.approx((59.66 - 39.36) / 59.66)
    assert position["expensive_share"] == pytest.approx(0.2)
    headline = module.history_headline("长江电力（600900.SH）", position)
    assert "比往常更贵" in headline
    assert "过去 3 年" in headline
    assert "20%" in headline
    assert module.gap_label(position["premium"]) == "34% 高估"
    assert module.gap_label(-0.1) == "10% 低估"
    recent = module.history_window(history, "2026-11-16", 1)
    assert pd.Timestamp("2020-11-16") not in set(recent["trade_date"])
    assert len(recent) == 5


def _chart_module():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("quantlab_web_charts", ROOT / "web" / "charts.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assert_chart_frames(valuation) -> None:
    module = _chart_module()

    assert module.security_label(valuation.assumptions["name"], valuation.code) == "比亚迪（002594.SZ）"
    values = module.value_frame(valuation.scenarios)
    assert list(values["情景"]) == ["熊市", "基准", "牛市"]
    assert list(values.columns) == ["情景", "每股价值"]
    revenue = module.revenue_frame(valuation.forecast)
    assert set(revenue["情景"]) == {"熊市", "基准", "牛市"}
    assert list(revenue.columns) == ["年份", "情景", "收入"]
    margin = module.margin_frame(valuation.forecast)
    assert list(margin["净利率"]) == [0.2]
    present = module.present_value_frame(valuation.scenarios)
    assert list(present["构成"]) == ["显式期", "终值"]
    anchors = module.growth_anchor_frame(valuation.assumptions)
    assert list(anchors["口径"]) == ["5年历史", "基准情景", "现价隐含"]
    heat = module.sensitivity_frame(valuation.sensitivity)
    assert {"折现率", "起始增速", "每股价值"} <= set(heat.columns)
    assert len(heat) == 1


def test_valuation_ranking_sorts_by_base_upside_descending():
    module = _chart_module()

    class _Run:
        def __init__(self, name, code, asof, price, base, bear=None, bull=None):
            self.code = code
            self.asof = asof
            self.assumptions = {
                "name": name,
                "fundamentals": {"price": price},
            }
            self.scenarios = pd.DataFrame(
                {
                    "scenario": ["bear", "base", "bull"],
                    "per_share": [bear, base, bull],
                }
            )

    runs = [
        _Run("甲", "000001.SZ", "20261116", 10.0, 12.0, bear=8.0, bull=15.0),
        _Run("乙", "000002.SZ", "20261116", 10.0, 9.0, bear=7.0, bull=11.0),
        _Run("丙", "000003.SZ", "20261116", 10.0, 15.0, bear=5.0, bull=20.0),
    ]
    ranking = module.valuation_ranking(runs)
    assert list(ranking.columns) == ["排名", "公司", "代码", "截面日", "现价", "熊市", "基准", "牛市", "估值机会"]
    assert list(ranking["公司"]) == ["丙", "甲", "乙"]
    assert list(ranking["排名"]) == [1, 2, 3]
    assert ranking.loc[ranking["公司"].eq("丙"), "估值机会"].iloc[0] == pytest.approx(0.5)
    assert ranking.loc[ranking["公司"].eq("乙"), "估值机会"].iloc[0] == pytest.approx(-0.1)

    # missing price or base is skipped
    runs_with_gap = [
        _Run("甲", "000001.SZ", "20261116", 10.0, 12.0),
        _Run("无价", "000004.SZ", "20261116", None, 12.0),
    ]
    ranking_gap = module.valuation_ranking(runs_with_gap)
    assert list(ranking_gap["公司"]) == ["甲"]


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
