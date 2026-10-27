# S3 v6 c004 真实诊断记录

日期：2026-10-26。此记录对应已单独批准的 v6 计划，只执行一次；不扩展到其他案例。

## 授权及启动检查

- 批准计划 SHA-256：`2c5bf0d25b13519a332a65ab8fa0364bc89478ee4ed8df20afcdb5eb64bc0ab2`。
- 直接用户授权覆盖一次 c004、最多8次、3.00元本地预留上限及冻结文件列明的外发范围；重跑和其他案例未授权。
- 执行前于 2026-10-26 15:03:58 UTC 重核[DeepSeek官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)，Flash峰时未缓存输入为USD 0.30/百万token、输出USD 1.20/百万token，与计划一致。运行器/source/计划绑定验证通过后才读取凭据。

## 真实结果

- 保存目录：[真实轨迹](../reports/local/s3-development-v6-live-20261026/)；冻结入口回放成功：[单轨报告](../reports/local/s3-development-v6-live-report-20261026/report.md)。
- 共发出5次真实提取请求，均无重试。e001–e004接受；e005状态转换因 `constraint reference scope mismatch: c_minimal_change_no_new_api; constraint reference scope mismatch: c_page_local_mapping` 被内核拒绝，接受4/5，首次失败即停止。
- 模型没有提出历史回查请求；`lookup_evidence=not_exercised`。没有续答或终点复核。本计划最关键的自主回查链仍未验证。
- 报告计划分母保留为1：有效输出0/1，无有效输出1/1，`s3_development_exit=not_satisfied`、`s3_full_exit=not_satisfied`。作者草稿预期为breaking / not_covered / revise；失败轨迹无观察三轴，不把它改写成结果。
- 本地保守预留0.799122元；峰时估算0.119862元；提供商实付账单unknown。累计输入35,462 token、输出1,123 token；reasoning token unknown。3元本地授权已消费，禁止按本计划补跑。

## 轨迹哈希

- `run.json`: `356f37b035152f2cd5418b506d9a09fa97e0442456be93e8c6496cd12f898d2b`
- `calls.json`: `1fc476d910c51b872b837eccdaa32c4eb570663a4af6109c13e4c0d0cd82c697`
- `jobs.json`: `b05db759e2da9bfbf2347c86935cd7d3bce613fb92fd5f4de45401bdf8795b18`
- 报告摘要：`b7238dc3de8cc7e4cc4747850e2e46b3bb395912880466188908bc223f7921f4`
- 报告中的 prior nine-job run 复核通过，未与本轨拼接比较或形成方法排名。

此单例仅说明该次运行在e005失败，不能证明v7/v4总体改善或退化；独立审核、真实自主回查和完整S3仍未完成。
