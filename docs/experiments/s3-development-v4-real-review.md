# S3 开发验收 v4 真实运行与判定

日期：2026-10-26。用户对计划`9b0b9976c958720aeb1c668a734790d401d29b9b185e244898a05924ac093b58`批准6.00元上限，并授权将c002/c004的公开合成历史、候选代码/契约、提示词及按需回查原文发送至DeepSeek。按冻结范围单次执行完毕，授权已用；未重试、未补跑。

## 执行与结果

执行前14:20 UTC重新核对[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)，Flash峰时未缓存输入$0.30/百万token、输出$1.20/百万token未变。计划/源码哈希、独立参考冻结记录均一致。[授权收据](../../reports/local/s3-development-v4-authorization-20261026.json)及[21份冻结源码快照](../../reports/local/s3-development-v4-source-snapshot-20261026/manifest.json)已保存。

运行开始于14:21:08 UTC、结束于14:21:48 UTC，共7次真实请求：6次提取、1次终点、0次回查续答。最多16次、本地6元、单并发、90秒、无重试的规则均满足。

| 案例 | 实际状态 | 接受事件 | 终点／失败 | 请求 | 本地预留/元 |
| --- | --- | ---: | --- | ---: | ---: |
| c002 | completed | 5/5 | breaking / covered / allow，与作者草稿一致 | 6 | 0.966978 |
| c004 | stopped_on_failure | 0/5 | e001范围表示拒绝，无终点 | 1 | 0.157353 |

c004首响应将“可以在客户端本地映射成页面需要的state”另提为服务端授权：主体为v1响应体`field=state`，但`old_field=status`，并引用status约束。内核原样拒绝：`authorization subject field differs from old_field; constraint reference scope mismatch: c_v1_profile_status_contract`。两项原子批次中的约束与授权均未提交，任务背景也未提交；状态约束/授权仍为空，背景through_seq=0。新版错误摘要已将确切原因透传到call/job/root，不再是null。

这是再次观察到的客户端局部映射与服务端授权表示失败。v5提示词虽已说明边界，本轮仍发生该错误；不能宣称提示词消除了问题。原响应、范围规则和失败均保留，不清空错误授权、改字段、丢弃条目或继续运行以取得后续结果。

## 回查与开发验收

c002没有请求回查，记`not_exercised`。c004在e001停止，未运行到e002工具报告或e005用户条件，记`failed_or_incomplete_preserved`；本轮没有真正检验到c004所设计的回查依赖，不能据此断言模型拒绝回查或回查工具不可用。

报告器重新核验原九任务真实覆盖（plan`1fa3e130…`、13请求）和本轮保存记录后，输出：

- `s3_development_exit = not_satisfied`
- `real_lookup_branch_verified = false`
- 有效终点1/2、三轴全中1/2、无有效输出1/2；唯一有效终点与作者草稿一致。

用户允许记录模型错误和失败，未要求全答对。当前不通过的硬缺口仍为“真实模型自主回查、原文返回、续答接受、有效终点”的完整链；不会因单例模型错误便添加全对率门槛，也不能通过更改判定关闭回查缺口。

## 费用与证据

| 项目 | 本轮记录 |
| --- | ---: |
| 真实请求／上限 | 7 / 16 |
| 本地预留 | 1.124331元 |
| 峰时保守估算 | 0.183624元 |
| 提供商实付 | unknown，7次均无账单字段 |
| 输入／输出token | 49,700 / 2,877 |
| 缓存命中／未命中输入 | 3,840 / 45,860 |
| reasoning token | unknown，7次缺失 |
| provider累计耗时 | 38,482.594ms |

本地预留不等于提供商实付；人工与共享准备成本仍unknown。本轮没有基线请求，不与旧轮拼接做方法排名。

- [原始运行](../../reports/local/s3-development-v4-live-20261026/run.json)经v4原入口直接回放成功，本次无需nullable兼容层。
- [真实报告](../../reports/local/s3-development-v4-live-report-20261026/report.md)保留两轨分母、失败与缺失，先回放再读取作者草稿；参考SHA-256仍匹配运行前的私有冻结记录。
- [收尾证据](../../reports/local/s3-development-v4-closeout-20261026/evidence.json)绑定计划、授权、运行、报告、源码快照与失败归因，新增复核请求0次。

本次单轮交付已执行并汇总，S3开发验收尚未通过。独立审核、完整研究验收和S4正式比较仍未完成。后续应先处理提取中的领域/作用域表示问题，任何新增真实运行须重新冻结范围并授权；本计划不续跑。
