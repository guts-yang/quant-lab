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


def test_calculation_process_shows_published_value_and_tolerates_gaps(tmp_path):
    module = _chart_module()
    path = tmp_path / "valuation_002594_SZ"
    _valuation(path)
    assumptions = json.loads((path / "assumptions.json").read_text(encoding="utf-8"))
    forecast = pd.read_parquet(path / "forecast.parquet")
    scenarios = pd.read_parquet(path / "scenarios.parquet")

    text = module.calculation_process(assumptions, forecast, scenarios)
    assert "比亚迪" in text
    assert "79.24" in text
    assert f"{79.24 / 90.0 - 1.0:+.1%}" in text

    sparse = module.calculation_process(
        {"name": "宇树科技", "template": "fcfe", "fundamentals": {"price": 100.0, "fcf_ttm": None, "shares": None}},
        pd.DataFrame(),
        pd.DataFrame(),
    )
    assert "宇树科技" in sparse
    assert "缺失" in sparse

    bank = module.calculation_process(
        {
            "name": "招商银行",
            "template": "net_income",
            "fundamentals": {"price": 43.01, "shares": 2.5e10, "fcf_ttm": None, "fcf_conversion": 2.7},
            "discount": {"beta": 0.5, "risk_free": 0.018, "equity_risk_premium": 0.055, "cost_of_equity": 0.06},
            "scenarios": {"base": {"cash_conversion": 1.0, "discount_rate": 0.06, "exit_multiple": 6.3, "exit_basis": "pe", "per_share": 66.28}},
        },
        pd.DataFrame(
            {
                "scenario": ["base"],
                "year": [1],
                "growth": [0.03],
                "revenue": [1.03e11],
                "margin": [0.4],
                "net_income": [4.12e10],
                "fcfe": [4.12e10],
            }
        ),
        pd.DataFrame({"scenario": ["base"], "per_share": [66.28], "exit_basis": ["pe"], "exit_multiple": [6.3]}),
    )
    assert "招商银行" in bank
    assert "转换率取 1" in bank
    assert "市盈率" in bank
    assert f"{66.28 / 43.01 - 1.0:+.1%}" in bank

    module.calculation_process(
        {"fundamentals": None, "discount": None, "scenarios": None, "implied_growth": "x"},
        pd.DataFrame({"scenario": ["base"], "year": [None]}),
        pd.DataFrame({"scenario": [None]}),
    )


def test_pending_fallback_says_uncalculable_and_skips_the_value_curve():
    module = _chart_module()
    assumptions = {
        "name": "神州数码",
        "ts_code": "000034.SZ",
        "asof": "20261116",
        "template": "fcfe",
        "approvals_path": "valuation/fallback_approvals.json",
        "fundamentals": {"price": 21.55, "revenue_ttm": 1.781e11, "fcf_conversion": -0.477},
        "fallbacks": [
            {
                "field": "cash_conversion",
                "reason": "历史中位数为负，不再夹到 50%",
                "proposed": None,
                "status": "pending_review",
                "computed": -0.477,
            }
        ],
        "scenarios": {"base": {"per_share": None, "growth_start": 0.09, "discount_rate": 0.12}},
    }
    forecast = pd.DataFrame(
        {"scenario": ["base"], "year": [1], "growth": [0.09], "revenue": [1e11], "margin": [0.01], "net_income": [1e9], "fcfe": [5e8]}
    )
    scenarios = pd.DataFrame({"scenario": ["bear", "base", "bull"], "per_share": [1.0, 4.86, 8.0]})
    text = module.calculation_process(assumptions, forecast, scenarios)
    assert "无法计算" in text
    assert "待审" in text or "审核" in text
    assert "fallback_approvals.json" in text
    assert "4.86" not in text
    assert module.value_frame(scenarios, assumptions).empty
    assert module.revenue_frame(forecast, assumptions).empty
    assert not module.value_frame(scenarios).empty


def test_published_runs_and_missing_values_do_not_raise_typeerror():
    """旧 Gordon 产物、已审核的神州数码，以及每股价值为空的待审兜底，打开时都不能抛 TypeError。"""
    module = _chart_module()
    root = ROOT / "web" / "valuations"

    def load(name: str):
        directory = root / name
        assumptions = json.loads((directory / "assumptions.json").read_text(encoding="utf-8"))
        scenarios = pd.read_parquet(directory / "scenarios.parquet")
        forecast = pd.read_parquet(directory / "forecast.parquet")
        sensitivity = pd.read_parquet(directory / "sensitivity.parquet")
        return assumptions, scenarios, forecast, sensitivity

    class _Run:
        def __init__(self, code, asof, assumptions, scenarios):
            self.code = code
            self.asof = asof
            self.assumptions = assumptions
            self.scenarios = scenarios

    old_assumptions, old_scenarios, old_forecast, old_sensitivity = load("600900.SH_20261116")
    old_values = module.value_frame(old_scenarios, old_assumptions)
    assert list(old_values["情景"]) == ["熊市", "基准", "牛市"]
    assert old_values["每股价值"].tolist() == pytest.approx([6.220043396495222, 36.260516116450574, 58.509602724758615])
    old_text = module.calculation_process(old_assumptions, old_forecast, old_scenarios)
    assert "长江电力" in old_text
    assert "无法计算" not in old_text
    assert not module.present_value_frame(old_scenarios, old_assumptions).empty
    assert not module.sensitivity_frame(old_sensitivity, old_assumptions).empty

    new_assumptions, new_scenarios, new_forecast, new_sensitivity = load("000034.SZ_20261116")
    new_values = module.value_frame(new_scenarios, new_assumptions)
    assert list(new_values["情景"]) == ["熊市", "基准", "牛市"]
    assert new_values["每股价值"].tolist() == pytest.approx([-0.12521392045880603, 1.1660340016953978, 12.870292388249931])
    new_text = module.calculation_process(new_assumptions, new_forecast, new_scenarios)
    assert "神州数码" in new_text
    assert "247%" in new_text
    assert "已审核兜底" in new_text
    assert "无法计算" not in new_text
    assert not module.present_value_frame(new_scenarios, new_assumptions).empty
    assert not module.sensitivity_frame(new_sensitivity, new_assumptions).empty

    ranking = module.chain_valuation_ranking(
        [
            _Run("600900.SH", "20261116", old_assumptions, old_scenarios),
            _Run("000034.SZ", "20261116", new_assumptions, new_scenarios),
        ]
    )
    assert set(ranking["代码"]) == {"600900.SH", "000034.SZ"}

    # 缺失状态如果是 pd.NA，`status or "pending_review"` 会 TypeError: boolean value of NA is ambiguous。
    pending = {
        "name": "神州数码",
        "ts_code": "000034.SZ",
        "asof": "20261116",
        "template": "fcfe",
        "approvals_path": "valuation/fallback_approvals.json",
        "fundamentals": {"price": 21.55, "revenue_ttm": 1.781e11},
        "fallbacks": [
            {
                "field": "cash_conversion",
                "reason": "历史中位数为负，不再夹到 50%",
                "proposed": None,
                "status": pd.NA,
                "computed": -0.477,
            }
        ],
        "scenarios": {
            "base": {"per_share": None, "terminal_warning": pd.NA, "discount_rate": 0.12},
            "bear": {"per_share": None, "terminal_warning": pd.NA},
            "bull": {"per_share": None, "terminal_warning": pd.NA},
        },
        "implied_growth": {"growth_start": 1.0, "average_growth": 0.51, "converged": pd.NA},
    }
    null_per_share = pd.DataFrame(
        {
            "scenario": ["bear", "base", "bull"],
            "per_share": [None, None, None],
            "terminal_warning": [pd.NA, pd.NA, pd.NA],
        }
    )
    assert module.value_frame(null_per_share, pending).empty
    assert "无法计算" in module.skipped_value_note(pending)
    pending_text = module.calculation_process(pending, pd.DataFrame(), null_per_share)
    assert "无法计算" in pending_text
    assert "待审" in pending_text
    assert "cash_conversion" in pending_text
    assert module.present_value_frame(null_per_share, pending).empty
    assert module.revenue_frame(pd.DataFrame({"scenario": ["base"], "year": [1], "revenue": [1.0]}), pending).empty

    # 没有待审项时，字符串每股价值和 pd.NA 终值提示仍然能画出数字。
    priced = {
        "name": "神州数码",
        "ts_code": "000034.SZ",
        "template": "fcfe",
        "fundamentals": {"price": 21.55, "shares": 1.0e9},
        "scenarios": {"base": {"per_share": "1.17", "terminal_warning": pd.NA, "terminal_weight": pd.NA}},
        "implied_growth": {"growth_start": 1.0, "average_growth": 0.51, "converged": pd.NA},
    }
    string_values = pd.DataFrame({"scenario": ["bear", "base", "bull"], "per_share": ["-0.13", "1.17", "12.87"]})
    framed = module.value_frame(string_values, priced)
    assert framed["每股价值"].tolist() == pytest.approx([-0.13, 1.17, 12.87])
    module.calculation_process(priced, pd.DataFrame(), string_values)
    module.chain_valuation_ranking([_Run("000034.SZ", "20261116", priced, string_values)])



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
    assert list(ranking.columns) == ["排名", "公司", "行业", "代码", "截面日", "现价", "熊市", "基准", "牛市", "估值机会"]
    assert list(ranking["公司"]) == ["丙", "甲", "乙"]
    assert list(ranking["行业"]) == ["未分类", "未分类", "未分类"]
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


def test_valuation_ranking_filters_by_industry_and_renumbers():
    module = _chart_module()
    _OMIT = object()

    class _Run:
        def __init__(self, name, code, price, base, industry=None, top_industry=None, fundamentals=_OMIT):
            self.code = code
            self.asof = "20261116"
            if fundamentals is _OMIT:
                fundamentals = {"price": price}
                if industry is not None:
                    fundamentals["industry"] = industry
            assumptions = {"name": name, "fundamentals": fundamentals}
            if top_industry is not None:
                assumptions["industry"] = top_industry
            self.assumptions = assumptions
            self.scenarios = pd.DataFrame({"scenario": ["bear", "base", "bull"], "per_share": [base - 1, base, base + 1]})

    runs = [
        _Run("甲", "000001.SZ", 10.0, 12.0, industry="半导体"),
        _Run("乙", "000002.SZ", 10.0, 14.0, industry="白酒"),
        _Run("丙", "000003.SZ", 10.0, 15.0, industry="半导体"),
        _Run("丁", "000004.SZ", 20.0, 30.0),
        _Run("戊", "000005.SZ", 10.0, 11.0, fundamentals={"price": 10.0, "industry": None}, top_industry="银行"),
        _Run("己", "000006.SZ", 10.0, 8.0, fundamentals={"price": 10.0}),
    ]
    ranking = module.valuation_ranking(runs)
    assert list(ranking["公司"]) == ["丙", "丁", "乙", "甲", "戊", "己"]
    assert list(ranking["行业"]) == ["半导体", "未分类", "白酒", "半导体", "银行", "未分类"]
    assert ranking.loc[ranking["公司"].eq("丙"), "估值机会"].iloc[0] == pytest.approx(0.5)
    assert list(ranking["估值机会"]) == sorted(ranking["估值机会"], reverse=True)

    semiconductor = module.valuation_ranking(runs, industry="半导体")
    assert list(semiconductor["公司"]) == ["丙", "甲"]
    assert list(semiconductor["行业"]) == ["半导体", "半导体"]
    assert list(semiconductor["排名"]) == [1, 2]
    assert list(semiconductor["估值机会"]) == sorted(semiconductor["估值机会"], reverse=True)
    assert semiconductor["估值机会"].iloc[0] == pytest.approx(0.5)
    assert semiconductor["估值机会"].iloc[1] == pytest.approx(0.2)

    unclassified = module.valuation_ranking(runs, industry="未分类")
    assert list(unclassified["公司"]) == ["丁", "己"]
    assert list(unclassified["排名"]) == [1, 2]
    assert list(unclassified["估值机会"]) == sorted(unclassified["估值机会"], reverse=True)

    bank = module.valuation_ranking(runs, industry="银行")
    assert list(bank["公司"]) == ["戊"]
    assert list(bank["排名"]) == [1]

    options = module.industry_filter_options(module.valuation_industry(item) for item in runs)
    assert options[0] == "全部"
    assert options[-1] == "未分类"
    assert options == ["全部", "白酒", "半导体", "银行", "未分类"]
    assert module.valuation_industry(_Run("空行业", "000007.SZ", 10.0, 10.0, industry="  ")) == "未分类"
    missing = _Run("无基本面", "000008.SZ", 10.0, 10.0, fundamentals=None)
    assert module.valuation_industry(missing) == "未分类"
    assert module.valuation_ranking([missing]).empty


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
