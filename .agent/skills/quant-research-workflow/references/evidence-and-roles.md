# 证据、成员交接与结论

## 本次运行证据

每项正式研究使用独立运行目录和固定参数快照；不可复用其他任务的状态文件、审批或完成回执。遵从活动专家团 runtime 的正式证据合约和守卫工具，不自行发明命令格式或手工伪造其产物。

标准/审计版的阶段证据至少包括：

- `00_intake`：请求与授权范围、参数快照、环境预检、技能/依赖哈希；本地复现股票池与平台股票池分别登记。
- `01_source_replication`：来源清单、复现摘要、公式、数据调用回执、真实紧凑回测、质量门和成员交接。
- `02_factor_candidates`：候选 JSONL、候选评审记录和成员交接。
- `03_platform_preflight`：脱敏的平台预检/余额回执、批准快照、成员交接。
- `04_platform_execution`：run ID、原始返回、结果汇总和成员交接。
- `05_statistical_audit`：`selected_returns.csv`、`trials_matrix.csv`、`overfit_report.json` 和成员交接。
- `06_tearsheet`：以相同审计收益生成的 tearsheet JSON/HTML 和成员交接。
- `07_final_review`：专家交接汇总、最终报告、各阶段封存回执及 `completion_receipt.json`。

具体文件名、字段及命令标签以当前专家团的 evidence contract 为准。`finalize` 退出码为 0 并生成完成回执，才允许称正式研究“已完成”。封存后改动任一证据，应重新校验相关阶段。

## 子 Agent 隔离与交接

每次调用只传递阶段任务包：目标、封存输入的相对路径和 SHA-256、必需输出、约束。不得传递整个会话、其他成员的 Skill 正文或秘密凭证。交接至少包含成员身份、唯一绑定 Skill、阶段、真实调用 ID、隔离标志、结论、保留意见和证据文件哈希。

没有真实独立成员调用能力时，不得声称五位专家都给出意见。若仅由主 Agent完成分析，应明确说“主 Agent 分析”，将多成员完成状态判为阻断，而不是角色扮演补齐调用。

## 收益与审计口径

- PandaAI 的逐股 `factor_result` 是因子值，不是策略收益，不得直接送入过拟合审计或 tearsheet。
- 过拟合审计使用真实回测净收益和所有实际尝试配置的收益矩阵；时间戳须对齐，样本与成本口径一致。标准/审计门槛以当前审计 Skill 为准，专家团当前基线要求至少 30 期和至少 10 条真实试验。
- `selected_returns.csv` 使用审计 Skill 约定的 `date,return` 字段；同一文件用于 tearsheet，不能替换为更漂亮的曲线。
- 本地 quant-lab 回测的 `equity` 序列与专家团 PandaData/PandaAI 结果分开归档，除非明确标为单独实验并说明两者口径差异。

## 最终状态

- `FAST_VALIDATED`：只限快速版的真实来源/数据/紧凑回测有效，不代表通过平台运行、过拟合审计或样本外。
- `PROMOTE_TO_OOS`：仅限标准/审计版所有必需阶段完成且统计审计通过；含义是允许继续独立样本外研究，不是策略推荐或未来收益保证。
- `RESEARCH_REJECTED`：核心质量门、候选验证或过拟合审计失败；保留实际失败证据。
- `WAITING_APPROVAL`：候选和参数可供审阅，但尚未获得当前收费批次的明确批准。
- `BLOCKED` / `BLOCKED_EXTERNAL`：宿主能力、数据、认证、预算或必需证据不足；逐项说明阻断原因。

## 外部专业 Skill 边界

本项目包只提供编排、路由和证据纪律，不复制或替代上游专业实现。按需要加载且遵守当前上游版本：

- `skill-report-replication`
- `skill-factor-mining-pandaai`
- `skill-pandaai-factor-online`
- `skill-backtest-overfit`
- `skill-strategy-tearsheet-report`

上游 Skill 的工具命令、字段契约、版本和许可各自为准；缺失或不可用时如实阻断。不得用本文件中的摘要推断未登记接口或参数。
