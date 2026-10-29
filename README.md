# quant-lab

A 股截面选股。做到取数、排雷、打分和展示。没有回测，也不下单。

## 已实现

- **取数。** 山西证券网关的 T-1 历史，客户端是 `sxsc_tushare`。Token 用 `SXSC_TUSHARE_TOKEN`，本机 `env=prd`，只放 `.env`。公开版 token 不能打这个网关。结果缓存在 `data/cache/`。
- **排雷。** 财务数字按实际公告日进入股票池：优先 `f_ann_date`，否则 `ann_date`。去掉次新、已退市、ST、全天停牌、成交不足、没有可用资产负债表、净资产不为正、商誉过高、负债率过高、最近年报扣非为负。名称用 `namechange` 在截面日的简称。阈值在 `CleanerConfig`。
- **打分。** 只在排雷后的股票里做。质量 0.4（扣非 ROE、ROIC、毛利率波动），价值 0.3（扣非利润/市值、净资产/市值、股息率），成长 0.3（单季扣非同比、单季营收同比、披露后开盘跳空）。毛利率波动越低越好。EP、BP 用截面日总市值。取出综合分最高的 5–8 只。
- **展示。** `strategy.publish` 把一天的结果写进 `web/snapshots/`。Streamlit 只读这些文件，不读 token。有真实快照后，示例日不再出现。

## 还没有

`backtest/` 回测和 `ptrade/` 下单都是空目录。终端手册在 `docs/ptrade/`。数据 SDK 在 `docs/tushare/`。

## 命令

```bash
pip install -e ".[dev,web]"
python -m core.universe --asof YYYYMMDD
python -m strategy.scorer --asof YYYYMMDD --top 8
python -m strategy.publish --asof YYYYMMDD --top 8
streamlit run web/app.py
```

Cloud 入口是 `web/app.py`，依赖用 `requirements.txt`，不配置 Secrets。`pytest` 使用合成数据，不访问网络。
