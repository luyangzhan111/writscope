# S3 提案结构错误：离线复现与 v2 修订

日期：2026-10-26。用户授权先离线处理提案结构错误。本步**新增真实请求0次**；原同轮14次请求、失败、评分、费用和未运行项保持不变，无新预算申请。

## 复现与原因

先以[原同轮入口](../scripts/s3_common_round.py)独立回放[原运行](../reports/local/s3-common-real-20261026/run.json)，再取c002 `job_005` 的五份原提取响应。前四事件接受，e005仍在原 `TransitionProposal` 形状检查失败，复现[原记录](s3-common-round-real-review.md)。

失败响应的grant同时包含非空target_ids和authorization.supersedes，后续还有一条没有替代内容的supersede。旧模型可见的 `ExtractionPayload` 只独立列出operation、目标和两种可空载荷，未在JSON结构中表达这些组合限制；因此原v1 `RESPONSE_ADAPTER`接受该响应，后续内核才拒绝。

这是可验证的结构描述缺口；不能据此确定模型为什么选错，也不等于原状态内核放行了错误。本步保持原内核严格拒绝，不以宽松解析或自动清空字段修复真实响应。

## 修订内容

新增[通用v2提示词](../prompts/incremental_interaction_v2.md)与[按操作分型的契约](../scripts/s3_interaction_v2.py)，规则见[规格](../specs/interaction-quote-v2.md)：

- grant必须是空目标列表、新授权、null约束，授权supersedes为空；约束关系只写constraint_refs。
- 替代约束/授权分别需要同类型目标和替代内容，supersedes与目标逐项一致；不允许裸supersede。
- 撤销、标歧义和no_change各自有明确形状；no_change只能单独出现，变更批次最多八项。
- v2形状校验通过后，把同一原始响应交给v1引文/来源/回查/身份检查，继续使用未改动的范围与原子状态内核。没有隐式重试或响应修复。

新提示词只有通用规则，没有c002答案、参考状态、期望三轴或固定案例ID。完整可见原文和公开证据不变。v2配置/请求哈希不同，不能直接喂给旧冻结运行器或复用旧授权。

当前DeepSeek适配器的输出是JSON对象模式，schema随请求传入；新schema不构成provider严格结构保证。**真实模型是否更少犯错尚未测量**。

## 已执行离线诊断

[诊断入口](../scripts/s3_proposal_shape_diagnosis.py)已prepare后另行verify成功，保存于[诊断报告](../reports/local/s3-proposal-shape-v2-20261026/audit.json)和[运行清单](../reports/local/s3-proposal-shape-v2-20261026/run.json)。每次复验先重验原同轮，再从公开输入和原响应重建全部诊断。

| 项目 | 本步实际结果 | 证据含义 |
| --- | --- | --- |
| 原v1模型结构检查失败响应 | 结构适配器接受，后续提案内核拒绝 | 重现描述与执行规则之间的缺口 |
| 原v1 c002完整保存提取前缀 | 接受4事件、e005失败 | 原失败未被改写 |
| v2复用c001五份原提取响应 | 5事件均接受 | 旧文本在新离线解析/配置下的兼容诊断，非新模型成绩 |
| v2复用c002五份原提取响应 | 接受4事件、e005前置形状拒绝；状态与背景均未推进 | 未自动修复非法响应 |
| 显式人工结构控制 | 合法形状提交第5事件，原约束完整保留 | 确认合法grant仍可构造，不证明语义正确或模型会输出 |
| 三历史×五前缀传输预览 | 15份请求构造并解析；最大27975字节 | 全部为no_change合成状态，只验证截止点与传输结构 |

人工控制另存于诊断字段 `synthetic_control`：清空两个错误列表并去掉未执行的裸supersede。它保持原conditions等字段，没有补充候选条件或作语义裁决；不能当参考正确状态、真实重试或反事实终点判断，也没有送给模型。

## 验证与保留边界

[新增测试](../tests/test_s3_interaction_v2.py)20项，加原交互契约6项，共 **26项通过**。覆盖七种合法形状、必填null、错误grant/裸替代/混用/重复目标、JSON Schema可见限制、来源与唯一引文、未来回查及额度、请求公开输入一致、原失败复现、状态不推进、跨版本范围拒绝、人工控制限制和重写哈希后的篡改拒绝。

诊断不读取 `.env`、参考标签或人工状态。Windows原同轮回放需要本地asyncio通信，命令在获准的本地环境运行；没有构造真实provider或产生新费用。原冻结源码、提示词、授权及原始工件哈希保持一致。

当前完成的是**v2操作形状、提示词和离线诊断**，尚未接入真实运行入口；完整九任务、独立审核和自主回查状态不变。下一小步可先接入新的版本化离线同轮运行器，补齐成功与首次故障停止的保存回放；准备好新预检后再另行决定真实重测。

无网络复验：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_proposal_shape_diagnosis verify --output reports/local/s3-proposal-shape-v2-20261026
```
