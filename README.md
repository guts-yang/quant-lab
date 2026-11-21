# quant-lab

A 股研究记录。从 T-1 数据取出截面，完成排雷、打分、回测和 DCF 估值，并可以把排名清单交给 PTrade。研究进程不下单。

![中国移动 DCF 估值](docs/assets/china-mobile-dcf-20261110.svg)

上图是中国移动（600941.SH）在 2026-11-10 的股权现金流估值，数字来自 [`web/valuations/600941.SH_20261110`](web/valuations/600941.SH_20261110)。现价 101.55 元。熊市每股价值 92.35 元，比现价低 9.1%；牛市每股价值 306.70 元，比现价高 202.0%。基准情景的起始增速仍待审核，正式每股价值留空，图里也不补一根基准柱。

## 中国移动这张图怎么来的

估值只用已经公告的财务报表。TTM 收入 10,358.2 亿，归母净利 1,279.4 亿，FCF 1,140.3 亿，净利率 12.4%，近 5 年现金转换率中位数 92.7%。基准折现率 6.00%，由无风险利率 1.80%、β 0.50 和 5.5% 股权风险溢价算出，并落在 6%–15% 的夹取区间里。

起始增速等于股权再投资率乘以 ROE。配对 ROE 是 10.0%，但净资本开支、运营资本变动和净债务发行还不齐，再投资率无法计算。模型记下本公司 5 年收入复合增速 6.5% 作为建议兜底；写入审核文件之前，这个数不进入基准估值。熊市和牛市使用已经公告的历史增速区间，所以这两列有每股价值。牛市终值占股权价值约 85%，读 306.70 元时要连同永续假设一起看。

口径写在 [DCF 估值说明](docs/valuation/dcf_methodology.md)。重新计算一只股票：

```bash
python -m valuation.run --code 600941.SH --asof YYYYMMDD
```

## 研究流程

- **取数。** `sxsc_tushare` 读取山西证券网关的 T-1 历史，结果缓存在 `data/cache/`。Token 用 `SXSC_TUSHARE_TOKEN`，本机 `env=prd`，只放 `.env`。公开版 token 不能打这个网关。
- **排雷。** 财务数字按实际公告日进入股票池：优先 `f_ann_date`，否则 `ann_date`。去掉上市未满 180 天、已退市、ST、名称含「退」、全天停牌、成交不足、没有可用资产负债表、净资产不为正、商誉过高、负债率过高、最近年报扣非为负的股票。名称用截面日的 `namechange`。
- **打分。** 只在排雷后的股票里做截面百分位。质量 40%（扣非 ROE、ROIC、毛利率波动），价值 30%（扣非利润 / 总市值、净资产 / 总市值、股息率），成长 30%（单季扣非同比、单季营收同比、披露后开盘跳空）。毛利率波动越低越好。默认取出综合分最高的 5–8 只。
- **回测。** 月末调仓，跌出前 15 名卖出。日频使用 8% 硬止损和 2 倍 ATR 移动止损。沪深 300 跌破 20 日或 60 日均线时总仓位降到 50%，两条都跌破降到 30%。初始资金 4 万元，计入卖出印花税、最低 5 元佣金和买卖滑点。
- **展示。** `strategy.publish` 把一天的结果写进 `web/snapshots/`。Streamlit 只读已发布文件，不读 token。估值、回测、研究结论和交易清单在同一页按目录显示。
- **交给 PTrade。** `ptrade/export_book.py` 写出排名清单。`fundamental_low_turnover.py` 在客户端里做整手调仓、止损和涨跌停保护；`hs300_tenths_ladder.py` 只交易沪深 300 ETF（510300.SS）。终端手册在 [`docs/ptrade/`](docs/ptrade/)。

## 命令

```bash
pip install -e ".[dev,web]"
python -m core.universe --asof YYYYMMDD
python -m strategy.scorer --asof YYYYMMDD --top 8
python -m strategy.publish --asof YYYYMMDD --top 8
python -m backtest.engine --start YYYYMMDD --end YYYYMMDD
python -m valuation.run --code 600941.SH --asof YYYYMMDD
python -m ptrade.export_book --asof YYYYMMDD --top 8 --out fundamental_book.json
streamlit run web/app.py
```

Cloud 入口是 `web/app.py`，依赖用 `requirements.txt`，不配置 Secrets。`pytest` 使用合成数据，不访问网络。

上海代码在研究侧是 `.SH`，进 PTrade 后改成 `.SS`。日线 `handle_data` 在盘后触发，实盘委托可能到下一交易日才成交。
