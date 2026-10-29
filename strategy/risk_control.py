"""组合风控。只接收已经取好的表，不访问网络。"""

from __future__ import annotations

import pandas as pd

EXIT_RANK = 15
HARD_STOP = 0.08
ATR_WINDOW = 14
ATR_MULTIPLE = 2.0
FAST_MA = 20
SLOW_MA = 60
EXPOSURE_FULL = 1.0
EXPOSURE_ONE_BREAK = 0.50
EXPOSURE_BOTH_BREAK = 0.30


def rank_scores(scores: pd.DataFrame | None) -> pd.DataFrame:
    """综合分从高到低，名次从 1 开始。同分按代码升序。"""
    columns = ["ts_code", "composite", "rank"]
    if scores is None or scores.empty or "ts_code" not in scores.columns:
        return pd.DataFrame(columns=columns)
    frame = scores.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    if "composite" not in frame.columns:
        frame["composite"] = pd.NA
    frame = frame.sort_values(["composite", "ts_code"], ascending=[False, True], na_position="last")
    frame = frame.drop_duplicates("ts_code", keep="first")
    frame["rank"] = range(1, len(frame) + 1)
    return frame.loc[:, columns].reset_index(drop=True)


def month_end_book(
    scores: pd.DataFrame | None,
    held: list[str] | pd.Series,
    top_n: int = 8,
    exit_rank: int = EXIT_RANK,
) -> list[str]:
    """跌出前 exit_rank 的持仓卖掉。前 top_n 名里还没持有的买入。其余持仓留下。"""
    if not 5 <= int(top_n) <= 8:
        raise ValueError("top_n 必须在 5 到 8 之间")
    ranked = rank_scores(scores)
    rank = dict(zip(ranked["ts_code"], ranked["rank"], strict=True))
    current = [str(code) for code in held]
    kept = [code for code in current if rank.get(code, exit_rank + 1) <= exit_rank]
    kept = sorted(set(kept), key=lambda code: (rank[code], code))
    owned = set(kept)
    fresh = [code for code in ranked["ts_code"] if int(rank[code]) <= int(top_n) and code not in owned]
    return kept + fresh


def average_true_range(bars: pd.DataFrame | None, window: int = ATR_WINDOW) -> pd.DataFrame:
    """每只股票截至当日的 ATR。真实波幅用最高、最低和前收。"""
    columns = ["ts_code", "trade_date", "atr"]
    if bars is None or bars.empty:
        return pd.DataFrame(columns=columns)
    frame = bars.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    frame = frame.sort_values(["ts_code", "trade_date"], kind="mergesort")
    previous = frame.groupby("ts_code", sort=False)["close"].shift(1)
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    true_range = pd.concat([(high - low), (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    frame["atr"] = true_range.groupby(frame["ts_code"], sort=False).transform(
        lambda values: values.rolling(window, min_periods=window).mean()
    )
    return frame.loc[:, columns].reset_index(drop=True)


def stop_price(cost: float, peak: float | None, atr: float | None) -> float:
    """硬止损是成本价下跌 8%。移动止损是最高价减去 2 倍 ATR。取更高的那个。"""
    hard = float(cost) * (1.0 - HARD_STOP)
    if peak is None or atr is None or pd.isna(peak) or pd.isna(atr):
        return hard
    return max(hard, float(peak) - ATR_MULTIPLE * float(atr))


def stop_fill(open_price: float, low: float, stop: float) -> float | None:
    """最低价触及止损才卖。开盘已经跌破时，按开盘价成交。"""
    if pd.isna(low) or pd.isna(stop) or float(low) > float(stop):
        return None
    if pd.notna(open_price) and float(open_price) < float(stop):
        return float(open_price)
    return float(stop)


def index_exposure(
    index_bars: pd.DataFrame | None,
    asof: pd.Timestamp,
    fast: int = FAST_MA,
    slow: int = SLOW_MA,
) -> float:
    """收盘跌破一条均线时仓位 50%，两条都跌破时 30%。均线之上满仓。"""
    if index_bars is None or index_bars.empty or "close" not in index_bars.columns:
        return EXPOSURE_FULL
    frame = index_bars.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.normalize()
    asof = pd.Timestamp(asof).normalize()
    history = frame.loc[frame["trade_date"].le(asof)].sort_values("trade_date")
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if closes.empty:
        return EXPOSURE_FULL
    last = float(closes.iloc[-1])
    below_fast = len(closes) >= fast and last < float(closes.tail(fast).mean())
    below_slow = len(closes) >= slow and last < float(closes.tail(slow).mean())
    if below_fast and below_slow:
        return EXPOSURE_BOTH_BREAK
    if below_fast or below_slow:
        return EXPOSURE_ONE_BREAK
    return EXPOSURE_FULL
