---
name: quant-research-workflow
description: "Use for quant-lab research tasks such as reproducing reports or papers, designing A-share factors, running this repository's strategy backtests, auditing overfitting, or producing performance reports. Route requests to the explicit PandaData research lane or the separate Shanxi Securities sxsc_tushare project-data lane; never mix their data, evidence, or credentials."
agent_created: true
---

# Quant Research Workflow

## Purpose

将 quant-lab 的本地策略研究与量化研究专家团的正式研究流程统一编排，同时把两条金融数据链路严格分开。适用于研报/论文复现、因子设计与筛选、项目策略回测、过拟合审计和绩效报告；不适用于下单或投资建议。

## 先判断调用路径

1. 先按 `references/data-source-routing.md` 判断请求属于 PandaData 专家团研究链路，还是山西证券本地项目链路。
2. 数据源由用户明确指定时遵从指定来源；没有指定但请求正式专家团复现、因子实跑或统计审计时，走 PandaData 链路。请求执行 quant-lab 自身的 `strategy` 或 `backtest` 时，走山西证券本地链路。
3. 请求同时覆盖两条链路时，为两者创建独立的参数、数据回执和结果，不合并原始数据、收益序列、凭证或证据。若用户要的是一条合并序列，先澄清对齐口径与目的。
4. 不以本技能代替上游五个专业 Skill 或独立 Agent。按可用的专家团路由真实调用成员；无法获得独立上下文、真实调用 ID、持久化运行目录或所需数据/工具时，标记阻断，不模拟成员、不伪造回执。

## 研究原则

- 将用户要求和研究范围固定为可复核参数：来源、股票池、起止日期、调仓周期、成本、样本外区间和预算。
- 默认使用 `standard`。只读预检先行；任何外部收费运行前，展示候选、公式摘要、方向、股票池、起止日期、调仓、预计成本、总预算、样本外方案和候选文件 SHA-256，并取得用户对当前批次的明确批准。
- 仅用本次运行的真实来源、数据、回测收益与调用回执作定量结论。合成测试数据仅用于软件测试，不构成研究证据。
- 将逐股因子值与策略收益/净值区分。过拟合审计和绩效报告只使用真实策略回测收益，且两者必须使用同一份序列。
- 不跳阶段、不改写失败结果。最终校验未通过时，只报告已完成阶段、阻断阶段、实际错误、缺失证据和下一步。

## 参考流程与职责

标准流程、三档深度及五个专业角色见 `references/research-workflow.md`；证据字段、交接和结论规则见 `references/evidence-and-roles.md`。使用前按需读取这些文件，并遵守上游技能和宿主工具的最新契约。

## quant-lab 本地实现入口

- 本地数据采集：遵循 `.agent/skills/shanxi-securities-tushare/SKILL.md`；不得在本技能中另写 SDK、缓存或令牌读取路径。
- 本地截面分析：沿用 `core.tushare_provider.TushareProvider`、`strategy.scorer` 等已有链路。
- 本地组合回测：入口为 `python -m backtest.engine --start YYYYMMDD --end YYYYMMDD`；其参数、假设、交易成本和结果必须从本次实际运行中读取，不从 README 或本技能抄成“实测结果”。
- `tests/` 中的合成数据只用于验证代码，不得用于声称策略有效。

## 交付格式

先给状态与结论，再列数据源/股票池/时间范围/调仓/成本口径、核心实测结果、风险限制和证据位置。专家团研究须列出真实调用成员与保留意见；本地链路则明确标注“quant-lab / 山西证券 sxsc_tushare”。
