"""只读研究结果页。运行：streamlit run web/app.py"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from labels import FACTOR_LABELS, MISSING, factor_rows, format_score, holdings_view, pool_view, reason_label, rejected_view
from load import Snapshot, filter_rows, outside_holdings, visible_snapshots


def _render_holdings(snapshot: Snapshot) -> None:
    holdings = snapshot.holdings
    top_n = int(snapshot.summary.get("top_n") or len(holdings) or 8)
    kept = int(snapshot.summary.get("kept") or 0)
    first, second, third = st.columns(3)
    first.metric("通过排雷", f"{kept} 只")
    second.metric("列出前几名", f"{min(top_n, len(holdings))} 只")
    leader = ""
    if not holdings.empty and "composite" in holdings.columns:
        leader = format_score(holdings.iloc[0].get("composite"))
    third.metric("最高综合分", leader or MISSING)

    if holdings.empty:
        st.info("这一天没有选出股票。")
        return

    view = holdings_view(holdings)
    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button(
        "下载候选名单",
        data=view.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"holdings-{snapshot.asof}.csv",
        mime="text/csv",
    )

    options = holdings["ts_code"].astype(str).tolist()
    names = holdings["name"].fillna("").astype(str).tolist() if "name" in holdings.columns else [""] * len(options)
    choice = st.selectbox(
        "看一只股票的财务数字",
        options=options,
        format_func=lambda code: _choice_label(code, options, names),
    )
    row = holdings.loc[holdings["ts_code"].astype(str).eq(choice)].iloc[0]
    quality, value, growth = st.columns(3)
    quality.metric("质量", format_score(row.get("quality_score")))
    value.metric("价值", format_score(row.get("value_score")))
    growth.metric("成长", format_score(row.get("growth_score")))
    st.dataframe(factor_rows(row), width="stretch", hide_index=True)
    st.caption("综合分是质量、价值、成长三块百分位的加权平均。毛利率波动越低，质量分越高。")


def _render_rejections(snapshot: Snapshot) -> None:
    kept = int(snapshot.summary.get("kept") or 0)
    rejected_count = int(snapshot.summary.get("rejected") or len(snapshot.rejected))
    left, right = st.columns(2)
    left.metric("留下", f"{kept} 只")
    right.metric("剔除", f"{rejected_count} 只")

    reasons = snapshot.summary.get("reasons") or {}
    chart = pd.DataFrame(
        {"原因": [reason_label(key) for key in reasons], "家数": [int(reasons[key]) for key in reasons]}
    )
    chart = chart.loc[chart["家数"].gt(0)]
    if chart.empty:
        st.caption("这一天没有记录到剔除原因。")
    else:
        st.bar_chart(chart, x="原因", y="家数")

    query = st.text_input("按代码或名称查找", placeholder="例如 000001 或公司简称")
    rejected = filter_rows(snapshot.rejected, query)
    pool = filter_rows(outside_holdings(snapshot), query)
    if query.strip():
        st.subheader("被剔除")
        if rejected.empty:
            st.caption("没有匹配的剔除记录。")
        else:
            st.dataframe(rejected_view(rejected), width="stretch", hide_index=True)
    else:
        st.caption("输入代码或名称后，可以看到每只股票命中的全部原因。")

    top_n = int(snapshot.summary.get("top_n") or 8)
    st.subheader("通过了排雷，没有进入前列")
    st.caption(f"这些股票留在池子里。综合分只列出最高的 {top_n} 只作为候选。")
    if pool.empty:
        st.caption("没有落在候选名单之外的股票。")
    else:
        st.dataframe(pool_view(pool), width="stretch", hide_index=True)


def _render_about(snapshot: Snapshot) -> None:
    weights = snapshot.summary.get("weights") or {"quality": 0.4, "value": 0.3, "growth": 0.3}
    quality = int(round(float(weights.get("quality", 0.4)) * 100))
    value = int(round(float(weights.get("value", 0.3)) * 100))
    growth = int(round(float(weights.get("growth", 0.3)) * 100))
    st.markdown(
        f"""
综合分按 **质量 {quality}%、价值 {value}%、成长 {growth}%** 加权。每一块先在当天的股票池里做百分位，再取平均。

财务数字要等到实际公告日才使用。报告期结束、或者交易所规定的披露截止日，都不会提前当成已经公布。

页面上的利润和净资产除以截面日总市值，不用数据源里现成的市盈率和市净率。股息率用过去一年已经实施的现金分红。毛利率波动看最近已公告的毛利率有多散，越稳越好。

行业中位数用的是当前行业分类。拿它去填更早的截面时，行业变更会带来一点偏差。

净利润断层没有使用一致预期：最近 120 天内最新一份已公告财报，只有单季扣非同比为正时，才记录披露日后第一个交易日的开盘跳空。

数据只到前一个交易日，页面不提供实时行情，也不下单。
"""
    )
    st.caption("原始指标：" + "、".join(FACTOR_LABELS.values()))


def _choice_label(code: str, options: list[str], names: list[str]) -> str:
    name = names[options.index(code)] if code in options else ""
    return f"{code}  {name}".strip()


st.set_page_config(page_title="研究记录", layout="wide")
st.title("研究记录")
st.caption("这是按公告日整理的研究记录，不是买卖建议。")

snapshots = visible_snapshots()
if not snapshots:
    st.info(
        "还没有可展示的截面。在本机运行 python -m strategy.publish --asof YYYYMMDD --top 8，"
        "把 web/snapshots 里的新日期推送到仓库后，这里会出现结果。"
    )
    st.stop()

selected_labels = {item.asof: item.label for item in snapshots}
selected = st.sidebar.selectbox(
    "截面日",
    options=[item.asof for item in snapshots],
    format_func=lambda key: selected_labels[key],
    index=len(snapshots) - 1,
)
snapshot = next(item for item in snapshots if item.asof == selected)
if snapshot.sample:
    st.warning("当前是示例数据，用来看页面长什么样。发布真实截面后，这个日期会从列表里消失。")

holding_tab, reject_tab, about_tab = st.tabs(["候选持仓", "为什么没入选", "这些数字怎么来的"])
with holding_tab:
    _render_holdings(snapshot)
with reject_tab:
    _render_rejections(snapshot)
with about_tab:
    _render_about(snapshot)
