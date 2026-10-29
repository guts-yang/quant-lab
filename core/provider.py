"""与数据供应商无关的表结构，以及 Tushare 字段到这些表的映射。"""

from __future__ import annotations

from typing import Protocol, Sequence

import pandas as pd

HS300 = "000300.SH"
CSI1000 = "000852.SH"

BALANCE_COLUMNS = [
    "ts_code",
    "ann_date",
    "end_date",
    "goodwill",
    "equity",
    "total_assets",
    "total_liab",
    "report_type",
    "comp_type",
]
INDICATOR_COLUMNS = ["ts_code", "ann_date", "end_date", "profit_dedt"]
BAR_COLUMNS = ["ts_code", "trade_date", "amount"]
INSTRUMENT_COLUMNS = ["ts_code", "list_date", "delist_date", "list_status"]
NAME_COLUMNS = ["ts_code", "name", "start_date", "end_date"]
SUSPEND_COLUMNS = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
INDEX_MEMBER_COLUMNS = ["index_code", "ts_code", "trade_date", "weight"]

# 1 一般工商业，2 银行，3 保险，4 证券。负债表结构不同。
FINANCIAL_COMP_TYPES = {"2", "3", "4"}


def _blank_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _code(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if text in {"", "<NA>", "nan", "None", "NaT"}:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _number_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")
    return _to_number(frame[name])


def _availability(frame: pd.DataFrame) -> pd.Series:
    """实际公告日优先。f_ann_date 缺失时退回 ann_date。"""
    ann = _to_datetime(frame["ann_date"]) if "ann_date" in frame.columns else pd.Series(pd.NaT, index=frame.index)
    if "f_ann_date" not in frame.columns:
        return ann
    actual = _to_datetime(frame["f_ann_date"])
    return actual.where(actual.notna(), ann)


def normalize_balance_sheet(raw: pd.DataFrame | None) -> pd.DataFrame:
    """负债表归一。equity 用归母净资产，避免和少数股东权益混在一起。"""
    if raw is None or raw.empty:
        return _blank_frame(BALANCE_COLUMNS)
    equity_name = "total_hldr_eqy_exc_min_int" if "total_hldr_eqy_exc_min_int" in raw.columns else "equity"
    out = pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "ann_date": _availability(raw),
            "end_date": _to_datetime(raw["end_date"]),
            "goodwill": _number_column(raw, "goodwill"),
            "equity": _number_column(raw, equity_name),
            "total_assets": _number_column(raw, "total_assets"),
            "total_liab": _number_column(raw, "total_liab"),
            "report_type": raw["report_type"].map(_code) if "report_type" in raw.columns else None,
            "comp_type": raw["comp_type"].map(_code) if "comp_type" in raw.columns else None,
        }
    )
    return out.reset_index(drop=True)


def normalize_indicator(raw: pd.DataFrame | None) -> pd.DataFrame:
    """财务指标归一。年报过滤交给 annual_indicators，缓存里保留各报告期。"""
    if raw is None or raw.empty:
        return _blank_frame(INDICATOR_COLUMNS)
    out = pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "ann_date": _availability(raw),
            "end_date": _to_datetime(raw["end_date"]),
            "profit_dedt": _number_column(raw, "profit_dedt"),
        }
    )
    return out.reset_index(drop=True)


def annual_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """只留 12 月 31 日报告期，作为「最近一年扣非净利润」。"""
    if frame is None or frame.empty:
        return _blank_frame(INDICATOR_COLUMNS)
    end = pd.to_datetime(frame["end_date"], errors="coerce")
    annual = frame.loc[end.dt.month.eq(12) & end.dt.day.eq(31)].copy()
    return annual.reset_index(drop=True)


def normalize_bars(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _blank_frame(BAR_COLUMNS)
    return pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "trade_date": _to_datetime(raw["trade_date"]),
            "amount": _number_column(raw, "amount"),
        }
    ).reset_index(drop=True)


def normalize_instruments(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _blank_frame(INSTRUMENT_COLUMNS)
    delist = raw["delist_date"] if "delist_date" in raw.columns else pd.Series(pd.NaT, index=raw.index)
    status = raw["list_status"] if "list_status" in raw.columns else pd.Series(pd.NA, index=raw.index)
    return pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "list_date": _to_datetime(raw["list_date"]),
            "delist_date": _to_datetime(delist),
            "list_status": status.map(_code),
        }
    ).reset_index(drop=True)


def normalize_name_history(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _blank_frame(NAME_COLUMNS)
    end = raw["end_date"] if "end_date" in raw.columns else pd.Series(pd.NaT, index=raw.index)
    return pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "name": raw["name"].astype("string").str.strip(),
            "start_date": _to_datetime(raw["start_date"]),
            "end_date": _to_datetime(end),
        }
    ).reset_index(drop=True)


def normalize_suspensions(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _blank_frame(SUSPEND_COLUMNS)
    timing = raw["suspend_timing"] if "suspend_timing" in raw.columns else pd.Series(pd.NA, index=raw.index)
    kind = raw["suspend_type"] if "suspend_type" in raw.columns else pd.Series(pd.NA, index=raw.index)
    trade_date = raw["trade_date"] if "trade_date" in raw.columns else pd.Series(pd.NaT, index=raw.index)
    return pd.DataFrame(
        {
            "ts_code": raw["ts_code"].astype(str),
            "trade_date": _to_datetime(trade_date),
            "suspend_timing": timing.map(_code),
            "suspend_type": kind.map(_code),
        }
    ).reset_index(drop=True)


def select_index_snapshot(weights: pd.DataFrame | None, asof: pd.Timestamp) -> pd.DataFrame:
    """指数权重取不晚于 asof 的最近一个发布日。"""
    if weights is None or weights.empty:
        return _blank_frame(INDEX_MEMBER_COLUMNS)
    frame = weights.copy()
    if "con_code" in frame.columns and "ts_code" not in frame.columns:
        frame = frame.rename(columns={"con_code": "ts_code"})
    frame["trade_date"] = _to_datetime(frame["trade_date"])
    asof = pd.Timestamp(asof).normalize()
    visible = frame[frame["trade_date"].notna() & frame["trade_date"].le(asof)]
    if visible.empty:
        return _blank_frame(INDEX_MEMBER_COLUMNS)
    latest = visible["trade_date"].max()
    snap = visible[visible["trade_date"].eq(latest)]
    return pd.DataFrame(
        {
            "index_code": snap["index_code"].astype(str) if "index_code" in snap.columns else pd.NA,
            "ts_code": snap["ts_code"].astype(str),
            "trade_date": snap["trade_date"],
            "weight": _number_column(snap, "weight"),
        }
    ).reset_index(drop=True)


def recent_period_ends(asof: pd.Timestamp, n: int = 8) -> list[str]:
    """不晚于 asof 的季度报告期，新的在前。公告日对齐仍由 pit 负责。"""
    asof = pd.Timestamp(asof).normalize()
    quarter_ends = ((12, 31), (9, 30), (6, 30), (3, 31))
    found: list[pd.Timestamp] = []
    year = asof.year
    while len(found) < n and year >= asof.year - 6:
        for month, day in quarter_ends:
            period = pd.Timestamp(year=year, month=month, day=day)
            if period <= asof:
                found.append(period)
            if len(found) >= n:
                break
        year -= 1
    return [item.strftime("%Y%m%d") for item in found]


def filter_periods(frame: pd.DataFrame, periods: Sequence[str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    wanted = {pd.Timestamp(period).normalize() for period in periods}
    end = pd.to_datetime(frame["end_date"], errors="coerce").dt.normalize()
    return frame.loc[end.isin(wanted)].reset_index(drop=True)


class DataProvider(Protocol):
    def get_instruments(self, asof: pd.Timestamp) -> pd.DataFrame:
        """ts_code、list_date、delist_date。不含当前证券简称。"""
        ...

    def get_name_history(self) -> pd.DataFrame:
        """namechange 区间：ts_code、name、start_date、end_date。"""
        ...

    def get_suspensions(self, trade_date: pd.Timestamp) -> pd.DataFrame:
        """当日停牌记录，含 suspend_timing，用来区分全天停牌和盘中停牌。"""
        ...

    def get_daily_bars(self, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        """日线里只保留成交额，供流动性分位。"""
        ...

    def get_index_members(self, index_code: str, asof: pd.Timestamp) -> pd.DataFrame:
        """不晚于 asof 的最近一期指数成分。排雷本身不用。"""
        ...

    def get_balance_sheets(self, periods: Sequence[str]) -> pd.DataFrame:
        """归一后的资产负债表，periods 为 YYYYMMDD 报告期。"""
        ...

    def get_indicators(self, periods: Sequence[str]) -> pd.DataFrame:
        """已公告年报的扣非净利润。"""
        ...
