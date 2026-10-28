# S3 v2 离线同轮集成验收

日期：2026-10-26。用户要求把v2接入新的离线同轮运行器。本步已完成独立v2轨迹、九任务入口、故障停止、报告和保存回放；**新增真实请求0次，没有读取模型密钥或申请新预算**。原14请求真实同轮、失败、评分及其他历史记录不变。

## 实现与接线

[新轨迹内核](../../../scripts/s3_scripted_trajectory_v2.py)从原M3p脚本流程另立版本，接入v2请求构造、输出结构、提示词、解析及配置hash。来源校验、唯一引文、原子状态更新、局部例外、回查截止点与限额保持原规则，最终仍用原 `source_quote_v1` 复核契约。没有修改或临时替换v1模块。

[新离线同轮入口](../../../scripts/s3_common_offline_v2.py)固定三历史×三方法、R=1；两基线保持原提示词和完整公开证据，各增量轨从空状态开始。流程为：

1. 冻结公开输入、九任务/首次请求/脚本和组件hash。
2. 每事件使用v2请求，脚本返回提取或可选回查；回查原文进入续答，只有背景与原子批次均接受才提交事件。
3. 五事件完成后才调用脚本终点；结构、状态引用与来源校验通过才生成有效终点。
4. 任一任务故障即停止全组，后续任务保留not_run；四阶段合成用量和模拟预留包含失败尝试。
5. 保存全部逐任务工件与九行共同报告，再从固定来源独立重建，核对数据和hash。

边界见[离线同轮契约](../../../specs/s3-common-offline-v2.md)。入口只有run/verify，模型名称仅供序列化请求，无real/score命令；报告不读取标签，metrics保持null。

## 成功与故障演练

以下四份最终记录均执行run后另行verify成功，均为脚本控制，**真实模型请求均为0**：

| 演练 | 脚本尝试 | 实际离线结果 | 保存记录 |
| --- | ---: | --- | --- |
| 完整回查链 | 30 | 九任务及三个增量终点有效，每条增量轨两次回查/续答 | [运行](../../../reports/local/s3-v2-round-lookup-20261026-final/run.json) / [报告](../../../reports/local/s3-v2-round-lookup-20261026-final/report.md) |
| 完整不回查链 | 24 | 九任务完成，三条增量轨均无回查 | [运行](../../../reports/local/s3-v2-round-no-lookup-20261026-final/run.json) / [报告](../../../reports/local/s3-v2-round-no-lookup-20261026-final/report.md) |
| c002非法grant | 18 | job_005/e005形状拒绝，四后续任务未运行，无c002终点 | [运行](../../../reports/local/s3-v2-round-bad-grant-20261026-final/run.json) / [报告](../../../reports/local/s3-v2-round-bad-grant-20261026-final/report.md) |
| 首次provider错误 | 1 | 首任务停止，其余八项未运行 | [运行](../../../reports/local/s3-v2-round-first-error-20261026-final/run.json) / [报告](../../../reports/local/s3-v2-round-first-error-20261026-final/report.md) |

完整回查演练中，c002合成脚本先建立两条约束，再提交一条合法授权，生成**一个关联两条约束的局部例外**；两条约束均保持active，授权没有删除宽范围限制。该脚本由公开材料构造，未读取作者标签，也不是原真实失败响应的自动修补。

每条增量轨均保存state、task_context、steps、lookup_history、ledger和可选final_request/final_result。模型可见提取/续答请求均标记 `interaction_quote_v2`，终点和两基线仍为 `source_quote_v1`。逐步请求保留完整当前可见原文，基础公开证据/工具权限hash与原两基线一致，首次请求hash与plan核对。

上述终点只是合成控制输出的结构性验收，不能当真实语义预测或正确率。脚本回查全部记 `offline_transport_only`；不回查演练也不变成真实模型的未触发证据。

## 测试与可复现性

[新集成测试](../../../tests/test_s3_common_offline_v2.py)13项、操作形状v2测试20项、原交互契约回归6项，共 **39项通过**。覆盖：九任务/30尝试、c002合法局部授权、四阶段账表、无回查24尝试、非法grant、裸替代、未来回查、跨版本范围拒绝、缺用量、provider错误、终点错误、全组停止、无真实provider/参考读取、没有real命令及重写hash后的篡改拒绝。

首轮测试有一处断言把“一例外关联两约束”误写成“两条例外”；按实际数据契约修正为一个例外及两条constraint_ids后，39项回归通过，没有为通过测试修改状态内核。中间演练目录保留；本次最终工件均使用 `-final` 后缀。

根plan绑定内核、v2契约、旧范围/来源组件、提示词与环境；各job先保存后独立复验，根汇总/报告再重建比对。所有真实价格、预算、新授权和实际provider用量为空；模拟金额代理仅用于接线对账，不是新的人民币付费上限。

## 已完成与下一边界

已完成v2的**离线完整轨迹及九任务同轮集成**，并验证成功、失败停止、保存回放和共同报告。旧真实记录哈希仍保持不变。

仍未实现v2真实provider入口及新付费预检，没有测量新提示词的模型合法率，也未补齐真实九任务覆盖、独立审核或自主回查。下一步可先准备独立的v2真实入口和无网络闸门/故障验证，具体核价与预算申请在可审查准备完成后进行，不能沿用旧12元授权。

复验命令（不调用模型）：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_common_offline_v2 verify --output reports/local/s3-v2-round-lookup-20261026-final
.\.venv\Scripts\python.exe -m scripts.s3_common_offline_v2 verify --output reports/local/s3-v2-round-bad-grant-20261026-final --profile bad_grant
```
