# M3 终点模型复核输入输出草案

状态：**M3m 已实现离线请求、脚本响应校验与回放；真实模型复核未接入、未调用**。本契约用于审查 M3 后续完整增量方法，不改变 M3c–M3l 的历史提取或结构性 `assessment.json`。

## 触发与输入

只有逐事件提取到达 `decision_point`、全部转换被接受、状态可独立回放，运行器才构造一次终点模型请求。提取失败、预算耗尽或状态不完整时记录 `no_final_review`，不把缺失状态默认为未授权，也不额外补一次免费复核。若策略需要回查，先冻结调用/工具预算和停止规则。

M3m 离线请求已由运行器从白名单材料构造；真实 provider 接入仍待实现：

| 字段 | 内容与来源 |
| --- | --- |
| `case_id`, `decision_point`, `evidence_mode` | 已核验公开 `ReviewInput`；时点严格截止到目标事件 |
| `candidate_patch`, `snapshot`, `contract_refs` | 与完整上下文基线相同的公开候选、基线快照和契约引用，保留内容/哈希绑定 |
| `impact_evidence` | A 轨的同一 `ImpactEvidence`，含每项影响、观察与局限；B 轨不提供 A 的影响结论，改用同等公开工具与证据权限 |
| `current_state` | 从空状态逐事件更新并回放成功的 `AuthorizationState`；包含约束、授权、局部例外、撤销/替代关系、来源和 `state_hash`；状态属方法生成，不是人工正确状态 |
| `task_context` | 仅来自截止点前可信用户消息并带原文来源；M3l 已将 M3i 原文账本接入人工计划轨迹，尚未接入正式模型方法。未接通时标为 `not_available`，不得从评分答案补齐 |
| `history_lookup` | M3m 附上 M3l 人工触发且按时点截断的已发生回查记录；模型自主提出回查尚未实现。完整历史仍在公开输入中，后续模型调用与结果 token/费用须另计 |

初始请求不附 `evaluation_private`、参考标签/状态、隐藏测试、兼容参考修复或设计文档中的答案。`current_state` 不能替代原始证据：模型可回查可见原文；运行器保存回查引用与状态来源。完整上下文两基线继续收到完整历史和相同基础证据，不因本方法缩减输入。

## 模型输出与程序验证

计划复用现有 `source_quote_v1` 的三轴 `ReviewResult` 语义：`impact`、`authorization`、`decision`、`affected_subjects`、`per_impact_authorization`、约束/授权/事件/影响证据引用、理由和局限。模型提交的事件引用仅含 `{event_id, quote}`，运行器仅从可见原文唯一精确匹配后派生位置；模型不生成角色、序号、字符跨度、`state_hash` 或费用。

每个破坏影响须有一条授权覆盖判断；最终组合遵守 [数据契约](data-contracts.md)的 `ReviewResult` 规则。`impact=breaking` 不会因授权变为 `compatible`；明确覆盖可 `allow`，明确不覆盖为 `revise`，歧义为 `ask`，影响未知或证据不足为 `investigate`。程序校验字段、来源时点、引用存在性、状态/证据哈希及组合规则；这些结构检查**不证明模型的语义判断正确**。模型输出与确定性 `ScopeAssessment` 分开保存，不用后者偷偷填补或修正模型三轴。

## 运行记录和费用边界

M3m 离线入口独立保存 `request.json`、可选 `response.txt`、`call.json`、有效时 `result.json`，以及来源轨迹、请求、公开证据、提示词、模型配置和输出契约哈希；失败保留原始响应与错误，不生成有效 `ReviewResult`。真实入口预计将这些文件放入 `final_review/`。完整成本从首条历史事件起计入提取、状态更新、回查、终点复核、验证和全部失败尝试；缺失 token 或账单写 `unknown`。M3h 的五次提取草案费用上限**不覆盖**终点复核或回查。真实终点模型请求、预算分配及与两基线共享的报告适配尚待单独实现和冻结。
