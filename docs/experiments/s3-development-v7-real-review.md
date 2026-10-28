# S3 开发验收 v7 c004 真实诊断记录

日期：2026-10-26。用户批准一次 c004 真实诊断、本地上限 3.00 元及计划列明的公开材料外发范围；授权仅用于该哈希绑定的一次运行，已消费。本记录不扩展到补跑、其他案例或方法比较。

## 运行及回查

- 冻结计划文件 SHA-256：`bb104db31a623025accfcabb9739b80e09752ff74f1eb4a053facd6ea54dc57f`；运行前复核计划/源码、一次性授权收据及 DeepSeek 官方价格。价格页列示 `deepseek-flash`（DeepSeek-V4.1-Flash-1017）峰时未缓存输入 $0.30/百万 token、输出 $1.20/百万 token；本地预留按保守 10 元/美元换算，并非供应商账单上限。[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)。
- 共 7 次真实请求：5 次逐事件提取、1 次自主历史回查续答、1 次终点复核。e001–e005 共 5 个事件均被接受。模型在 e005 请求 e002；运行器只返回该时点可见的原始工具事件，续答被严格状态入口接受。
- 回查报告里的候选摘要为 `21ddfd5f…af54b55`，与当前候选 `3bfe2099…d00455e` 不同；模型将条件记为 `unsatisfied`，授权未覆盖当前候选。未通过提示、范围或状态内核放宽来消除这一失败。
- 终点返回三轴 `breaking / not_covered / revise`，但最终 JSON 未通过引用校验，不能作为有效终点或记入三轴成绩。其 `per_impact_authorization[].source_refs` 同时引用 e005 用户授权和 e002 工具报告；校验器要求该字段只引用用户事件，因此拒绝：`only a user source can authorize a change`。e002 作为条件事实来源可放在顶层 `source_refs`，但不能作为授权来源。保存运行器的 `error` 摘要为 null；对原响应的离线重新校验恢复了上述错误原因。原响应仍保留原样。

## 费用与回放

| 项目 | 记录 |
| --- | ---: |
| 实际请求 / 上限 | 7 / 8 |
| 本地预留 | 1.121982 元 |
| 峰时保守估算 | 0.175377 元 |
| 提供商实际账单 | unknown，7 次均无账单金额 |
| 输入 / 输出 token | 50,003 / 2,114 |
| 推理 token | unknown，7 次均未提供 |
| 有效终点 | 0/1 |

[原始真实轨迹](../../reports/local/s3-development-v7-live-20261026)经 v7 冻结入口回放通过；[机器报告](../../reports/local/s3-development-v7-live-report-20261026/report.md)保留原状态 `s3_development_exit=not_satisfied`、`s3_full_exit=not_satisfied`。授权已用，不补跑。

终点拒绝原因由[只读离线诊断](../../reports/local/s3-development-v7-postrun-diagnosis-20261026/diagnostic.json)重新校验原响应得到；诊断只补充原因和阶段分类，不改原 `calls.json`、`jobs.json`、`run.json` 或机器报告。

## 结论及后续

本次**验证了模型自主选择回查、指定原文返回和续答接受**，但没有有效终点，所以开发验收仍未通过。机器字段 `lookup_evidence=failed_or_incomplete_preserved` 将部分真实回查链合并归类；旁路诊断另行记录阶段事实，不覆盖机器原值。

离线根因是终点契约将逐影响授权来源限制为用户消息，但旧提示未解释工具事实应引用在顶层来源字段，导致工具事件引用进入授权字段。下一离线修订应给终点提示版本化地说明“权限来源”与“事实来源”字段边界；旧提示、真实响应和报告不改。新的真实尝试须另建并冻结入口、重新核价并取得新预算和外发授权。

独立审核者仍未提供实际审核响应，因此完整研究验收仍不能自签；S4正式比较也未进行。
