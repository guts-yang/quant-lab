"""页面上的中文名称。原因编码与排雷模块保持一致。"""

from __future__ import annotations

import math

import pandas as pd

REASON_LABELS = {
    "listed_lt_180": "上市未满 180 天",
    "delisted": "已退市",
    "st": "名称含 ST",
    "delisting": "名称含「退」",
    "suspended": "全天停牌",
    "illiquid_history": "近 20 个交易日成交不足 10 天",
    "illiquid": "日均成交额处于后 10%",
    "missing_balance_sheet": "没有可用的资产负债表",
    "non_positive_equity": "净资产不为正",
    "goodwill": "商誉占净资产超过 30%",
    "leverage": "资产负债率超过 80%",
    "negative_deducted_profit": "最近年报扣非净利润为负",
}

FACTOR_LABELS = {
    "ep_ttm": "扣非利润 / 市值",
    "bp": "净资产 / 市值",
    "dividend_yield": "股息率",
    "roe_ttm": "扣非净资产收益率",
    "roic": "投入资本回报率",
    "gross_margin_var": "毛利率波动",
    "q_profit_yoy": "单季扣非利润同比",
    "q_sales_yoy": "单季营收同比",
    "earnings_gap": "披露后开盘跳空",
}

# 这些列在研究代码里是小数。券商原样返回的百分数不在这里。
_RATIO_FACTORS = {"ep_ttm", "bp", "dividend_yield", "roe_ttm", "q_profit_yoy", "earnings_gap"}
_REPORTED_PERCENT = {"roic", "q_sales_yoy"}
MISSING = "数据不足"


def reason_label(reason: str) -> str:
    return REASON_LABELS.get(reason, reason)


def reason_text(reasons: object) -> str:
    if reasons is None or (isinstance(reasons, float) and math.isnan(reasons)):
        return ""
    parts = [reason_label(part) for part in str(reasons).split(",") if part and part != "nan"]
    return "、".join(parts)


def format_score(value: object) -> str:
    if _missing(value):
        return MISSING
    return str(int(round(float(value) * 100)))


def format_factor(name: str, value: object) -> str:
    if _missing(value):
        return MISSING
    number = float(value)
    if name in _RATIO_FACTORS:
        return f"{number * 100:.1f}%"
    if name in _REPORTED_PERCENT:
        return f"{number:.1f}%"
    return f"{number:.2f}"


def factor_note(name: str) -> str:
    if name == "gross_margin_var":
        return "越低越好"
    return ""


def holdings_view(frame: pd.DataFrame) -> pd.DataFrame:
    """给表格用的中文列。得分是 0–100 的百分位。"""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["排名", "代码", "名称", "行业", "综合分", "质量", "价值", "成长"])
    rows = []
    ordered = frame.reset_index(drop=True)
    for index, row in ordered.iterrows():
        rows.append(
            {
                "排名": int(index) + 1,
                "代码": row.get("ts_code", ""),
                "名称": _text(row.get("name")),
                "行业": _text(row.get("industry")),
                "综合分": format_score(row.get("composite")),
                "质量": format_score(row.get("quality_score")),
                "价值": format_score(row.get("value_score")),
                "成长": format_score(row.get("growth_score")),
            }
        )
    return pd.DataFrame(rows)


def pool_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["代码", "名称", "行业", "综合分"])
    rows = [
        {
            "代码": row.get("ts_code", ""),
            "名称": _text(row.get("name")),
            "行业": _text(row.get("industry")),
            "综合分": format_score(row.get("composite")),
        }
        for _, row in frame.reset_index(drop=True).iterrows()
    ]
    return pd.DataFrame(rows)


def rejected_view(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["代码", "名称", "原因"])
    rows = [
        {
            "代码": row.get("ts_code", ""),
            "名称": _text(row.get("name")),
            "原因": reason_text(row.get("reasons")),
        }
        for _, row in frame.reset_index(drop=True).iterrows()
    ]
    return pd.DataFrame(rows)


def factor_rows(row: pd.Series) -> pd.DataFrame:
    records = []
    for name, label in FACTOR_LABELS.items():
        records.append({"指标": label, "数值": format_factor(name, row.get(name)), "说明": factor_note(name)})
    return pd.DataFrame(records)


def _text(value: object) -> str:
    if _missing(value):
        return ""
    return str(value)


def _missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False
