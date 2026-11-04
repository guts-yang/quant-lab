"""逐日确认笔级二买、三买。笔端点日不是可交易日。"""

from __future__ import annotations

import math

import pandas as pd

from strategy.chan.bars import BI_STANDARD, require_chan, to_chan_bars

EVENT_COLUMNS = [
    "ts_code",
    "kind",
    "structure_date",
    "confirm_date",
    "price",
    "defend_price",
    "tradable",
    "basis",
]


def locked_signals(points: list[dict] | None, frozen_ends: set[pd.Timestamp], *, side: str) -> list[dict]:
    """只保留终点已经不是最后一笔的信号。二买防守确认低点，三买防守中枢下沿。"""
    if side not in ("buy", "sell"):
        raise ValueError("side 必须是 buy 或 sell")
    frozen = {pd.Timestamp(item).normalize() for item in frozen_ends}
    rows: list[dict] = []
    for point in points or []:
        kind_no = point.get("type")
        if kind_no not in (1, 2, 3):
            continue
        if side == "buy" and kind_no not in (2, 3):
            continue
        structure = _as_day(point.get("dt"))
        if structure is None or structure not in frozen:
            continue
        price = _round(point.get("price"))
        if price is None:
            continue
        if side == "buy" and kind_no == 3:
            defend = _round(point.get("zs_zd"))
            if defend is None:
                continue
        else:
            defend = price
        rows.append(
            {
                "kind": f"{side}{kind_no}",
                "structure_date": structure,
                "price": price,
                "defend_price": defend,
                "tradable": side == "buy",
                "basis": str(point.get("basis") or ""),
            }
        )
    return rows


def confirmed_events(bars: pd.DataFrame | None, universe: pd.DataFrame | None = None) -> pd.DataFrame:
    """按已收盘前缀回放。确认日是回抽笔被后一笔固定下来的那个交易日。"""
    if bars is None or bars.empty or "ts_code" not in bars.columns:
        return _empty_events()
    require_chan()
    from chan import replay_bars

    frame = bars.copy()
    frame["ts_code"] = frame["ts_code"].astype(str)
    rows: list[dict] = []
    for code, group in frame.groupby("ts_code", sort=True):
        chan_bars = to_chan_bars(group)
        if len(chan_bars) < 2:
            continue
        locked: dict[tuple[str, pd.Timestamp], dict] = {}
        for snapshot in replay_bars(chan_bars, bi_standard=BI_STANDARD):
            frozen = _frozen_ends(snapshot.result.bis)
            found = locked_signals(snapshot.result.buy_points, frozen, side="buy")
            found.extend(locked_signals(snapshot.result.sell_points, frozen, side="sell"))
            observed = _as_day(snapshot.observed_dt)
            for item in found:
                key = (item["kind"], item["structure_date"])
                if key in locked or observed is None or observed <= item["structure_date"]:
                    continue
                locked[key] = {**item, "ts_code": str(code), "confirm_date": observed}
        rows.extend(locked.values())
    events = pd.DataFrame(rows, columns=EVENT_COLUMNS) if rows else _empty_events()
    events = _filter_universe(events, universe)
    if events.empty:
        return _empty_events()
    return events.sort_values(["confirm_date", "ts_code", "kind", "structure_date"]).reset_index(drop=True)


def same_events(reference: pd.DataFrame | None, other: pd.DataFrame | None, asof: pd.Timestamp) -> bool:
    """确认日不晚于 asof 的事件必须在类型、价格和防守价上一致。"""
    left = _view(reference, asof)
    right = _view(other, asof)
    if len(left) != len(right):
        return False
    if left.empty:
        return True
    keys = ["ts_code", "kind", "structure_date", "confirm_date"]
    merged = left.merge(right, on=keys, how="outer", indicator=True)
    if not merged["_merge"].eq("both").all():
        return False
    price_ok = _close(merged["price_x"], merged["price_y"])
    defend_ok = _close(merged["defend_price_x"], merged["defend_price_y"])
    tradable_ok = merged["tradable_x"].astype(bool).eq(merged["tradable_y"].astype(bool)).all()
    return bool(price_ok and defend_ok and tradable_ok)


def _frozen_ends(bis: list) -> set[pd.Timestamp]:
    if bis is None or len(bis) < 2:
        return set()
    ends: set[pd.Timestamp] = set()
    for bi in bis[:-1]:
        day = _as_day(getattr(bi, "end_dt", None))
        if day is not None:
            ends.add(day)
    return ends


def _filter_universe(events: pd.DataFrame, universe: pd.DataFrame | None) -> pd.DataFrame:
    if universe is None or events.empty:
        return events
    if universe.empty or "ts_code" not in universe.columns or "asof" not in universe.columns:
        return events.iloc[0:0]
    members = universe.copy()
    members["ts_code"] = members["ts_code"].astype(str)
    members["asof"] = pd.to_datetime(members["asof"], errors="coerce").dt.normalize()
    members = members.dropna(subset=["asof"])
    keep: list[bool] = []
    for row in events.itertuples(index=False):
        history = members[(members["ts_code"] == row.ts_code) & (members["asof"] <= row.confirm_date)]
        keep.append(not history.empty)
    return events.loc[keep].reset_index(drop=True)


def _view(frame: pd.DataFrame | None, asof: pd.Timestamp) -> pd.DataFrame:
    columns = ["ts_code", "kind", "structure_date", "confirm_date", "price", "defend_price", "tradable"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    work = frame.copy()
    work["confirm_date"] = pd.to_datetime(work["confirm_date"]).dt.normalize()
    work["structure_date"] = pd.to_datetime(work["structure_date"]).dt.normalize()
    work = work.loc[work["confirm_date"].le(pd.Timestamp(asof).normalize()), columns]
    return work.sort_values(["ts_code", "kind", "structure_date", "confirm_date"]).reset_index(drop=True)


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=EVENT_COLUMNS)


def _as_day(value: object) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    day = pd.to_datetime(value, errors="coerce")
    if pd.isna(day):
        return None
    return pd.Timestamp(day).normalize()


def _round(value: object) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number) or not math.isfinite(float(number)):
        return None
    return round(float(number), 4)


def _close(left: pd.Series, right: pd.Series) -> bool:
    paired = pd.DataFrame({"left": pd.to_numeric(left, errors="coerce"), "right": pd.to_numeric(right, errors="coerce")})
    both_missing = paired["left"].isna() & paired["right"].isna()
    both_present = paired["left"].notna() & paired["right"].notna()
    close_enough = (paired["left"] - paired["right"]).abs().le(1e-4)
    return bool((both_missing | (both_present & close_enough)).all())
