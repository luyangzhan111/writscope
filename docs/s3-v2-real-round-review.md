# S3 v2 新预算冻结与真实同轮执行记录

日期：2026-10-26。本轮已实际执行并按首次故障停止，保存回放和九任务共同报告已完成。**2 个任务完成、1 个失败、6 个未运行；不签收完整 S3，不作方法排名。**

## 冻结与授权

用户在上一条具体 v2 候选方案之后直接指示“新预算冻结与真实同轮执行。不要求模型全答对，但必须完整记录误判、失败和缺失”。据此记录对该候选单轮的授权依据：DeepSeek `deepseek-flash`、三历史×三方法、R=1、12.00 元本地总上限、最多 30 次请求，单并发、每次 90 秒、无重试、全组首次故障停止。不把该指令改写成用户逐字说过“批准12元”，也不沿用旧轮授权。

- [冻结计划](../reports/local/s3-real-v2-preflight-20261026-final/plan.json)：规范哈希 `2188a28843df9f89c1b9ea23f8d973b5ee50b2e6ba1c9e93cf715213fcf2e349`，满额最坏预留 11.211840 元。
- [新授权记录](../reports/local/s3-real-v2-authorization-20261026.json)：保存原指令、上下文依据、计划/运行器/价格绑定。
- 08:33:07 UTC 重新核对[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)：峰时未缓存输入 0.3 美元/百万 token、输出 1.2 美元/百万 token，与冻结保守单价一致；内部按 10 元/美元预留，不是汇率报价或提供商硬账单上限。
- [一次性启动凭据](../reports/local/s3-v2-paid-claims/2188a28843df9f89c1b9ea23f8d973b5ee50b2e6ba1c9e93cf715213fcf2e349.json)已占用。本轮授权已用，即使预留未耗尽也不补跑。

## 实际结果与错误

启动 08:33:54.508186 UTC，结束 08:34:19.857768 UTC，墙钟 25,349.421 ms。实际请求 3 次：两次基线 review、一次 extract；回查续答和增量终点均未发生。三次响应 finish_reason 均为 stop。

| 任务 | 方法 | 结果 | 终点三轴／缺失原因 |
| --- | --- | --- | --- |
| c001 | full_direct | completed | breaking / not_covered / revise，与作者草稿一致 |
| c001 | full_checklist | completed | breaking / not_covered / revise，与作者草稿一致 |
| c001 | incremental | stopped_on_failure | e001 状态批次被拒绝，接受事件数 0，无终点 |
| c002 | full_checklist | not_run | 全组首错停止 |
| c002 | incremental | not_run | 全组首错停止 |
| c002 | full_direct | not_run | 全组首错停止 |
| c003 | incremental | not_run | 全组首错停止 |
| c003 | full_direct | not_run | 全组首错停止 |
| c003 | full_checklist | not_run | 全组首错停止 |

失败的保存转换日志位于 [jobs.json](../reports/local/s3-real-v2-live-20261026/jobs.json) 第三个任务 `transition_steps[0]`。模型先提出约束，再提出授权；第二项（零起始索引 1）的授权主体 `field=state`，但 `change_details.old_field=status`，并引用 status 约束。原拒绝原因：

`authorization subject field differs from old_field; constraint reference scope mismatch: c001-constraint-v1-profile-status`

模型引文为“可以在客户端本地映射成页面需要的 state。”，却将主体写成 HTTP v1 响应体 state；这是本次实际提取/范围表示失败，不能因局部映射条件存在便将其修补为有效服务端授权。范围内核拒绝该原子批次，后续事件和任务不再请求。v2 操作形状通过不代表主体语义与范围正确；本次不是旧轮 grant 非空 targets/supersedes 的同一个形状故障。

原根 `run.failure.error` 及对应调用 `error` 为 null，详细原因仍保存在转换 `error` 与批次 `rejection_reason`；这是摘要字段未透传详细原因的已知报告缺口。本报告从保存转换派生说明，**不改写原调用、失败记录、源码或评分**。转换内部 `scripted_call_001` 是重建内核的内部标识；真实调用来源以根 real_model、provider_response 用量和 calls.json 为准，不能据此把模拟结果改标真实。

## 分数、缺失与费用

[九行共同报告](../reports/local/s3-real-v2-live-report-20261026/report.md)及[机器汇总](../reports/local/s3-real-v2-live-report-20261026/summary.json)保留计划分母。full_direct 和 full_checklist 各有效 1/3、三轴全中 1/3、无有效终点 2/3；incremental 有效 0/3、无有效终点 3/3。仅两个有效终点可核对，均无观察到的三轴误判；七个无终点不能算正确。误放行 0/2、误拦截 0/1 均是每方法的原计划分母下观察计数，不能解释为完整零错误率，正例缺失另列。参考仍为作者草稿，独立审核 pending_external_person。

| 账目 | 本轮记录 |
| --- | ---: |
| 实际请求／上限 | 3 / 30 |
| 本地已预留 | 0.416688 元 |
| 保守价格估算 | 0.080841 元 |
| 提供商实付 | unknown，3 次均无账单字段 |
| 输入／输出 token | 16,147 / 2,700 |
| 缓存命中／未命中输入 | 9,216 / 6,931 |
| reasoning token | unknown，3 次缺失 |
| provider 累计耗时 | 25,041.671 ms |
| 本地状态重建耗时 | 41.777 ms |

未运行任务的本轮实际请求成本为零；人工审核、跨轮共同准备等未完整计量成本为 unknown，不能当作零。此前轮次费用独立保留，不混入本轮方法成绩。

## 验证与下一步

[真实保存记录](../reports/local/s3-real-v2-live-20261026/run.json)独立重建通过；报告已在回放之后读取私有参考，报告文件哈希验证通过。只做离线核对，没有额外真实请求。运行时入口与报告源码快照保存在 reports/local，冻结计划及旧证据不追改；[收尾证据](../reports/local/s3-real-v2-live-closeout-20261026/evidence.json)记录工件哈希和派生失败原因。

本次交付是可重放的部分真实共同报告；完整九任务、自主真实回查和实际独立审核仍缺。下一步可任何新版本付费轮次另行冻结授权。S4 未运行。
