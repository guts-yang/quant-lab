# 01：点时数据与 A 股硬性排雷

## 📖 【核心理论与量化直觉】

横截面策略的问题不是“今天能看到什么数据”，而是“在历史中的当日，交易者当时能看到什么数据”。这叫 Point-in-Time（PIT）约束。对一条财报记录，仓库采用可得日：

$$
\text{available\_date}=
\begin{cases}
\text{f\_ann\_date}, & \text{若 f\_ann\_date 存在} \\
\text{ann\_date}, & \text{否则}
\end{cases}
$$

在截面日 $t$ 只保留 $\text{available\_date}\le t$ 的记录，并从同一股票的可见报告中选择最新报告期。`core/pit.py` 的 `latest_visible` 实现这条边界；它防止把日后才披露的年报带回过去。

股票池不是收益来源，却决定尾部风险。`UniverseCleaner` 先删除结构上不适合小资金持有的对象：上市不足 180 日、退市、ST/退市风险、全天停牌、流动性历史不足、流动性后 10%、不可用资产负债表、非正净资产、商誉/净资产过高、负债/资产过高和最近年报扣非利润为负。排雷是可解释的布尔规则，拒绝原因按 `REASON_ORDER` 留痕。

流动性判断使用截面日前的最近 20 个交易日。$\mathcal{D}_{i,t}$ 是其中股票 $i$ 有成交额的日期集合，$A_{i,d}$ 是成交额：

$$
\bar{A}_i=\frac{1}{N_i}\sum_{d\in\mathcal{D}_{i,t}}A_{i,d},\quad N_i=\lvert\mathcal{D}_{i,t}\rvert\ge 10
$$

合格股票中，$\bar{A}_i$ 位于最低 10% 的股票被剔除。这里使用成交额，不把价格高低误当成可交易性。

## ⚠️ 【实盘避坑指南】

- 公告日不是报告期。2024 年报的 `end_date=20241231` 不表示 2024 年 12 月已经可交易。
- 只使用今天的股票列表会产生幸存者偏差。退市股、历史名称和停牌记录也必须按照截面日判断。
- `stock_basic` 的当前名称不能代表历史名称；ST 判断需要使用 `namechange` 在截面日有效的简称。
- 交易日的当日成交额不能拿来在当日开盘建仓。当前清洗器严格使用 `trade_date < asof`。
- 财务类公司与非金融公司杠杆结构不同。`CleanerConfig.apply_leverage_filter_to_financials` 用于显式决定是否把同一杠杆门槛施加于金融业。
- 阈值是风险预算，不是免费收益。反复调整 180 天、10%、30% 等数字直到回测最好，是过拟合。

## 💻 【仓库代码实战导引】

主要入口：

- [core/pit.py](../../core/pit.py)：`latest_visible`、`visible_history`，负责按可得日截取历史。
- [core/cleaner.py](../../core/cleaner.py)：`CleanerConfig`、`UniverseCleaner.filter`，负责生成 `kept` 与 `rejected`。
- [core/universe.py](../../core/universe.py)：`build_universe`，负责把提供方数据拼接后交给清洗器。
- [tests/test_pit.py](../../tests/test_pit.py)、[tests/test_cleaner.py](../../tests/test_cleaner.py)：不联网的边界样例。

先运行单元测试，确认时间边界和拒绝原因没有被后续重构破坏：

```bash
pytest -q tests/test_pit.py tests/test_cleaner.py
```

配置好本地数据凭据后，构建真实截面：

```bash
python -m core.universe --asof 20240630
```

查看最小调用形态时，输入表至少应覆盖 `instruments` 的 `ts_code/name/list_date/delist_date`、`bars` 的 `trade_date/amount`，以及资产负债表和指标中的公告日字段。清洗器只吃表，便于用 CSV、Parquet 或合成 DataFrame 重现问题。

## 📊 【实验复现与指标预期】

`UniverseCleaner.filter` 返回 `CleanResult`：

- `kept`：`ts_code`、`name`、`list_date`、`delist_date`；
- `rejected`：相同标识列加 `reasons`，例如 `st,leverage`；
- `reasons` 的顺序稳定，适合快照、回归测试和排错。

正常情况不是“保留越多越好”。应重点检查：

1. 每只被拒绝的股票都有至少一个原因；
2. 在 `asof` 之后公告的报告不会影响结果；
3. 流动性窗口里没有当日数据；
4. 同一天重复运行得到相同的 `ts_code` 集合。

若真实数据中突然出现大面积 `missing_balance_sheet` 或 `illiquid_history`，优先检查数据接口、缓存日期和公告日期解析，不要先放宽阈值。

## 🧠 【本章实操挑战题】

1. 将 `CleanerConfig.liquidity_quantile` 临时改为 `0.20`，为合成输入补一条刚好位于分位边界的股票。验证 `rank(method="min", pct=True)` 对并列成交额的处理，再恢复默认参数。
2. 在 `tests/test_pit.py` 增加一条记录：报告期较新但公告日晚于 `asof`。先观察错误实现会选中它，再用 `latest_visible` 验证该记录被排除。
