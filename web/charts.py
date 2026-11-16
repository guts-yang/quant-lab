"""估值页图表用的数据框。只整理已发布的表，不重新估值。"""

from __future__ import annotations

import pandas as pd

SCENARIO_ORDER = ("bear", "base", "bull")
SCENARIO_LABELS = {"bear": "熊市", "base": "基准", "bull": "牛市"}


def security_label(name: str | None, code: str) -> str:
    """公司名称在前，代码放在括号里。没有名称时只留代码。"""
    company = str(name or "").strip()
    if company in {"", "None", "nan", "<NA>"}:
        return str(code)
    return f"{company}（{code}）"


def value_frame(scenarios: pd.DataFrame | None) -> pd.DataFrame:
    """熊、基准、牛的每股价值，顺序固定。"""
    columns = ["情景", "每股价值"]
    if scenarios is None or scenarios.empty or "scenario" not in scenarios.columns or "per_share" not in scenarios.columns:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for name in SCENARIO_ORDER:
        matched = scenarios.loc[scenarios["scenario"].astype(str).eq(name)]
        if matched.empty:
            continue
        value = _number(matched["per_share"].iloc[0])
        if value is None:
            continue
        rows.append({"情景": SCENARIO_LABELS[name], "每股价值": value})
    return pd.DataFrame(rows, columns=columns)


def quoted_price(assumptions: dict | None) -> float | None:
    fundamentals = (assumptions or {}).get("fundamentals") or {}
    return _number(fundamentals.get("price"))


def present_value_frame(scenarios: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景的显式期现值与终值现值，单位是亿元。"""
    columns = ["构成", "现值"]
    if scenarios is None or scenarios.empty or "scenario" not in scenarios.columns:
        return pd.DataFrame(columns=columns)
    matched = scenarios.loc[scenarios["scenario"].astype(str).eq("base")]
    if matched.empty:
        return pd.DataFrame(columns=columns)
    row = matched.iloc[0]
    explicit = _number(row.get("pv_explicit"))
    terminal = _number(row.get("pv_terminal"))
    if explicit is None or terminal is None:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        [
            {"构成": "显式期", "现值": explicit / 1e8},
            {"构成": "终值", "现值": terminal / 1e8},
        ],
        columns=columns,
    )


def revenue_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """三情景收入路径，单位是亿元。没有 scenario 列时全部记为基准。"""
    columns = ["年份", "情景", "收入"]
    if forecast is None or forecast.empty or "revenue" not in forecast.columns or "year" not in forecast.columns:
        return pd.DataFrame(columns=columns)
    frame = forecast.copy()
    if "scenario" not in frame.columns:
        frame["scenario"] = "base"
    frame["情景"] = frame["scenario"].astype(str).map(SCENARIO_LABELS)
    frame = frame.dropna(subset=["情景"])
    frame["年份"] = pd.to_numeric(frame["year"], errors="coerce")
    frame["收入"] = pd.to_numeric(frame["revenue"], errors="coerce") / 1e8
    frame = frame.dropna(subset=["年份", "收入"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    order = {label: index for index, label in enumerate(SCENARIO_LABELS.values())}
    frame = frame.assign(_order=frame["情景"].map(order)).sort_values(["_order", "年份"], kind="mergesort")
    return frame[columns].reset_index(drop=True)


def margin_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景净利率，仍是比率。"""
    columns = ["年份", "净利率"]
    base = _base_forecast(forecast)
    if base.empty or "margin" not in base.columns:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(
        {
            "年份": pd.to_numeric(base["year"], errors="coerce"),
            "净利率": pd.to_numeric(base["margin"], errors="coerce"),
        }
    ).dropna()
    return frame.sort_values("年份", kind="mergesort").reset_index(drop=True)


def fcfe_frame(forecast: pd.DataFrame | None) -> pd.DataFrame:
    """基准情景股权现金流，单位是亿元。"""
    columns = ["年份", "股权现金流"]
    base = _base_forecast(forecast)
    if base.empty or "fcfe" not in base.columns:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(
        {
            "年份": pd.to_numeric(base["year"], errors="coerce"),
            "股权现金流": pd.to_numeric(base["fcfe"], errors="coerce") / 1e8,
        }
    ).dropna()
    return frame.sort_values("年份", kind="mergesort").reset_index(drop=True)


def growth_anchor_frame(assumptions: dict | None) -> pd.DataFrame:
    """历史、基准、现价隐含三种收入增速。缺哪一项就少一根柱。"""
    columns = ["口径", "增速"]
    payload = assumptions or {}
    rows: list[dict[str, object]] = []
    history = _number(((payload.get("fundamentals") or {}).get("revenue_cagr_5y")))
    if history is not None:
        rows.append({"口径": "5年历史", "增速": history})
    base = _number(((payload.get("scenarios") or {}).get("base") or {}).get("average_growth"))
    if base is not None:
        rows.append({"口径": "基准情景", "增速": base})
    implied = payload.get("implied_growth") or {}
    implied_growth = _number(implied.get("average_growth")) if isinstance(implied, dict) else None
    if implied_growth is not None:
        rows.append({"口径": "现价隐含", "增速": implied_growth})
    return pd.DataFrame(rows, columns=columns)


def sensitivity_frame(sensitivity: pd.DataFrame | None) -> pd.DataFrame:
    """折现率 × 起始增速的长表。标签按数值排序，避免百分号字符串的字典序。"""
    columns = ["折现率", "起始增速", "每股价值", "折现率标签", "增速标签"]
    if sensitivity is None or sensitivity.empty:
        return pd.DataFrame(columns=columns)
    grid = sensitivity.copy()
    grid.index = pd.to_numeric(grid.index, errors="coerce")
    grid.columns = pd.to_numeric(grid.columns, errors="coerce")
    long = grid.stack(future_stack=True).rename("每股价值").reset_index()
    long.columns = ["折现率", "起始增速", "每股价值"]
    long["折现率"] = pd.to_numeric(long["折现率"], errors="coerce")
    long["起始增速"] = pd.to_numeric(long["起始增速"], errors="coerce")
    long["每股价值"] = pd.to_numeric(long["每股价值"], errors="coerce")
    long = long.dropna()
    if long.empty:
        return pd.DataFrame(columns=columns)
    long["折现率标签"] = long["折现率"].map(lambda value: f"{value:.1%}")
    long["增速标签"] = long["起始增速"].map(lambda value: f"{value:.1%}")
    return long[columns].reset_index(drop=True)


HISTORY_WINDOWS = {"近1年": 1, "近3年": 3}


def history_window(history: pd.DataFrame | None, asof: object, years: int) -> pd.DataFrame:
    """截到截面日为止的最近若干年。premium = (收盘价 − 内在价值) / 收盘价。"""
    columns = ["trade_date", "close", "intrinsic", "premium"]
    if history is None or history.empty or "trade_date" not in history.columns:
        return pd.DataFrame(columns=columns)
    frame = history.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
    frame["close"] = pd.to_numeric(frame.get("close"), errors="coerce")
    frame["intrinsic"] = pd.to_numeric(frame.get("intrinsic"), errors="coerce") if "intrinsic" in frame.columns else pd.NA
    frame = frame.dropna(subset=["trade_date", "close"])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    end = pd.Timestamp(asof).normalize() if asof else frame["trade_date"].max()
    start = end - pd.DateOffset(years=int(years))
    frame = frame.loc[frame["trade_date"].between(start, end)]
    frame = frame.sort_values("trade_date", kind="mergesort").drop_duplicates("trade_date", keep="last")
    close = frame["close"]
    intrinsic = frame["intrinsic"]
    frame["premium"] = (close - intrinsic) / close.where(close > 0)
    return frame[columns].reset_index(drop=True)


def history_position(history: pd.DataFrame | None, asof: object) -> dict[str, float] | None:
    """过去 3 年里，高估幅度大于或等于今天的交易日占比。缺内在价值的日期不计。"""
    window = history_window(history, asof, HISTORY_WINDOWS["近3年"])
    usable = window.dropna(subset=["premium"])
    if usable.empty:
        return None
    latest = usable.iloc[-1]
    share = float((usable["premium"] >= float(latest["premium"])).mean())
    return {
        "close": float(latest["close"]),
        "intrinsic": float(latest["intrinsic"]),
        "premium": float(latest["premium"]),
        "expensive_share": share,
    }


def history_headline(name: str, position: dict[str, float] | None) -> str | None:
    """三年位置的一句话。占比不随页面上的 1 年 / 3 年切换改变。"""
    if not position:
        return None
    share = position["expensive_share"]
    pct = f"{share:.0%}"
    if share < 0.5:
        return f"{name}比往常更贵。过去 3 年里，只有约 {pct} 的交易日达到或超过今天的高估幅度。"
    if share > 0.5:
        return f"{name}比往常更便宜。过去 3 年里，约 {pct} 的交易日达到或超过今天的高估幅度。"
    return f"{name}的估值处在过去 3 年的中间。约 {pct} 的交易日达到或超过今天的高估幅度。"


def gap_label(premium: float) -> str:
    """徽章文字。正数是现价里高出内在价值的部分。"""
    if abs(premium) < 0.005:
        return "与内在价值接近"
    if premium > 0:
        return f"{premium:.0%} 高估"
    return f"{abs(premium):.0%} 低估"


def price_value_lines(window: pd.DataFrame | None) -> pd.DataFrame:
    """股价实线和内在价值虚线共用的长表。"""
    columns = ["trade_date", "序列", "价格"]
    if window is None or window.empty:
        return pd.DataFrame(columns=columns)
    price = window.loc[window["close"].notna(), ["trade_date", "close"]].rename(columns={"close": "价格"})
    price["序列"] = "股价"
    intrinsic = window.loc[window["intrinsic"].notna(), ["trade_date", "intrinsic"]].rename(columns={"intrinsic": "价格"})
    intrinsic["序列"] = "内在价值"
    lines = pd.concat([price, intrinsic], ignore_index=True)
    return lines[columns]


def valuation_ranking(runs) -> pd.DataFrame:
    """把所有估值按基准情景相对现价的上行空间排序，最被低估的排最前。

    估值机会 = 基准每股价值 / 现价 − 1。现价或基准缺失的股票不进排名。
    """
    columns = ["公司", "代码", "截面日", "现价", "熊市", "基准", "牛市", "估值机会"]
    rows: list[dict[str, object]] = []
    for run in runs or []:
        assumptions = getattr(run, "assumptions", None) or {}
        fundamentals = assumptions.get("fundamentals") or {}
        price = _number(fundamentals.get("price"))
        name = str(assumptions.get("name") or "").strip()
        if name in {"", "None", "nan", "<NA>"}:
            name = str(getattr(run, "code", "") or "")
        scenarios = getattr(run, "scenarios", None)
        per_share: dict[str, float | None] = {}
        if scenarios is not None and not scenarios.empty and "scenario" in scenarios.columns:
            for label in ("bear", "base", "bull"):
                matched = scenarios.loc[scenarios["scenario"].astype(str).eq(label)]
                per_share[label] = _number(matched["per_share"].iloc[0]) if not matched.empty else None
        else:
            per_share = {"bear": None, "base": None, "bull": None}
        base = per_share.get("base")
        if price is None or price <= 0 or base is None:
            continue
        rows.append(
            {
                "公司": name,
                "代码": str(getattr(run, "code", "") or ""),
                "截面日": str(getattr(run, "asof", "") or ""),
                "现价": price,
                "熊市": per_share.get("bear"),
                "基准": base,
                "牛市": per_share.get("bull"),
                "估值机会": base / price - 1.0,
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    frame = frame.sort_values("估值机会", ascending=False, kind="stable").reset_index(drop=True)
    frame.insert(0, "排名", range(1, len(frame) + 1))
    return frame


def _base_forecast(forecast: pd.DataFrame | None) -> pd.DataFrame:
    if forecast is None or forecast.empty or "year" not in forecast.columns:
        return pd.DataFrame()
    if "scenario" not in forecast.columns:
        return forecast
    return forecast.loc[forecast["scenario"].astype(str).eq("base")]


def _number(value: object) -> float | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number
