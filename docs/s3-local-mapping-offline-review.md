# S3 局部映射主体离线复现与摘要透传修订

日期：2026-10-26。已用原保存响应复现 c001 e001 失败，另立摘要适配层及离线诊断入口。**新增真实请求 0 次，未改提示词、旧响应、范围规则、冻结运行器或原评分。**

## 主体与局部映射复现

输入为 [v2 原真实记录](../reports/local/s3-real-v2-live-20261026/run.json)与其 [冻结预检](../reports/local/s3-real-v2-preflight-20261026-final/plan.json)。先调用原版 verify 独立重建全部保存请求、响应、状态和费用，再用新适配层消费相同响应；不读取私有标签、密钥或人工正确状态。

原第三次请求的第二项提案（索引 1）为 grant：引文“可以在客户端本地映射成页面需要的 state。”；授权主体为 `GET /api/v1/profile`、v1、`response.body`、`field=state`，但旧字段为 status，并引用 status 约束。条件中虽有 client-side local mapping only，主体仍被表示为服务端 HTTP 响应字段；局部客户端映射许可不能据此被当成服务端字段变更授权。

原 `scope_ref_v2_offline` 规则再次给出同一拒绝：

`authorization subject field differs from old_field; constraint reference scope mismatch: c001-constraint-v1-profile-status`

批次失败项仍为 1，接受事件仍为 0，首项约束也未部分提交。范围拒绝是可复现的程序结果；局部映射与服务端授权的区别是对公开原文的作者诊断，不是独立金标准或新增模型输出。仅把 field 改成 status 也不能证明语义正确，本步没有制作或运行这种修补响应，没有声称模型问题已解决。

## 单独修订摘要错误透传

问题位于旧 Cursor.consume：probe 已持有转换失败原因，但随后取对应 core 调用的 `error=null`，又用它覆盖外层 failure。原 calls.json、jobs.json 与根 run.json 的空摘要全部保留。

[新摘要适配层](../scripts/s3_failure_summary_v1.py)继承原 Cursor，先完整执行原消费逻辑，仅在 incremental 调用为 state_rejected 且缺失 error 时补原因。必须以 core_call_id 和事件序号唯一关联同一步；batch_rejected 要求 accepted=false 且转换 error 与批次 rejection_reason 一致，context_rejected 使用匹配转换的明确 error。已有原因优先保留，证据缺失、矛盾、重复或错位时不补。它只返回和透传摘要，不修改 core 响应、提案、状态转换、停止条件或费用。

[新离线入口](../scripts/s3_local_mapping_diagnosis.py)只有 run/verify，无真实执行、预算申请、补跑或修复命令。它将补齐原因后的 call/job/root 摘要写入**新诊断文件**，保留 raw_error/raw_failure 和原记录哈希；不覆盖旧文件。每条原请求逐一比对，所有原状态、结果、背景、回查、费用和任务状态必须保持一致。此适配层尚未接入任何新付费运行器，未来接入需另立版本并重新冻结；不修改已授权计划的源码绑定。

## 验证与产物

- [10 项定向测试](../tests/test_s3_local_mapping_diagnosis.py)通过：原入口空摘要复现、新摘要正确传递、原子拒绝/空状态保持、错误调用或事件不串用、重复/缺失/矛盾记录不猜测、已有错误保留、背景拒绝及 provider_error 保持、禁止 provider/私有标签读取、源工件不变、保存后重建与篡改拒绝。
- [保存诊断](../reports/local/s3-local-mapping-diagnosis-20261026/diagnostic.json)首次执行及独立 verify 均通过：原结果仍为 stopped_on_failure，2 完成/1 失败/6 未运行，新增真实请求 0；补齐摘要不是新方法成绩，metrics=null。
- [证据清单](../reports/local/s3-local-mapping-evidence-20261026/evidence.json)绑定新源码、测试、诊断、原运行和既有冻结工件，原费用仍为预留 0.416688 元、估算 0.080841 元、实付 unknown。

下一步如继续改善提取，应先设计通用的“客户端局部映射与服务端变更授权”表示及离线控制，保留反例与原范围拒绝；本步未变更模型契约或提示词。真实九任务完整覆盖、自主回查及独立审核仍缺，S3 未完整签收，本轮授权仍已用。

## 既有证据核对差异

六份既有证据清单的54项工件中，53项字节哈希一致。唯一差异为 docs/s3-v2-real-round-review.md：原收尾期望 b37356d952982d98518d1de0417726bb21dc698e6c1e8a3e40a9dd7fc102721d，当前 aa9d71d976869fe046c2394ea90fb3f1c6b7e8434959f823561479059bd940e2。本步没有编辑该正文，不推定差异产生者或时间，不恢复覆盖、不更新原哈希；新证据清单记录该差异。原运行JSON、调用响应、报告生成器输出、授权、源码及其余冻结工件均匹配，因此原记录可按冻结入口独立重建；不能将人工报告正文称为原收尾字节一致副本。
