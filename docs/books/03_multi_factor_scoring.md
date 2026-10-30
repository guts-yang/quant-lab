# 03：多因子打分、检验与 Top-N 组合

## 📖 【核心理论与量化直觉】

不同因子单位不同，也可能只在部分股票上有效。当前实现不直接相加 Z-Score，而是先做截面百分位排名。对高值更优的因子：

$$
r_i=\frac{\operatorname{rank}(x_i)}{N}
$$

对低值更优的因子使用 $-x_i$ 后再排名。每类因子取可用排名的均值：

$$
S_{\mathrm{group},i}=\operatorname{mean}_{j=1,\ldots,k}(r_{i,j})
$$

最终综合分为：

$$
S_i=0.4\,S_{\mathrm{quality},i}+0.3\,S_{\mathrm{value},i}+0.3\,S_{\mathrm{growth},i}
$$

权重位于 `strategy/scorer.py` 的 `QUALITY_WEIGHT`、`VALUE_WEIGHT`、`GROWTH_WEIGHT`。同分时按 `ts_code` 升序，确保回测与网页快照可复现。`select_holdings` 限制 `top_n` 为 5–8，符合小资金集中组合的风险预算。

因子有效性常用 Rank IC 评价。$f_{i,t}$ 是股票 $i$ 在 $t$ 的因子值，$r_{i,t+h}$ 是其后持有期收益：

$$
\mathrm{RankIC}_t=\operatorname{Spearman}_i(f_{i,t}, r_{i,t+h})
$$

目前仓库尚未实现批量 Rank IC 脚本。它是下一层研究工具，用于评估因子而不是替代当前的选股和回测路径。

## ⚠️ 【实盘避坑指南】

- 在全市场排名再排雷，会让不可交易股票参与分位数并污染信号。当前顺序是先排雷、后打分。
- 回测时按最终 Top-N 重复调参，再声称 Rank IC 很高，属于数据窥探。IC 检验与参数选择需要样本内/样本外分割。
- IC 均值为正仍可能不可交易：换手、成本、停牌、涨跌停和行业暴露都可能吃掉收益。
- 同一财报数据同时构成价值、质量与成长因子时，三个组不是独立信号。权重之和为 1 不等于风险已经分散。
- 小资金 5–8 只股票不应按“每月一定换满”理解。风控模块允许排名仍在前 15 的旧持仓保留，减少无意义换手。
- 仓库没有 `screener.py`。不要复制外部教程中同名模块的调用；当前筛选职责由 `strategy/scorer.py` 和 `strategy/publish.py` 承担。

## 💻 【仓库代码实战导引】

阅读和调用顺序：

- [strategy/scorer.py](../../strategy/scorer.py)：`assemble_factors` 组装特征，`score_cross_section` 产生完整排序，`score_candidates` 为回测提供候选池，`select_holdings` 截取 Top-N。
- [strategy/publish.py](../../strategy/publish.py)：`build_frames` 保留完整得分和原始因子，`publish` 写入可展示快照。
- [tests/test_scorer.py](../../tests/test_scorer.py)：权重、方向、并列代码和 Top-N 边界。

```bash
pytest -q tests/test_scorer.py
python -m strategy.scorer --asof 20240630 --top 8
python -m strategy.publish --asof 20240630 --top 8
```

`strategy.scorer` 控制台会输出 `asof`、实际选中数量，以及 `ts_code`、`composite`、`quality_score`、`value_score`、`growth_score`。`publish` 会生成 `holdings.parquet`、`scores.parquet`、`rejected.parquet` 和 `summary.json`。

## 📊 【实验复现与指标预期】

正常的评分输出具备以下性质：

- `scores.parquet` 是完整候选池，不止 Top-N；
- `holdings.parquet` 最多 8 行，候选数不足时按实际数返回；
- 三个分组得分和 `composite` 都在 0–1 的排名尺度附近，而不是原始财务单位；
- 同分排序稳定，重复运行同一快照不会改变持仓顺序。

Rank IC 的经验判断必须克制：月频基本面因子的单期 IC 往往波动很大；持续为正、跨市场状态稳定、且扣除换手成本后仍存收益，才值得进入组合。单个窗口的高 IC 或高年化都不足以证明稳健。

## 🧠 【本章实操挑战题】

1. 在 `tests/test_scorer.py` 增加并列 `composite` 的案例，验证 `ts_code` 升序是唯一的打破并列规则。
2. 用历史快照设计一个离线 Rank IC 实验：将每月 `scores.parquet` 的 `composite` 与下月收益对齐，计算 Spearman 相关。先只写测试数据和接口，再决定是否新建研究脚本；不要把未来收益合并进当前 `score_cross_section`。
