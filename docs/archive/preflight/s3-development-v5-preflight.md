# S3 开发验收 v5 离线预检：c004 × interaction v6

日期：2026-10-26。v6 提示候选已完成离线评审，新增单轨版本化入口；本步骤没有读取凭据或发起真实 provider 请求。

## 冻结候选

- 计划：`reports/local/s3-development-v5-preflight-final2-20261026/plan.json`
- 计划 SHA-256：`72104c5ef4f560ff5aa0c99f4844802a64ed367de29fcaec693a5a347f7b7277`
- scope：`S3_c004_interaction_v6_diagnostic_v1`
- 单轨 c004、incremental、R=1；同一已调试案例家族，不是泛化样本或方法比较。
- DeepSeek `deepseek-flash`、temperature 0、最大输出4096；最多5次事件提取、2次模型自主回查续答、1次终点复核，共8次；单并发、90秒、无重试，首次失败停止唯一轨迹，不续跑。
- 只变更至 interaction v6 提示词。候选版本按来源哈希绑定交互/运行/报告/模拟器/范围策略代码；公开事件原文仅在模型请求后按回查规则提供。参考标签不进入模型请求。

## 预算候选及外发范围

当日14:34:37 UTC复核[DeepSeek官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)：Flash峰时未缓存输入为$0.30/百万token，输出为$1.20/百万token。按项目保守预算换算10元/美元，8次×100,000字节的最坏本地预留为**2.989824元**；拟议本地上限**3.00元**。提供商账单可能不同，此预留不构成提供商账单硬上限。正式启动前重新核价。

若获单独批准，仅可发送冻结计划列明的公开合成c004历史、候选代码/契约/公开证据、v6及相关提示；历史原文只能按模型自主请求回查时发送。不得发送私有参考标签/状态。v4的6元授权已使用且绑定另一计划；本次草案仍为 `draft_unapproved`、无授权收据，**必须另行明确批准3元上限和上述数据外发范围**。

## 离线验证

九项定向测试通过，覆盖计划与v6绑定、初始隐藏历史正文、预检不读取密钥/私有参考、旧v4授权拒绝、新入口回放以及已保存失败停机。5条模拟轨迹均保存并重建：

| 控制 | 模拟尝试 | 轨迹结果 | 回查标记 |
| --- | ---: | --- | --- |
| v6_lookup | 7 | 全五事件与模拟终点完成 | offline_transport_only |
| no_lookup | 6 | 全五事件与模拟终点完成 | not_exercised |
| original_v4_rejection | 1 | 精确重放旧e001范围拒绝并停止 | not_exercised |
| first_error | 1 | 首错停止 | not_exercised |
| invalid_output | 1 | 非法JSON首错停止 | not_exercised |

五份报告均 `actual_model_calls=0`、`metrics=null`、`s3_development_exit=not_satisfied`。模拟回查不算真实自主回查证据。准备目录复验成功，冻结源码快照含27个文件；准确计划、首次请求、交互/终点提示和源码哈希可在预检目录及[源码快照](../../../reports/local/s3-development-v5-source-snapshot-20261026/manifest.json)复核。

## 验收边界与下一动作

本步完成了入口、范围、调用上限、费用预留、失败停机和回放的离线冻结。它不证明真实模型遵守v6，也不证明真实回查会触发；即使将来完整跑通，也只补一个开发诊断，独立审核和完整研究S3仍另列。继续真实诊断前，需要用户对以上**确切计划哈希、3.00元上限和外发范围**作新的明确批准；未批准前不读密钥、不发请求。不得把本轮计划作为c004未见样本或跨轮排名数据。

## 执行更新（2026-10-26）

上述计划已获单次3.00元及指定材料外发授权。复核官方价格后实际执行6次，五事件提取及一次回查续答；模型自主选择e002，原文成功返回，续答因条件类型/证据引用错误被拒绝。共接受4/5事件，无终点，回查链未完整，报告`s3_development_exit=not_satisfied`。本地预留0.968514元、峰时保守估算0.140325元、提供商实付unknown；授权已用，不续跑。详情见[真实记录](../../experiments/s3-development-v5-real-review.md)。后续离线归因见[条件与证据引用诊断](../../experiments/s3-lookup-condition-offline-diagnosis.md)。
