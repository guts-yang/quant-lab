"""字段归一、指数成分时点、以及不访问 Tushare 的缓存降级。"""

import pandas as pd

from core.pit import latest_visible
from core.provider import (
    annual_indicators,
    normalize_balance_sheet,
    normalize_indicator,
    recent_period_ends,
    select_index_snapshot,
)
from core.tushare_provider import TushareProvider


def test_balance_sheet_uses_actual_announcement_and_parent_equity():
    raw = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "ann_date": ["20240420"],
            "f_ann_date": ["20240820"],
            "end_date": ["20231231"],
            "goodwill": [None],
            "total_hldr_eqy_exc_min_int": [10],
            "total_assets": [50],
            "total_liab": [40],
            "report_type": [1],
            "comp_type": [2.0],
        }
    )
    out = normalize_balance_sheet(raw)
    assert out.loc[0, "ann_date"] == pd.Timestamp("2024-08-20")
    assert out.loc[0, "equity"] == 10
    assert out.loc[0, "report_type"] == "1"
    assert out.loc[0, "comp_type"] == "2"
    assert latest_visible(out, "2024-06-01", allowed_report_types={"1", "5"}).empty


def test_annual_indicators_drop_interim_reports():
    raw = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "ann_date": ["20240430", "20241030"],
            "end_date": ["20231231", "20240930"],
            "profit_dedt": [-1.0, 5.0],
        }
    )
    annual = annual_indicators(normalize_indicator(raw))
    assert list(annual["end_date"]) == [pd.Timestamp("2023-12-31")]
    assert list(annual["profit_dedt"]) == [-1.0]


def test_recent_period_ends_stop_at_asof():
    assert recent_period_ends("2024-12-31", n=2) == ["20241231", "20240930"]
    assert recent_period_ends("2024-12-30", n=1) == ["20240930"]


def test_index_snapshot_uses_latest_print_on_or_before_asof():
    weights = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000300.SH", "000300.SH"],
            "con_code": ["000001.SZ", "000002.SZ", "000001.SZ"],
            "trade_date": ["20241031", "20241031", "20241129"],
            "weight": [1.0, 2.0, 3.0],
        }
    )
    snap = select_index_snapshot(weights, "2024-11-15")
    assert set(snap["ts_code"]) == {"000001.SZ", "000002.SZ"}
    assert set(snap["trade_date"]) == {pd.Timestamp("2024-10-31")}


class _FakePro:
    def __init__(self) -> None:
        self.vip_calls = 0
        self.code_calls: list[str] = []

    def balancesheet_vip(self, **kwargs: object) -> pd.DataFrame:
        self.vip_calls += 1
        raise Exception("抱歉，您没有接口访问权限")

    def balancesheet(self, ts_code: str, fields: str | None = None) -> pd.DataFrame:
        self.code_calls.append(ts_code)
        return pd.DataFrame(
            {
                "ts_code": [ts_code],
                "ann_date": ["20241030"],
                "f_ann_date": ["20241030"],
                "end_date": ["20240930"],
                "report_type": ["1"],
                "comp_type": ["1"],
                "goodwill": [0],
                "total_hldr_eqy_exc_min_int": [100],
                "total_assets": [200],
                "total_liab": [50],
            }
        )

    def stock_basic(self, exchange: str = "", list_status: str = "L", fields: str | None = None) -> pd.DataFrame:
        if list_status != "L":
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "list_date": ["19910403", "19910129"],
                "delist_date": [None, None],
                "list_status": ["L", "L"],
            }
        )


def test_balance_sheet_vip_permission_falls_back_to_cached_per_code(tmp_path):
    pro = _FakePro()
    provider = TushareProvider(cache_dir=tmp_path, pause_seconds=0, pro=pro)
    first = provider.get_balance_sheets(["20240930"])
    second = provider.get_balance_sheets(["20240930"])

    assert pro.vip_calls == 1
    assert pro.code_calls == ["000001.SZ", "000002.SZ"]
    assert set(first["ts_code"]) == {"000001.SZ", "000002.SZ"}
    assert list(first["equity"]) == [100, 100]
    assert set(second["ts_code"]) == {"000001.SZ", "000002.SZ"}
