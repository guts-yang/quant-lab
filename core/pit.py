"""按实际公告日对齐报表，并还原截面日当天的证券名称。

法定披露截止日只作对照，不能当作因子或排雷的可用日：
一季报 4 月 30 日，半年报 8 月 31 日，三季报 10 月 31 日，年报次年 4 月 30 日。
回测只认实际公告日（f_ann_date，缺失时用 ann_date）。
"""

from __future__ import annotations

import pandas as pd

from core.provider import _code


def latest_visible(
    reports: pd.DataFrame | None,
    asof: pd.Timestamp,
    *,
    code_col: str = "ts_code",
    ann_col: str = "ann_date",
    f_ann_col: str = "f_ann_date",
    end_col: str = "end_date",
    report_type_col: str = "report_type",
    allowed_report_types: set[str] | None = None,
) -> pd.DataFrame:
    """每个股票留一条截面日已经公告、且报告期最新的记录。

    同一报告期有多条时，留可用日最新的那条。不预先过滤 update_flag，
    否则「今天的最新修订」会泄漏进历史截面。
    """
    if reports is None or reports.empty:
        return reports.iloc[0:0].copy() if isinstance(reports, pd.DataFrame) else pd.DataFrame()

    frame = reports.copy()
    asof = pd.Timestamp(asof).normalize()
    frame[end_col] = pd.to_datetime(frame[end_col], errors="coerce")
    announced = pd.to_datetime(frame[ann_col], errors="coerce") if ann_col in frame.columns else pd.Series(pd.NaT, index=frame.index)
    if f_ann_col in frame.columns:
        actual = pd.to_datetime(frame[f_ann_col], errors="coerce")
        announced = actual.where(actual.notna(), announced)
    frame["_available"] = announced

    visible = frame["_available"].notna() & frame[end_col].notna()
    visible &= frame["_available"].le(asof)
    visible &= frame["_available"].ge(frame[end_col])
    frame = frame.loc[visible]

    if allowed_report_types is not None and report_type_col in frame.columns:
        types = frame[report_type_col].map(_code)
        frame = frame.loc[types.isin(allowed_report_types)]

    if frame.empty:
        return frame.drop(columns=["_available"])

    frame = frame.sort_values([code_col, end_col, "_available"], kind="mergesort")
    frame = frame.drop_duplicates(code_col, keep="last")
    return frame.drop(columns=["_available"]).reset_index(drop=True)


def resolve_names(name_history: pd.DataFrame | None, asof: pd.Timestamp) -> pd.DataFrame:
    """按 start_date / end_date 闭区间还原 asof 当天的证券名称。

    end_date 为空表示这名还在用。同一天有多条时，取 start_date 最新的一条。
    从未改名的股票不会出现在结果里。
    """
    columns = ["ts_code", "name"]
    if name_history is None or name_history.empty:
        return pd.DataFrame(columns=columns)

    asof = pd.Timestamp(asof).normalize()
    frame = name_history.copy()
    frame["start_date"] = pd.to_datetime(frame["start_date"], errors="coerce")
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    started = frame["start_date"].notna() & frame["start_date"].le(asof)
    still_active = frame["end_date"].isna() | frame["end_date"].ge(asof)
    active = frame.loc[started & still_active].sort_values(["ts_code", "start_date"], kind="mergesort")
    active = active.drop_duplicates("ts_code", keep="last")
    return active[columns].reset_index(drop=True)
