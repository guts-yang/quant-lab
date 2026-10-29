"""分组得分、权重和持仓截断。不访问网络。"""

import pandas as pd

from strategy.scorer import (
    _group_score,
    combine_group_scores,
    score_cross_section,
    select_holdings,
)


def test_higher_roe_gets_a_higher_quality_score():
    frame = pd.DataFrame(
        {
            "roe_ttm": [0.2, 0.05],
            "roic": [1.0, 1.0],
            "gross_margin_var": [0.2, 0.2],
        }
    )
    scores = _group_score(frame, ["roe_ttm", "roic", "gross_margin_var"])
    assert scores.iloc[0] > scores.iloc[1]


def test_lower_margin_variance_gets_a_higher_quality_score():
    frame = pd.DataFrame(
        {
            "roe_ttm": [0.1, 0.1],
            "roic": [1.0, 1.0],
            "gross_margin_var": [0.1, 4.0],
        }
    )
    scores = _group_score(frame, ["roe_ttm", "roic", "gross_margin_var"])
    assert scores.iloc[0] > scores.iloc[1]


def test_composite_uses_the_stated_weights():
    quality = pd.Series([1.0, 0.0, 0.0])
    value = pd.Series([0.0, 1.0, 0.0])
    growth = pd.Series([0.0, 0.0, 1.0])
    assert combine_group_scores(quality, value, growth).tolist() == [0.4, 0.3, 0.3]


def test_select_holdings_keeps_the_top_slice():
    scores = pd.DataFrame({"ts_code": [f"{i:06d}.SZ" for i in range(1, 11)], "composite": list(range(1, 11))})
    selected = select_holdings(scores, top_n=8)
    assert len(selected) == 8
    assert set(selected["ts_code"]).isdisjoint({"000001.SZ", "000002.SZ"})
    assert selected.iloc[0]["ts_code"] == "000010.SZ"


def test_cross_section_ranks_the_more_profitable_name_higher():
    universe = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "industry": ["银行", "银行"]})
    market = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"], "close": [10.0, 10.0], "total_mv": [400.0, 400.0]})
    quarters = ["2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31"]
    announcements = ["2023-08-30", "2023-10-30", "2024-04-20", "2024-04-30"]
    indicators = []
    balances = []
    for code, profit in (("000001.SZ", 10.0), ("000002.SZ", 5.0)):
        for end, ann in zip(quarters, announcements, strict=True):
            indicators.append(
                {
                    "ts_code": code,
                    "end_date": end,
                    "ann_date": ann,
                    "q_dtprofit": profit,
                    "q_sales_yoy": 10.0,
                    "roic": 3.0,
                    "grossprofit_margin": 20.0,
                }
            )
        for end, ann in (("2023-03-31", "2023-04-25"), ("2024-03-31", "2024-04-30")):
            balances.append(
                {"ts_code": code, "end_date": end, "ann_date": ann, "equity": 200.0, "report_type": "1"}
            )
    scored = score_cross_section(
        "2024-06-01",
        universe,
        market,
        pd.DataFrame(balances),
        pd.DataFrame(indicators),
        pd.DataFrame(columns=["ts_code", "ex_date", "cash_div_tax"]),
        pd.DataFrame(columns=["ts_code", "trade_date", "open", "pre_close"]),
    )
    quality = scored.set_index("ts_code")["quality_score"]
    assert quality.loc["000001.SZ"] > quality.loc["000002.SZ"]
    assert scored.iloc[0]["ts_code"] == "000001.SZ"
