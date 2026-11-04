"""把仓库日线表转成 chan-lun-core 的单标的 bar。"""

from __future__ import annotations

import pandas as pd

CHAN_VERSION = "1.2.2"
BI_STANDARD = "81"


def require_chan():
    """导入固定版本。版本不一致时拒绝计算，避免结构口径漂移。"""
    import chan

    version = str(getattr(chan, "__version__", ""))
    if version != CHAN_VERSION:
        found = version or "未知版本"
        raise RuntimeError(f"需要 chan-lun-core {CHAN_VERSION}，当前是 {found}")
    return chan


def to_chan_bars(frame: pd.DataFrame) -> list[dict]:
    """单只股票的日线转为已收盘 bar。停牌且无波动的行不进入结构计算。"""
    require_chan()
    from chan import normalize_bars

    if frame is None or frame.empty:
        return []
    if "ts_code" in frame.columns and frame["ts_code"].astype(str).nunique() > 1:
        raise ValueError("to_chan_bars 一次只接受一只股票")
    work = frame.copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce").dt.normalize()
    for name in ("open", "high", "low", "close"):
        work[name] = pd.to_numeric(work[name], errors="coerce")
    if "volume" in work.columns:
        work["volume"] = pd.to_numeric(work["volume"], errors="coerce").fillna(0.0)
    else:
        work["volume"] = 1.0
    work = work.dropna(subset=["trade_date", "open", "high", "low", "close"])
    valid = (work["open"] > 0) & (work["high"] > 0) & (work["low"] > 0) & (work["close"] > 0)
    valid &= work["low"] <= work["open"]
    valid &= work["low"] <= work["close"]
    valid &= work["open"] <= work["high"]
    valid &= work["close"] <= work["high"]
    work = work.loc[valid].sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    if work.empty:
        return []
    payload = pd.DataFrame(
        {
            "dt": [value.to_pydatetime() for value in work["trade_date"]],
            "open": work["open"].astype(float).tolist(),
            "high": work["high"].astype(float).tolist(),
            "low": work["low"].astype(float).tolist(),
            "close": work["close"].astype(float).tolist(),
            "volume": work["volume"].astype(float).tolist(),
        }
    )
    return normalize_bars(payload, dt_col="dt", drop_paused=True)
