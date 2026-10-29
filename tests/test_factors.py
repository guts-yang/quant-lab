"""因子公式和公告日。不访问网络。"""

import pandas as pd
import pytest

from factors.fundamental.common import shift_quarter_end
from factors.fundamental.growth import growth_factors
from factors.fundamental.quality import quality_factors
from factors.fundamental.value import value_factors

ASOF = "2024-06-01"


def _indicator_rows() -> list[dict[str, object]]:
    rows = [
        ("2023-03-31", "2023-04-20", 5.0),
        ("2023-06-30", "2023-08-30", 10.0),
        ("2023-09-30", "2023-10-30", 10.0),
        ("2023-12-31", "2024-04-20", 10.0),
        ("2024-03-31", "2024-04-30", 10.0),
        ("2024-06-30", "2024-08-20", 1000.0),
    ]
    return [
        {
            "ts_code": "000001.SZ",
            "end_date": end,
            "ann_date": ann,
            "q_dtprofit": profit,
            "q_sales_yoy": 15.0,
            "roic": 3.0 if end != "2024-06-30" else 99.0,
            "grossprofit_margin": 20.0,
        }
        for end, ann, profit in rows
    ]


def _balance_rows() -> list[dict[str, object]]:
    rows = [
        ("2023-03-31", "2023-04-25", 200.0),
        ("2024-03-31", "2024-04-30", 200.0),
        ("2024-06-30", "2024-08-20", 1.0),
    ]
    return [
        {
            "ts_code": "000001.SZ",
            "end_date": end,
            "ann_date": ann,
            "equity": equity,
            "report_type": "1",
        }
        for end, ann, equity in rows
    ]


def _market() -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0], "total_mv": [400.0], "pe_ttm": [5.0]})


def _dividends() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ", "000001.SZ"],
            "ex_date": ["2024-05-01", "2024-01-01", "2024-07-01", "2023-01-01"],
            "cash_div_tax": [0.5, 0.3, 5.0, 9.0],
        }
    )


def test_shift_quarter_end_moves_by_reported_quarters():
    assert shift_quarter_end("2024-03-31", -4) == pd.Timestamp("2023-03-31")
    assert shift_quarter_end("2024-03-31", -1) == pd.Timestamp("2023-12-31")


def test_future_profit_does_not_enter_roe_or_roic():
    quality = quality_factors(ASOF, ["000001.SZ"], pd.DataFrame(_balance_rows()), pd.DataFrame(_indicator_rows()))
    assert quality.loc[0, "roe_ttm"] == 0.2
    assert quality.loc[0, "roic"] == 3.0


def test_ep_uses_market_value_not_published_pe():
    value = value_factors(
        ASOF,
        ["000001.SZ"],
        _market(),
        pd.DataFrame(_balance_rows()),
        pd.DataFrame(_indicator_rows()),
        _dividends(),
    )
    assert value.loc[0, "ep_ttm"] == 0.1
    assert value.loc[0, "bp"] == 0.5
    assert value.loc[0, "dividend_yield"] == 0.08


def test_profit_yoy_needs_an_announced_base_quarter():
    hidden_base = pd.DataFrame(_indicator_rows())
    hidden_base.loc[hidden_base["end_date"].eq("2023-03-31"), "ann_date"] = "2024-08-01"
    growth = growth_factors(ASOF, ["000001.SZ"], hidden_base, pd.DataFrame())
    assert pd.isna(growth.loc[0, "q_profit_yoy"])


def test_earnings_gap_uses_the_session_after_a_positive_report():
    prices = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["2024-04-30", "2024-05-06"],
            "open": [100.0, 11.0],
            "pre_close": [10.0, 10.0],
        }
    )
    growth = growth_factors(ASOF, ["000001.SZ"], pd.DataFrame(_indicator_rows()), prices)
    assert growth.loc[0, "q_profit_yoy"] == 1.0
    assert growth.loc[0, "earnings_gap"] == pytest.approx(0.1)

    negative = pd.DataFrame(_indicator_rows())
    negative.loc[negative["end_date"].eq("2024-03-31"), "q_dtprofit"] = 1.0
    no_gap = growth_factors(ASOF, ["000001.SZ"], negative, prices)
    assert pd.isna(no_gap.loc[0, "earnings_gap"])
