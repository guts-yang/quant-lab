"""佣金下限、止损成交和行情缓存字段。不访问券商接口。"""

import pandas as pd

from backtest.engine import buy_cash, commission, simulate
from core.tushare_provider import TushareProvider


def test_commission_below_five_yuan_is_raised_to_five():
    assert commission(1_000) == 5
    assert commission(40_000) == 10


def test_eight_percent_stop_closes_the_position():
    dates = pd.to_datetime(["2024-01-31", "2024-02-01"])
    bars = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": dates,
            "open": [10.0, 10.0],
            "high": [10.2, 10.0],
            "low": [9.8, 9.0],
            "close": [10.0, 9.1],
        }
    )

    def scores_at(day: pd.Timestamp) -> pd.DataFrame:
        if pd.Timestamp(day).normalize() == dates[0]:
            return pd.DataFrame({"ts_code": ["000001.SZ"], "composite": [1.0]})
        return pd.DataFrame(columns=["ts_code", "composite"])

    result = simulate(bars, None, scores_at, dates[0], dates[1], capital=10_000, top_n=5)
    buys = result.trades.loc[result.trades["side"].eq("buy")]
    stops = result.trades.loc[result.trades["reason"].eq("stop")]
    assert int(buys.iloc[0]["shares"]) == 900
    spent, fill = buy_cash(10.0, 900)
    assert spent == 900 * fill + 5
    assert list(stops["ts_code"]) == ["000001.SZ"]
    assert result.equity.iloc[-1]["equity"] < 10_000


class _Quotes:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def trade_cal(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("trade_cal", kwargs))
        return pd.DataFrame({"cal_date": ["20240131"]})

    def daily(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("daily", kwargs))
        fields = str(kwargs.get("fields", ""))
        if "high" in fields:
            return pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240131"],
                    "open": [10.0],
                    "high": [11.0],
                    "low": [9.0],
                    "close": [10.0],
                }
            )
        return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240131"], "amount": [100.0]})

    def index_daily(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("index_daily", kwargs))
        return pd.DataFrame({"ts_code": ["000300.SH"], "trade_date": ["20240131"], "close": [3000.0]})


def test_ohlc_cache_is_separate_from_amount_bars(tmp_path):
    quotes = _Quotes()
    provider = TushareProvider(cache_dir=tmp_path, pause_seconds=0, pro=quotes)
    day = pd.Timestamp("2024-01-31")
    ohlc = provider.get_ohlc(day, day)
    bars = provider.get_daily_bars(day, day)
    index = provider.get_index_daily("000300.SH", day, day)

    assert list(ohlc["close"]) == [10.0]
    assert "amount" in bars.columns
    assert list(index["close"]) == [3000.0]
    daily_fields = [str(kwargs["fields"]) for name, kwargs in quotes.calls if name == "daily"]
    assert any("high" in fields for fields in daily_fields)
    assert any(fields == "ts_code,trade_date,amount" for fields in daily_fields)
    assert (tmp_path / "ohlc" / "20240131.parquet").exists()
    assert (tmp_path / "daily" / "20240131.parquet").exists()
