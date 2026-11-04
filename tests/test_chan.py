"""缠论确认滞后、未来数据和日频交易约束。不访问网络。"""

import pandas as pd
import pytest

from backtest.engine import buy_cash
from chan import analyze_bars
from research.chan_report import make_chan_config, run_chan_research
from strategy.chan import confirmed_events, locked_signals, same_events, to_chan_bars
from strategy.chan.simulate import simulate_chan
from strategy.risk_control import index_exposure


def test_second_buy_defends_its_low_and_waits_for_a_later_bi():
    point = {"type": 2, "dt": "2024-01-05", "price": 9.5, "b1_price": 9.0, "basis": "回调"}
    day = pd.Timestamp("2024-01-05")
    assert locked_signals([point], set(), side="buy") == []
    ready = locked_signals([point], {day}, side="buy")
    assert ready[0]["kind"] == "buy2"
    assert ready[0]["defend_price"] == 9.5
    assert ready[0]["tradable"] is True
    ignored = locked_signals([{"type": 1, "dt": "2024-01-05", "price": 8.0}], {day}, side="buy")
    assert ignored == []


def test_third_buy_defends_the_center_low():
    point = {"type": 3, "dt": "2024-01-05", "price": 12.0, "zs_zd": 10.5, "basis": "回抽"}
    ready = locked_signals([point], {pd.Timestamp("2024-01-05")}, side="buy")
    assert ready[0]["kind"] == "buy3"
    assert ready[0]["defend_price"] == 10.5


def test_contained_bar_is_merged_before_fractals():
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 3,
            "trade_date": dates,
            "open": [10.0, 10.5, 10.0],
            "high": [12.0, 11.0, 11.5],
            "low": [8.0, 9.0, 9.2],
            "close": [11.0, 10.2, 10.4],
            "volume": [1000.0, 1000.0, 1000.0],
        }
    )
    bars = to_chan_bars(frame)
    assert len(bars) == 3
    result = analyze_bars(bars, bi_standard="81")
    assert len(result.new_bars) < len(bars)


def test_third_buy_confirmation_is_later_than_the_bi_endpoint_and_stable():
    frame = _zigzag()
    events = confirmed_events(frame)
    buys = events.loc[events["kind"].eq("buy3")]
    assert len(buys) == 1
    buy = buys.iloc[0]
    assert buy["confirm_date"] > buy["structure_date"]
    early = confirmed_events(frame.loc[frame["trade_date"].le(buy["structure_date"])])
    assert early.loc[early["kind"].eq("buy3")].empty
    at_confirm = confirmed_events(frame.loc[frame["trade_date"].le(buy["confirm_date"])])
    assert same_events(events, at_confirm, buy["confirm_date"])
    later = buy["confirm_date"] + pd.Timedelta(days=3)
    assert same_events(events, confirmed_events(frame.loc[frame["trade_date"].le(later)]), buy["confirm_date"])


def test_events_require_membership_on_or_before_confirmation():
    frame = _zigzag()
    events = confirmed_events(frame)
    confirm = events.loc[events["kind"].eq("buy3"), "confirm_date"].iloc[0]
    missed = confirmed_events(frame, pd.DataFrame({"ts_code": ["000001.SZ"], "asof": [confirm + pd.Timedelta(days=1)]}))
    assert missed.loc[missed["kind"].eq("buy3")].empty
    kept = confirmed_events(frame, pd.DataFrame({"ts_code": ["000001.SZ"], "asof": [confirm]}))
    assert len(kept.loc[kept["kind"].eq("buy3")]) == 1


def test_entry_uses_hundred_share_lots_and_the_five_yuan_commission():
    dates = pd.to_datetime(["2024-03-01", "2024-03-04"])
    bars = _flat("000001.SZ", dates, 10.0)
    events = _signal("000001.SZ", dates[0], "buy2", 1.0)
    result = simulate_chan(bars, events, None, dates[0], dates[1], capital=3007)
    buy = result.trades.loc[result.trades["reason"].eq("entry")].iloc[0]
    assert int(buy["shares"]) == 200
    assert int(buy["shares"]) % 100 == 0
    spent, fill = buy_cash(10.0, int(buy["shares"]))
    assert spent <= 3007
    assert spent - int(buy["shares"]) * fill == pytest.approx(5)
    assert result.equity.iloc[-1]["cash"] == pytest.approx(3007 - spent)


def test_stop_and_structure_break_sell_on_the_next_actionable_day():
    dates = pd.to_datetime(["2024-03-01", "2024-03-04", "2024-03-05"])
    bars = _flat("000001.SZ", dates, 10.0)
    bars.loc[bars["trade_date"].eq(dates[1]), ["low", "close"]] = [9.4, 9.4]
    bars.loc[bars["trade_date"].eq(dates[2]), ["open", "low", "close"]] = [9.3, 9.2, 9.3]
    stopped = simulate_chan(bars, _signal("000001.SZ", dates[0], "buy3", 1.0), None, dates[0], dates[2], capital=20_000)
    assert list(stopped.trades.loc[stopped.trades["reason"].eq("stop"), "trade_date"]) == [dates[2]]

    intact = _flat("000001.SZ", dates, 10.0)
    intact.loc[intact["trade_date"].eq(dates[1]), ["low", "close"]] = [9.4, 9.4]
    intact.loc[intact["trade_date"].eq(dates[2]), ["open", "high", "low", "close"]] = [9.3, 9.4, 9.3, 9.35]
    broken = simulate_chan(intact, _signal("000001.SZ", dates[0], "buy2", 9.5), None, dates[0], dates[2], capital=20_000)
    sales = broken.trades.loc[broken.trades["reason"].eq("structure")]
    assert list(sales["trade_date"]) == [dates[2]]
    assert float(sales.iloc[0]["price"]) == pytest.approx(9.3)


def test_limit_up_defers_the_buy_and_limit_down_defers_the_sell():
    dates = pd.to_datetime(["2024-03-01", "2024-03-04", "2024-03-05", "2024-03-06"])
    bars = _flat("000001.SZ", dates, 10.0)
    bars.loc[bars["trade_date"].eq(dates[1]), ["open", "high", "low", "close"]] = [11.0, 11.0, 10.0, 10.0]
    bars.loc[bars["trade_date"].eq(dates[2]), ["open", "high", "low", "close"]] = [10.5, 10.6, 10.4, 10.5]
    deferred = simulate_chan(bars, _signal("000001.SZ", dates[0], "buy2", 1.0), None, dates[0], dates[2], capital=20_000)
    assert list(deferred.trades["trade_date"]) == [dates[2]]

    exit_bars = _flat("000001.SZ", dates, 10.0)
    exit_bars.loc[exit_bars["trade_date"].eq(dates[1]), ["low", "close"]] = [9.4, 9.4]
    exit_bars.loc[exit_bars["trade_date"].eq(dates[2]), ["open", "high", "low", "close"]] = [8.46, 8.46, 8.46, 8.46]
    exit_bars.loc[exit_bars["trade_date"].eq(dates[3]), ["open", "high", "low", "close"]] = [9.0, 9.1, 8.9, 9.0]
    delayed = simulate_chan(exit_bars, _signal("000001.SZ", dates[0], "buy2", 9.5), None, dates[0], dates[3], capital=20_000)
    assert dates[2] not in set(delayed.trades["trade_date"])
    assert list(delayed.trades.loc[delayed.trades["reason"].eq("structure"), "trade_date"]) == [dates[3]]


def test_at_most_five_names_are_opened():
    dates = pd.to_datetime(["2024-03-01", "2024-03-04"])
    frames = [_flat(f"{index:06d}.SZ", dates, 10.0) for index in range(1, 7)]
    bars = pd.concat(frames, ignore_index=True)
    events = pd.concat([_signal(f"{index:06d}.SZ", dates[0], "buy2", 1.0) for index in range(1, 7)], ignore_index=True)
    result = simulate_chan(bars, events, None, dates[0], dates[1], capital=40_000)
    bought = set(result.trades.loc[result.trades["reason"].eq("entry"), "ts_code"])
    assert bought == {f"{index:06d}.SZ" for index in range(1, 6)}


def test_index_break_cuts_gross_exposure():
    dates = pd.to_datetime(["2024-03-01", "2024-03-04"])
    bars = _flat("000001.SZ", dates, 10.0)
    events = _signal("000001.SZ", dates[0], "buy3", 1.0)
    rising = _index(dates[1], 100.0, 120.0)
    falling = _index(dates[1], 100.0, 50.0)
    assert index_exposure(falling.loc[falling["trade_date"].lt(dates[1])], dates[1]) == 0.30
    full = simulate_chan(bars, events, rising, dates[0], dates[1], capital=40_000)
    reduced = simulate_chan(bars, events, falling, dates[0], dates[1], capital=40_000)
    assert int(reduced.trades.iloc[0]["shares"]) < int(full.trades.iloc[0]["shares"])


def test_research_report_keeps_its_own_directory(tmp_path):
    dates = pd.bdate_range("2024-03-01", "2024-03-29")
    bars = _flat("000001.SZ", dates, 10.0)
    events = _signal("000001.SZ", pd.Timestamp("2024-03-21"), "buy2", 1.0)
    index = pd.DataFrame({"trade_date": dates, "close": 100.0})
    config = make_chan_config("20240301", "20240308", "20240318", "20240329", min_holdout_months=12)
    root = tmp_path / "chan_swing"
    result = run_chan_research(bars, events, index, config, root)
    report = (root / "report.md").read_text(encoding="utf-8")
    assert "沪深300" in report
    assert "等权" in report
    assert result["decision"] == "evidence_insufficient"
    assert (root / "events.parquet").exists()
    with pytest.raises(FileExistsError):
        run_chan_research(bars, events, index, config, root)


def _zigzag() -> pd.DataFrame:
    points = [30, 22, 28, 24, 40, 33, 38, 35]
    closes: list[float] = []
    steps = 6
    for start, end in zip(points, points[1:]):
        segment = [start + (end - start) * i / (steps - 1) for i in range(steps)]
        closes.extend(segment if not closes else segment[1:])
    first = pd.Timestamp("2024-01-02")
    rows = []
    for index, close in enumerate(closes):
        rows.append(
            {
                "ts_code": "000001.SZ",
                "trade_date": first + pd.Timedelta(days=index),
                "open": close,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def _flat(code: str, dates: pd.DatetimeIndex | pd.Series, price: float) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "ts_code": [code] * count,
            "trade_date": pd.to_datetime(dates),
            "open": [price] * count,
            "high": [price] * count,
            "low": [price] * count,
            "close": [price] * count,
            "volume": [1000.0] * count,
        }
    )


def _signal(code: str, confirm: pd.Timestamp, kind: str, defend: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": [code],
            "kind": [kind],
            "structure_date": [confirm - pd.Timedelta(days=5)],
            "confirm_date": [pd.Timestamp(confirm)],
            "price": [10.0],
            "defend_price": [defend],
            "tradable": [True],
            "basis": ["测试"],
        }
    )


def _index(entry: pd.Timestamp, base: float, last: float) -> pd.DataFrame:
    """最后一根收盘落在开仓日前，供开盘时使用。"""
    dates = pd.date_range(entry - pd.Timedelta(days=90), entry - pd.Timedelta(days=1))
    closes = [base] * (len(dates) - 1) + [last]
    return pd.DataFrame({"trade_date": dates, "close": closes})
