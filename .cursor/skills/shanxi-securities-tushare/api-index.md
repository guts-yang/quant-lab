# 接口索引

名称与 Tushare Pro 相同，由 `sxsc_tushare` 发到山西证券 `prd` 网关。下面是本仓库会用到、并且已经调通过的接口。不在表里的名字先查券商文档 `http://221.204.19.233:7173/document/2`，不要凭记忆新造。

| 用途 | api_name | 备注 |
| --- | --- | --- |
| 股票列表 | `stock_basic` | `list_status` 用 L/D/P |
| 公司资料 | `stock_company` | |
| 曾用名 | `namechange` | provider 无第二条路径 |
| 交易日历 | `trade_cal` | `exchange=SSE` |
| 日线 | `daily` | |
| 周线 / 月线 | `weekly` / `monthly` | |
| 复权因子 | `adj_factor` | |
| 每日指标 | `daily_basic` | 收盘价、总市值 |
| 停复牌 | `suspend_d` | provider 无第二条路径 |
| 涨跌停价 | `stk_limit` | |
| 分红 | `dividend` | provider 无第二条路径 |
| 利润表 | `income` / `income_vip` | 先 vip |
| 资产负债表 | `balancesheet` / `balancesheet_vip` | 先 vip |
| 现金流量表 | `cashflow` / `cashflow_vip` | 先 vip |
| 财务指标 | `fina_indicator` / `fina_indicator_vip` | 先 vip |
| 业绩预告 / 快报 | `forecast` / `express` | |
| 披露计划 | `disclosure_date` | |
| 指数列表 / 日线 / 权重 | `index_basic` / `index_daily` / `index_weight` | 权重无第二条路径 |
| 基金列表 / 净值 / 分红 | `fund_basic` / `fund_nav` / `fund_div` | |
| 期货合约 / 日线 | `fut_basic` / `fut_daily` | |
| 期权合约 | `opt_basic` | |
| 港股列表 / 日线 | `hk_basic` / `hk_daily` | |
| 资金流 / 两融 / 龙虎榜 | `moneyflow` / `margin` / `top_list` | |
| 沪深股通十大成交 | `hsgt_top10` | |
| CPI / GDP / Shibor | `cn_cpi` / `cn_gdp` / `shibor` | |

`stk_mins` 在券商网关是「未知的数据源」。`rt_k` 和 `realtime_quote` 不存在。`moneyflow_hsgt` 能调用，但是可能返回空表。

公开版 `TUSHARE_TOKEN` 不要拿来调这些 vip 财务接口。
