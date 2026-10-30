# 02：截面基本面因子工程

## 📖 【核心理论与量化直觉】

因子是可在某一时点计算、可跨股票比较的数值特征。当前仓库在通过排雷的候选池中计算九个基本面因子：

- 价值：`ep_ttm`、`bp`、`dividend_yield`；
- 质量：`roe_ttm`、`roic`、`gross_margin_var`；
- 成长：`q_profit_yoy`、`q_sales_yoy`、`earnings_gap`。

价值因子强调“为每单位现金流或净资产支付多低的价格”；质量因子强调盈利资本效率与稳定性；成长因子强调利润、营收和披露后信息。`gross_margin_var` 是唯一“越低越好”的列。

原始基本面分布通常厚尾且缺失多。`factors/processor.py` 按每个截面依次做三步。

**1. MAD 去极值。** 令中位数为 $m$，

$$
MAD=\operatorname{median}(\lvert x_i-m\rvert)
$$

截断边界为

$$
\bigl[m-k\times 1.4826\times MAD,\; m+k\times 1.4826\times MAD\bigr]
$$

当前 $k=3$。超界值被截到边界。

**2. 缺失填充。** 优先使用行业中位数，无行业或行业全缺时使用全截面中位数。

**3. Z-Score 标准化。**

$$
z_i=\frac{x_i-\mu}{\sigma}
$$

其中 $\mu$ 是截面均值，$\sigma$ 是总体标准差（`ddof=0`）。若一列标准差为零，整列为零。

标准化不是制造预测能力，它让不同单位的变量进入同一比较尺度。最终打分仍按排序进行，因此它对极端值稳定性更重要。

## ⚠️ 【实盘避坑指南】

- `total_mv` 的原始单位与财报单位必须统一。提供方在进入因子前已把市值转换为元，手工扩展因子时不要重复乘以 10,000。
- TTM、单季同比和年报利润的报告期不同；不能把季度累计值直接当单季值。
- 行业中位数填充降低缺失损失，也可能把行业分类的当前信息带入过去。当前行业字段存在轻微前视风险，应在严谨研究中使用历史行业归属。
- MAD 为零时，说明大部分观测相同；强行除以零或用极小数代替会制造伪差异。
- 缺失本身可能有信息。这里选择填充是为了构建可交易的综合分，研究新的因子时应先统计缺失率和缺失的行业分布。
- 因子定义、单位、方向和可得日必须一起版本化。只保存最终综合分，无法复盘异常排名。

## 💻 【仓库代码实战导引】

相关模块：

- [factors/fundamental/value.py](../../factors/fundamental/value.py)：价值因子。
- [factors/fundamental/quality.py](../../factors/fundamental/quality.py)：质量因子。
- [factors/fundamental/growth.py](../../factors/fundamental/growth.py)：成长因子与披露后跳空。
- [factors/processor.py](../../factors/processor.py)：`winsorize_mad`、`fill_industry_median`、`zscore`、`prepare_factors`。
- [tests/test_factors.py](../../tests/test_factors.py)、[tests/test_processor.py](../../tests/test_processor.py)：因子口径和预处理的离线验证。

直接运行：

```bash
pytest -q tests/test_factors.py tests/test_processor.py
python -m strategy.scorer --asof 20240630 --top 8
```

第二条命令会经 `strategy.scorer.assemble_factors` 调用三类因子函数。若要观察包含原始因子值的完整矩阵，使用快照命令：

```bash
python -m strategy.publish --asof 20240630 --top 8
```

输出目录的 `scores.parquet` 保存原始因子列和得分列，避免把已 Z-Score 化的内部值误当成财务原值。

## 📊 【实验复现与指标预期】

特征矩阵每行是一只通过排雷的股票，至少包含 `ts_code`、`industry`、九个因子和后续得分列。预处理后：

- 有效因子的 Z-Score 截面均值应接近 0；
- 非常数列的总体标准差应接近 1；
- `gross_margin_var` 的数值较小代表更好，进入排序时方向会翻转；
- 原始因子存在缺失是正常的，预处理的目标是让可用因子参与本截面比较。

不要把某个 Z-Score 大于 2 解释为“买入信号”。它只表示相对同日候选池偏高。真正要验证的是未来收益或 Rank IC，并且必须使用走到当时才可得的因子快照。

## 🧠 【本章实操挑战题】

1. 为 `winsorize_mad` 写一个包含一个极端值的测试，比较 `width=3` 与 `width=5` 的截断边界。确认缺失值仍是缺失值。
2. 选择一个现有因子，在 `scores.parquet` 中统计行业维度的缺失率；提出是否应该保留行业中位数填充的理由，并用测试固定你的选择。
