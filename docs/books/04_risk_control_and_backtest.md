# 04：风控、交易摩擦与本地回测

## 📖 【核心理论与量化直觉】

选股回答“买什么”，风控回答“买多少、何时退出”。当前组合在每个自然月最后一个交易日重新打分：已持仓跌出前 15 名则卖出，前 5–8 名中的新股票买入，仍在前 15 名的持仓留在组合中。这个缓冲带把信号不稳定性转化为较低换手。

止损由硬止损和 ATR 移动止损共同决定。真实波幅：

$$
TR_t=\max\bigl(H_t-L_t,\;\lvert H_t-C_{t-1}\rvert,\;\lvert L_t-C_{t-1}\rvert\bigr)
$$

14 日 ATR：

$$
ATR_t=\frac{1}{14}\sum_{j=0}^{13}TR_{t-j}
$$

持仓的有效止损线为：

$$
\mathrm{stop}=\max\bigl(\mathrm{cost}\times(1-0.08),\;\mathrm{peak}-2\times ATR_{t-1}\bigr)
$$

其中 `peak` 是入场以来的历史最高价；回测仅使用前一日可得的 ATR 处理当日止损，避免拿当天收盘价修正当天早盘决策。若开盘直接低于止损线，按开盘价成交，体现跳空风险。

系统风险由沪深 300 均线控制：收盘低于 20 日或 60 日均线时总仓位为 50%，两条都低于时为 30%，否则满仓。再按持仓数等权分配目标价值，并按 100 股整手取整。

交易摩擦定义为：

$$
\mathrm{commission}=\max\bigl(5,\;0.00025\times\lvert\mathrm{turnover}\rvert\bigr)
$$

卖出额外扣除 $0.0005\times\mathrm{turnover}$ 印花税。买入成交价为 $P(1+0.001)$，卖出成交价为 $P(1-0.001)$。4 万元账户下，最低 5 元佣金使频繁小单尤其昂贵。

绩效指标：

$$
\mathrm{CAGR}=\left(\frac{V_T}{V_0}\right)^{365.25/\mathrm{days}}-1
$$

$$
\mathrm{MaxDD}=\min_t\left(\frac{V_t}{\max_{s\le t}V_s}-1\right)
$$

$$
\mathrm{Sharpe}=\sqrt{252}\,\frac{\operatorname{mean}(r_t)}{\operatorname{std}(r_t)},\qquad
\mathrm{Calmar}=\frac{\mathrm{CAGR}}{\lvert\mathrm{MaxDD}\rvert}
$$

## ⚠️ 【实盘避坑指南】

- 日 K 的最低价触及止损，不表示能以止损价成交。回测已处理开盘跳空，仍没有建模涨跌停、停牌和排队成交。
- 佣金最低收费必须按每笔订单计算；把多笔订单先净额汇总，会系统性低估小账户成本。
- ATR 在数据不足 14 日时不可用，当前实现此时只使用 8% 硬止损。
- 同日流程是先检查止损、后月末调仓。颠倒顺序会让已经触发的持仓被错误地继续持有。
- 指数均线是仓位开关，不是预测模型。它可能在震荡市频繁降仓，必须在不同市场状态下观察收益损失和回撤改善。
- 卡玛比率分母是最大回撤绝对值。零回撤曲线的卡玛没有定义，不能把它记成无穷大后与真实策略比较。
- 当前回测没有分红复权、融资利息、容量约束和涨跌停成交模型；结果用于筛查规则，不用于直接承诺实盘收益。

## 💻 【仓库代码实战导引】

核心实现：

- [strategy/risk_control.py](../../strategy/risk_control.py)：`month_end_book`、`average_true_range`、`stop_price`、`stop_fill`、`index_exposure`。
- [backtest/engine.py](../../backtest/engine.py)：`commission`、`buy_cash`、`sell_cash`、`simulate`、`run_backtest`。
- [core/metrics.py](../../core/metrics.py)：`cagr`、`max_drawdown`、`sharpe`、`calmar`、`performance`。
- [tests/test_risk.py](../../tests/test_risk.py)、[tests/test_engine.py](../../tests/test_engine.py)、[tests/test_metrics.py](../../tests/test_metrics.py)：合成行情下的精确断言。

先离线复现交易成本和止损：

```bash
pytest -q tests/test_risk.py tests/test_engine.py tests/test_metrics.py
```

配置好历史数据后执行完整回测：

```bash
python -m backtest.engine --start 20200101 --end 20241231 --capital 40000 --top 8
```

`simulate` 是纯函数，可接收合成 OHLC、指数收盘和 `scores_at` 回调。`run_backtest` 才负责通过提供方取得 OHLC、沪深 300 和月末候选分数。

## 📊 【实验复现与指标预期】

回测命令输出以下格式：

```text
start=YYYYMMDD end=YYYYMMDD cagr=... max_drawdown=... sharpe=... calmar=...
```

`BacktestResult` 还包含：

- `equity`：每日 `trade_date/equity` 净值曲线；
- `trades`：交易日、股票、方向、股数、未滑点原始价格和 `stop` 或 `rebalance` 原因；
- `metrics`：四项浮点绩效。

合成测试中应确认：1,000 元成交额的佣金是 5 元；40,000 元成交额的佣金是 10 元；成本约 10.01 元的持仓跌至止损线时会被平仓；净值从 100 跌到 80 的最大回撤为 -20%。

目标“年化约 10%–20%、最大回撤低于 20%”只能作为跨样本的风险预算。更有意义的检查是：样本外最大回撤可接受、卡玛持续为正且高于基准、交易次数与佣金占成交额没有失控。

## 🧠 【本章实操挑战题】

1. 把 `ATR_WINDOW` 临时设为 20，为合成价格补足 20 天历史，验证 ATR 从第 20 个观测才出现；再确认引擎仍只用前一日 ATR。
2. 为 `simulate` 设计一个月末候选池更换案例，比较“前 15 名缓冲”和“跌出前 8 名立即卖出”的交易次数、佣金与最大回撤。将新规则保留在测试或实验分支，避免未经验证地改生产默认值。
