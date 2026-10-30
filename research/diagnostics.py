"""因子诊断：冻结信号之后生成收益标签，再计算 Rank IC 与分组单调性。"""

from __future__ import annotations

import pandas as pd

from strategy.scorer import FACTOR_COLUMNS

SCORE_COLUMNS = [*FACTOR_COLUMNS, "quality_score", "value_score", "growth_score", "composite"]


def forward_labels(
    snapshots: dict[pd.Timestamp, pd.DataFrame],
    bars: pd.DataFrame,
    holding_sessions: int,
) -> pd.DataFrame:
    """使用信号日之后第一个交易日入场，持有指定交易日数的价格收益标签。"""
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["asof", "ts_code", "entry_date", "exit_date", "forward_return"])
    prices = bars.copy()
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce").dt.normalize()
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    matrix = prices.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    sessions = list(matrix.index)
    labels: list[pd.DataFrame] = []
    for asof, scores in snapshots.items():
        asof = pd.Timestamp(asof).normalize()
        positions = [index for index, date in enumerate(sessions) if date > asof]
        if not positions:
            continue
        entry_pos = positions[0]
        exit_pos = entry_pos + holding_sessions
        if exit_pos >= len(sessions):
            continue
        entry_date, exit_date = sessions[entry_pos], sessions[exit_pos]
        frame = scores.copy()
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["entry_price"] = frame["ts_code"].map(matrix.loc[entry_date])
        frame["exit_price"] = frame["ts_code"].map(matrix.loc[exit_date])
        frame["forward_return"] = frame["exit_price"] / frame["entry_price"] - 1.0
        frame["asof"] = asof
        frame["entry_date"] = entry_date
        frame["exit_date"] = exit_date
        labels.append(frame)
    if not labels:
        return pd.DataFrame(columns=["asof", "ts_code", "entry_date", "exit_date", "forward_return"])
    return pd.concat(labels, ignore_index=True)


def rank_ic(labels: pd.DataFrame, columns: list[str] = SCORE_COLUMNS) -> pd.DataFrame:
    """每个截面对未来收益做 Spearman 秩相关。缺失或常数截面保留为 NaN。"""
    records: list[dict[str, object]] = []
    if labels.empty:
        return pd.DataFrame(columns=["asof", "factor", "rank_ic", "observations"])
    for asof, frame in labels.groupby("asof", sort=True):
        target = pd.to_numeric(frame["forward_return"], errors="coerce")
        for column in columns:
            if column not in frame:
                continue
            signal = pd.to_numeric(frame[column], errors="coerce")
            usable = signal.notna() & target.notna()
            value = signal.loc[usable].corr(target.loc[usable], method="spearman") if usable.sum() >= 2 else float("nan")
            records.append({"asof": asof, "factor": column, "rank_ic": value, "observations": int(usable.sum())})
    return pd.DataFrame(records)


def ic_summary(ic: pd.DataFrame) -> pd.DataFrame:
    if ic.empty:
        return pd.DataFrame(columns=["factor", "mean_rank_ic", "std_rank_ic", "positive_month_ratio", "months"])
    grouped = ic.groupby("factor", sort=True)["rank_ic"]
    return grouped.agg(
        mean_rank_ic="mean",
        std_rank_ic="std",
        positive_month_ratio=lambda values: (values > 0).mean(),
        months="count",
    ).reset_index()


def quantile_returns(labels: pd.DataFrame, factor: str = "composite", quantiles: int = 5) -> pd.DataFrame:
    """每月按因子等数量分组，输出各组未来等权收益和最高减最低。"""
    records: list[dict[str, object]] = []
    if factor not in labels or labels.empty:
        return pd.DataFrame(columns=["asof", "quantile", "return", "observations"])
    for asof, frame in labels.groupby("asof", sort=True):
        usable = frame[[factor, "forward_return"]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(usable) < quantiles:
            continue
        ranks = usable[factor].rank(method="first")
        buckets = pd.qcut(ranks, q=quantiles, labels=False, duplicates="drop") + 1
        for bucket, values in usable.groupby(buckets, sort=True):
            records.append(
                {
                    "asof": asof,
                    "quantile": int(bucket),
                    "return": float(values["forward_return"].mean()),
                    "observations": int(len(values)),
                }
            )
    output = pd.DataFrame(records, columns=["asof", "quantile", "return", "observations"])
    if output.empty:
        return output
    spread = output.groupby("asof", sort=True)["return"].agg(lambda values: values.iloc[-1] - values.iloc[0])
    return output.merge(spread.rename("top_minus_bottom"), on="asof", how="left")
