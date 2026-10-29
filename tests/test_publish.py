"""快照写入使用合成数据，不访问券商接口。"""

import pandas as pd
import pytest

from strategy.publish import publish


class _Provider:
    def __init__(self) -> None:
        self.asof = pd.Timestamp("2024-06-01")
        self.codes = ["000001.SZ", "000002.SZ", "000003.SZ"]

    def get_instruments(self, asof: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "list_date": ["2010-01-04", "2010-01-04", "2010-01-04"],
                "delist_date": [None, None, None],
                "list_status": ["L", "L", "L"],
                "industry": ["银行", "食品", "综合"],
            }
        )

    def get_name_history(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "name": ["甲公司", "乙公司", "*ST丙"],
                "start_date": ["2020-01-01", "2020-01-01", "2020-01-01"],
                "end_date": [None, None, None],
            }
        )

    def get_suspensions(self, trade_date: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "trade_date", "suspend_timing", "suspend_type"])

    def get_daily_bars(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        dates = pd.bdate_range(end=self.asof - pd.Timedelta(days=1), periods=20)
        rows = []
        for code in self.codes:
            for trade_date in dates:
                rows.append({"ts_code": code, "trade_date": trade_date, "amount": 1_000_000_000})
        return pd.DataFrame(rows)

    def get_balance_sheets(self, periods: object) -> pd.DataFrame:
        rows = []
        for code in self.codes:
            rows.append(
                {
                    "ts_code": code,
                    "ann_date": "2024-04-28",
                    "end_date": "2024-03-31",
                    "report_type": "1",
                    "comp_type": "1",
                    "goodwill": 0.0,
                    "equity": 200.0,
                    "total_assets": 400.0,
                    "total_liab": 100.0,
                }
            )
        return pd.DataFrame(rows)

    def get_indicators(self, periods: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": self.codes,
                "ann_date": ["2024-04-20"] * 3,
                "end_date": ["2023-12-31"] * 3,
                "profit_dedt": [8.0, 8.0, 8.0],
            }
        )

    def get_factor_indicators(self, periods: object) -> pd.DataFrame:
        quarters = ["2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31"]
        announcements = ["2023-08-20", "2023-10-25", "2024-04-20", "2024-04-28"]
        rows = []
        for code, profit in (("000001.SZ", 10.0), ("000002.SZ", 5.0), ("000003.SZ", 1.0)):
            for end, ann in zip(quarters, announcements, strict=True):
                rows.append(
                    {
                        "ts_code": code,
                        "ann_date": ann,
                        "end_date": end,
                        "q_dtprofit": profit,
                        "q_sales_yoy": 10.0,
                        "roic": 8.0,
                        "grossprofit_margin": 20.0,
                    }
                )
        return pd.DataFrame(rows)

    def get_daily_basic(self, asof: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["2024-05-31", "2024-05-31"],
                "close": [10.0, 10.0],
                "total_mv": [400.0, 400.0],
            }
        )

    def get_dividends(self, ts_codes: object) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "ex_date", "cash_div_tax"])

    def get_daily_prices(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame(columns=["ts_code", "trade_date", "open", "pre_close"])


def test_publish_keeps_names_reasons_and_raw_factors(tmp_path):
    directory = publish(_Provider(), "20240601", top_n=5, snapshot_root=tmp_path)
    holdings = pd.read_parquet(directory / "holdings.parquet")
    scores = pd.read_parquet(directory / "scores.parquet")
    rejected = pd.read_parquet(directory / "rejected.parquet")

    assert set(scores["ts_code"]) == {"000001.SZ", "000002.SZ"}
    first = scores.loc[scores["ts_code"].eq("000001.SZ")].iloc[0]
    assert first["name"] == "甲公司"
    assert first["industry"] == "银行"
    assert first["ep_ttm"] == pytest.approx(0.1)
    assert list(holdings["ts_code"])[0] == "000001.SZ"
    assert set(rejected["ts_code"]) == {"000003.SZ"}
    assert "st" in rejected.iloc[0]["reasons"].split(",")
    assert rejected.iloc[0]["name"] == "*ST丙"
