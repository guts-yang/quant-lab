# 缠论日线波段研究报告

## 结论

`reject_current_definition`

- 锁定样本外最大回撤超过预先设定门槛。
- 成本占平均资产比例超过预先设定门槛，或成本结果缺失。

## 研究规格

```json
{
  "development": {
    "name": "development",
    "start": "20180101",
    "end": "20211231"
  },
  "validation": {
    "name": "validation",
    "start": "20220101",
    "end": "20231231"
  },
  "holdout": {
    "name": "holdout",
    "start": "20240101",
    "end": "20251231"
  },
  "capital": 40000.0,
  "max_positions": 5,
  "costs": {
    "commission_rate": 0.00025,
    "min_commission": 5.0,
    "stamp_tax": 0.0005,
    "slippage": 0.001
  },
  "benchmark_code": "000300.SH",
  "chan_core": "1.2.2",
  "bi_standard": "81",
  "price_return_only": true,
  "min_holdout_months": 12,
  "max_holdout_drawdown": -0.2,
  "max_cost_to_average_equity": 0.05,
  "min_holdout_sells": 5,
  "max_winner_share": 0.8
}
```

## 策略、沪深300与同池等权

```csv
period,strategy_cagr,strategy_max_drawdown,strategy_sharpe,strategy_calmar,benchmark_cagr,benchmark_max_drawdown,benchmark_sharpe,benchmark_calmar,equal_weight_cagr,equal_weight_max_drawdown,equal_weight_sharpe,equal_weight_calmar,trades,sells,annualized_turnover,estimated_cost_to_average_equity,winner_share,strategy_end,equal_weight_end
development,-0.11192250769835854,-0.4757899912396769,-0.6347940365479787,-0.23523510321590207,0.04859150009058988,-0.32461968881054826,0.3410040074496847,0.14968747049396758,-0.03142834953008733,-0.18390175730167502,-0.4762361299611104,-0.17089749435363918,555,274,35.27428808493665,0.2662245116226326,0.31715048135787444,24896.925070955775,35119.71000000001
validation,-0.11498500293285785,-0.22113371078634247,-1.2650662446538294,-0.51997952968806,-0.1660660712807811,-0.32947141662087864,-0.9996673237615907,-0.5040378706717148,0.010904517207065867,-0.08844280424155448,0.19348784843386221,0.12329456647805413,273,137,25.800165282079963,0.1022410536512058,0.2804637470029433,31398.24108308203,40764.42999999999
holdout,-0.02668857731408203,-0.23925311312844044,-0.09368988549268958,-0.1115495508714846,0.1696646151997767,-0.15663546326795907,0.9716582819726862,1.0831813668500372,0.05822678049532071,-0.07027481697918503,0.7148574034917247,0.8285582659371012,427,220,44.95037808762644,0.16763155245412223,0.2459356228780357,37897.61495304849,44649.02600000001
```

## 已知缺口

- 买卖点使用 chan-lun-core 笔级简化口径，不是第 101 课的精确二类点。
- 价格收益不含复权、分红和公司行动。
- 涨跌停按前收 10% 判断，没有覆盖 20% 和 ST 板块，也没有建模排队成交。
