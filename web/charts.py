"""估值页图表用的数据框。只整理已发布的表，不重新估值。"""

from __future__ import annotations

import pandas as pd

SCENARIO_ORDER = ("bear", "base", "bull")
SCENARIO_LABELS = {"bear": "熊市", "base": "基准", "bull": "牛市"}


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
            {"构成": "显式期现金流", "现值": explicit / 1e8},
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
