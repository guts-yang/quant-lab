# DCF 估值方法论

`valuation/` 包复刻 AlphaSpread 的 DCF 框架并适配 A 股。本文档记录方法论来源、公式、
适配差异和参数口径。实现入口见文末「代码地图」。

## 1. AlphaSpread 的做法（公开资料核实）

来源：AlphaSpread 帮助页（What is DCF value）、DCF Calculator 页、2026-11 抓取的
NVDA / SAP / TGT / KO / META / MS / GPT 等股票的 DCF 页面，以及第三方评测
（CompleteTraderEdge 2026、DayTradeReview、TIKR 博客）。

- **模板选择。** 每家公司按特征选 FCFE / Net Income / FCFF 模板。NVDA、SAP、TGT、
  META 用 FCFE；GPT（REIT）用 Net Income；退出倍数多数是 P/S，部分公司（KO、MS）用 P/E。
- **收入预测。** 三股信息收敛：历史回归外推、行业基准（base rates）、分析师一致预期。
  近期年份锚定一致预期，远期向行业基准收敛。页面展示的单个增速是预测期平均值——
  NVDA 基准 24.4%，低于 5 年历史 66.9% 和华尔街 5 年 41.8%（基准情景刻意保守）。
- **净利率路径。** 从当前水平线性过渡到终值水平，如 NVDA 54.4% → 49.7%（7 年）。
- **现金流转换。** FCFE = 净利润 × 转换率，来自历史 FCF/NI（NVDA 91%）。
- **折现率。** FCFE 模板用股权成本（CAPM：rf + β×ERP），FCFF 模板用 WACC；反映
  无风险利率、资本结构和风险溢价（NVDA 10.3%）。
- **终值。** 退出倍数法：NVDA 7.4x P/S，KO 20.3x P/E，折现回现值。
- **情景与反向 DCF。** 熊/基准/牛三情景改增速、利润率、折现率和倍数；反向 DCF 从
  市价反解隐含预期。Intrinsic Value = 基准 DCF 与相对估值（可比公司倍数）的平均，
  每月末更新。

## 2. 公式骨架（FCFE 模板，N 年预测期）

```text
g_t    = growth_start + (growth_terminal − growth_start) × (t−1)/(N−1)   # 线性收敛
Rev_t  = Rev_{t−1} × (1 + g_t)
m_t    = margin_start + (margin_terminal − margin_start) × t/N           # 第 N 年恰好到位
FCFE_t = Rev_t × m_t × conversion
TV     = Rev_N × P/S_exit   或   NI_N × P/E_exit   或   FCFE_N×(1+g∞)/(r−g∞)
V/share = [Σ_t FCFE_t/(1+r)^t  +  TV/(1+r)^N] / shares
```

反向 DCF：固定其他假设，二分求解使 V/share = 市价的 growth_start。
敏感性：每股价值对 growth_start × discount_rate 的矩阵。

## 3. 本实现的假设推导规则（基准情景）

规则全部显式写在 `valuation/assumptions.py`，缺失输入按「公司历史 → 行业基准 →
保守常数」的顺序回退：

| 输入 | 规则 |
| --- | --- |
| growth_start | min(公司 5 年收入 CAGR, 行业中位 TTM 增速 × 1.5)，夹取 [−20%, 50%]；有一致预期时 50/50 混合 |
| growth_terminal | `terminal_growth`（默认 2.5%），且不超过折现率 −1% |
| margin_start | TTM 净利率 → 5 年合计净利率 → 行业中位 → 10% |
| margin_terminal | min(当前, max(公司 5 年合计净利率, 行业中位))；不用行业分位封顶，行业 P25/P75 只作熊/牛情景锚 |
| conversion | 近 5 个盈利年报年度 FCF/归母净利的中位数，夹取 [0.5, 1.2]，缺省 0.9 |
| 折现率 | rf + β×ERP，β 为个股对沪深 300 的 104 周收益回归（Blume 0.67β+0.33，夹取 [0.5, 2.5]），结果夹取 [6%, 15%] |
| 退出倍数 | 行业 P/S（金融业 P/E）中位数；无行业基准时 P/S=2.0、P/E=12.0（文档化常数，不从市价反推） |
| 模板 | 金融业（comp_type 2/3/4：银行/保险/证券）用 Net Income（转换率=1、P/E 退出），其余 FCFE |

熊市：增速 ×0.6、终值净利率取行业 P25（无行业则 ×0.8）、折现率 +150bp、退出倍数取
行业 P25。牛市：增速 ×1.25（封顶 50%）、终值净利率取行业 P75、折现率 −100bp、退出
倍数取行业 P75。

## 4. A 股适配差异

- **无免费一致预期。** 券商网关没有分析师一致预期接口。`EstimatesProvider` 协议 +
  `FileEstimatesProvider`（手工 JSON）预留了接入；缺省时回退历史 + 行业基准，
  这正是 AlphaSpread 在缺少预期时的做法。后续可接付费源实现该协议。
- **报表是年内累计值（YTD）。** TTM = 本季 YTD + 上年年报 − 去年同期 YTD；年报
  （12-31）的 YTD 即全年值。换算在 `valuation/data.py`。
- **公告日对齐。** 只用 asof 前实际公告（f_ann_date 优先）的合并报表
  （report_type ∈ {1, 5}），复用 `core.pit.visible_history`，与排雷口径一致。
- **股本由市值/收盘价推。** daily_basic 的 total_mv = close × 总股本，反推精确且
  不引入新的缓存目录。
- **rf / ERP 参数化。** 网关没有国债收益率接口，rf 默认 1.8%（10Y 国债量级）、
  ERP 默认 5.5%（Damodaran 中国口径），CLI 可覆盖。
- **beta 自算。** 个股对沪深 300 周收益回归，个股日线按代码+年份缓存
  （`data/cache/ohlc_code/`），避免为单票估值拉全市场日线。

## 5. 数据源清单

| 数据 | 接口 | 缓存 |
| --- | --- | --- |
| 利润表（total_revenue, n_income_attr_p, basic_eps） | income_vip / income | `data/cache/income/` |
| 现金流量表（n_cashflow_act, c_pay_acq_const_fiolta） | cashflow_vip / cashflow | `data/cache/cashflow/` |
| 行业与上市状态 | stock_basic | `data/cache/stock_basic.parquet` |
| 市值与收盘价 | daily_basic | `data/cache/daily_basic/` |
| 个股日线（beta） | daily（按代码） | `data/cache/ohlc_code/` |
| 沪深 300（beta 基准） | index_daily | `data/cache/index_daily/` |

## 6. 代码地图

| 文件 | 职责 |
| --- | --- |
| `valuation/model.py` | 纯函数引擎：预测路径、折现、终值（倍数/Gordon）、反向 DCF、敏感性 |
| `valuation/discount.py` | 周收益 beta 回归、Blume 调整、CAPM 股权成本 |
| `valuation/assumptions.py` | 三情景假设推导、模板选择、`EstimatesProvider` 接口 |
| `valuation/data.py` | 公告日对齐、YTD→TTM 换算、行业分位数基准 |
| `valuation/scenario.py` | 编排与产物（assumptions.json / forecast / scenarios / sensitivity / report.md） |
| `valuation/run.py` | CLI |

```bash
python -m valuation.run --code 600519.SH --asof 20261110 [--rf 0.018] [--estimates exp.json]
```

产物写入 `data/research/valuation_<code>/`。测试 `tests/test_valuation.py` 用合成
数据，不访问网络。

## 7. 已知局限

- 退出倍数法是终值的强假设，基准情景同时可用 Gordon 法交叉校验
  （`terminal_method="gordon"`）；终值占股权价值比例会打印在 report.md 里。
- 行业分位数需要同行业至少 5 家上市公司，否则回退到文档化常数。
- TTM 对「去年同期 YTD 缺失」的公司（次新股）返回缺失，不做估算。
- 相对估值与「DCF + 相对估值平均」的综合内在价值尚未实现，是后续阶段。
