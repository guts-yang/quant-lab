"""季度对齐。财务数字先过公告日，再按报告期取数。"""

from __future__ import annotations

import pandas as pd

from core.pit import latest_visible, visible_history

QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))
CONSOLIDATED_REPORTS = {"1", "5"}


def shift_quarter_end(period: pd.Timestamp, steps: int) -> pd.Timestamp:
    """按季末日期平移。steps 为负表示往前。"""
    period = pd.Timestamp(period).normalize()
    try:
        index = QUARTER_ENDS.index((period.month, period.day))
    except ValueError:
        return pd.NaT
    moved = index + steps
    year = int(period.year + moved // 4)
    month, day = QUARTER_ENDS[moved % 4]
    return pd.Timestamp(year=year, month=month, day=day)


def announced_history(
    reports: pd.DataFrame | None,
    asof: pd.Timestamp,
    *,
    allowed_report_types: set[str] | None = None,
) -> pd.DataFrame:
    history = visible_history(reports, asof, allowed_report_types=allowed_report_types)
    if history.empty:
        return history
    history = history.copy()
    history["ts_code"] = history["ts_code"].astype(str)
    history["end_date"] = pd.to_datetime(history["end_date"], errors="coerce").dt.normalize()
    if "ann_date" in history.columns:
        history["ann_date"] = pd.to_datetime(history["ann_date"], errors="coerce").dt.normalize()
    return history.dropna(subset=["end_date"]).drop_duplicates(["ts_code", "end_date"], keep="last")


def four_quarter_sum(history: pd.DataFrame | None, column: str) -> pd.DataFrame:
    """最近四个连续已公告单季之和。缺任何一季则 total 为缺失。

    start_date 是这四个季度之前的那个季末，用来取期初净资产。
    """
    columns = ["ts_code", "end_date", "start_date", "total"]
    if history is None or history.empty or column not in history.columns:
        return pd.DataFrame(columns=columns)
    indexed = _indexed(history, column)
    rows: list[dict[str, object]] = []
    ends = history.groupby("ts_code", sort=False)["end_date"].max()
    for code, end in ends.items():
        periods = [shift_quarter_end(end, -step) for step in range(4)]
        numbers = [_lookup(indexed, str(code), period) for period in periods]
        complete = all(pd.notna(period) and pd.notna(number) for period, number in zip(periods, numbers, strict=True))
        rows.append(
            {
                "ts_code": str(code),
                "end_date": pd.Timestamp(end),
                "start_date": shift_quarter_end(end, -4),
                "total": float(sum(numbers)) if complete else pd.NA,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def single_quarter_yoy(history: pd.DataFrame | None, column: str) -> pd.DataFrame:
    """最新已公告单季相对四个季度前的同比。基期小于等于 0 时记为缺失。"""
    columns = ["ts_code", "end_date", "yoy"]
    if history is None or history.empty or column not in history.columns:
        return pd.DataFrame(columns=columns)
    indexed = _indexed(history, column)
    rows: list[dict[str, object]] = []
    ends = history.groupby("ts_code", sort=False)["end_date"].max()
    for code, end in ends.items():
        current = _lookup(indexed, str(code), end)
        previous = _lookup(indexed, str(code), shift_quarter_end(end, -4))
        yoy = pd.NA
        if pd.notna(current) and pd.notna(previous) and previous > 0:
            yoy = float(current) / float(previous) - 1
        rows.append({"ts_code": str(code), "end_date": pd.Timestamp(end), "yoy": yoy})
    return pd.DataFrame(rows, columns=columns)


def point_yoy(history: pd.DataFrame | None, column: str, code: str, end: pd.Timestamp) -> float:
    """指定报告期的单季同比。基期还没公告、缺失，或小于等于 0 时返回缺失。"""
    if history is None or history.empty or column not in history.columns:
        return pd.NA
    indexed = _indexed(history, column)
    current = _lookup(indexed, code, end)
    previous = _lookup(indexed, code, shift_quarter_end(end, -4))
    if pd.notna(current) and pd.notna(previous) and previous > 0:
        return float(current) / float(previous) - 1
    return pd.NA


def trailing_variance(history: pd.DataFrame | None, column: str, periods: int = 12) -> pd.DataFrame:
    """最近 periods 个已公告季度的方差。不足 periods 个则缺失。"""
    columns = ["ts_code", column]
    if history is None or history.empty or column not in history.columns:
        return pd.DataFrame(columns=["ts_code", "variance"])
    rows: list[dict[str, object]] = []
    for code, group in history.groupby("ts_code", sort=False):
        values = pd.to_numeric(group.sort_values("end_date")[column], errors="coerce").dropna()
        variance = pd.NA
        if len(values) >= periods:
            variance = float(values.tail(periods).var(ddof=0))
        rows.append({"ts_code": str(code), "variance": variance})
    return pd.DataFrame(rows, columns=["ts_code", "variance"])


def latest_column(reports: pd.DataFrame | None, asof: pd.Timestamp, column: str) -> pd.DataFrame:
    latest = latest_visible(reports, asof)
    if latest.empty or column not in latest.columns:
        return pd.DataFrame(columns=["ts_code", column])
    frame = latest[["ts_code", column]].copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    return frame.drop_duplicates("ts_code", keep="last")


def equity_lookup(history: pd.DataFrame | None, keys: pd.DataFrame) -> pd.Series:
    """keys 需要 ts_code 和 end_date。返回与 keys 同序的净资产。"""
    if keys.empty:
        return pd.Series(dtype="Float64")
    if history is None or history.empty or "equity" not in history.columns:
        return pd.Series(pd.NA, index=keys.index, dtype="Float64")
    indexed = _indexed(history, "equity")
    found = [_lookup(indexed, str(code), period) for code, period in zip(keys["ts_code"], keys["end_date"], strict=True)]
    return pd.Series(found, index=keys.index, dtype="Float64")


def _indexed(history: pd.DataFrame, column: str) -> pd.Series:
    frame = history.dropna(subset=["end_date"]).drop_duplicates(["ts_code", "end_date"], keep="last")
    values = pd.to_numeric(frame[column], errors="coerce")
    index = pd.MultiIndex.from_arrays([frame["ts_code"].astype(str), pd.to_datetime(frame["end_date"]).dt.normalize()])
    return pd.Series(values.to_numpy(), index=index)


def _lookup(indexed: pd.Series, code: str, period: pd.Timestamp) -> float:
    if pd.isna(period) or indexed.empty:
        return pd.NA
    key = (str(code), pd.Timestamp(period).normalize())
    if key not in indexed.index:
        return pd.NA
    found = indexed.loc[key]
    if isinstance(found, pd.Series):
        found = found.iloc[-1]
    if pd.isna(found):
        return pd.NA
    return float(found)
