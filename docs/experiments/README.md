# 实验与验收记录

这里保存实际执行的代码验证、脚本机制检查、真实模型开发诊断、失败归因与作者验收。各记录保留原执行时点和对应版本；离线或合成结果不计模型成绩，跨轮结果不能拼成方法排名。

## 优先阅读

| 记录 | 能说明什么 |
| --- | --- |
| [S3 最终开发审查](s3-development-final-audit-20261027.md) | 作者审核开发出口的证据与剩余研究限制 |
| [v8 c004 真实诊断](s3-development-v8-real-review.md) | 自主回查、原文返回、续答和有效终点的单例完整链 |
| [九任务共同覆盖](s3-coverage-followup-real-review.md) | 六基线完成、三增量失败，保留失败分母 |
| [v7 终点失败](s3-development-v7-real-review.md) | 回查成功后仍可能在终点权限引用处失败 |
| [生命周期图诊断](s3-4-lifecycle-alignment-review.md) | 六个合成分支的有限状态与身份控制检查 |

这些材料来自已调试开发场景及作者审核。独立审核、S4 同配置比较和泛化收益仍待验证。原始请求、响应和费用记录通过 [精选证据包](../../reports/public-v0.1/README.md) 恢复。

## 全部记录

下方按阶段列出，不代表各记录采用同一配置或具有相同证据等级。

### M1 / M2：样例、脚本与基线接通

- [M1 验收与运行说明](m1-review.md)
- [M2a 验收：两种完整上下文基线的无密钥流程](m2a-review.md)
- [M2b 新版引文契约真实验证](m2b-quote-real-review.md)
- [M2b 首次 DeepSeek 真实接通记录](m2b-real-review.md)

### M3：状态机制与早期真实诊断

- [M3a 最小增量授权状态内核验收](m3a-review.md)
- [M3b 增量方法离线接线验收](m3b-review.md)
- [M3c 真实增量提取开发试跑](m3c-real-pilot.md)
- [M3d：M3c 失败归因与离线提取修订](m3d-offline-diagnosis.md)
- [M3d c002 v2 真实诊断记录](m3d-real-review.md)
- [M3e c003 v2 真实诊断记录](m3e-real-review.md)
- [M3f：跨版本约束引用的离线范围校验](m3f-offline-scope-review.md)
- [M3g：版本化运行器离线集成与失败回放](m3g-offline-integration.md)
- [M3q：离线故障与证据审计](m3q-fault-evidence-audit.md)
- [M3s：c003 完整增量真实开发诊断](m3s-real-review.md)
- [M3t：M3 阶段收尾复核](m3t-closeout-review.md)

### S3.4：评测器侧隔离诊断

- [S3.4 c001/c002：完整前缀状态编译与局部例外对齐](s3-4-c001-c002-state-compilation.md)
- [S3.4 c001：M3c 历史提取的隔离审核](s3-4-c001-historical-audit.md)
- [S3.4 c002：两次历史提取的隔离审核](s3-4-c002-historical-audit.md)
- [S3.4 c003：隔离状态诊断与成本核查](s3-4-c003-isolated-review.md)
- [S3.4 生命周期图 v3 离线验收](s3-4-lifecycle-alignment-review.md)
- [S3.4 离线验收矩阵与剩余证据](s3-4-offline-acceptance-matrix.md)
- [S3.4：predicate 判读与六类生命周期边界](s3-4-semantic-lifecycle-review.md)

### S3：共同轮、后续真实诊断与失败归因

- [S3 同轮真实执行：14 次请求后按首次故障停止](s3-common-round-real-review.md)
- [S3 v3 开发验收补充轮真实记录](s3-coverage-followup-real-review.md)
- [S3 开发验收最终审查](s3-development-final-audit-20261027.md)
- [S3 开发验收 v4 真实运行与判定](s3-development-v4-real-review.md)
- [S3 开发验收 v5 真实诊断与判定](s3-development-v5-real-review.md)
- [S3 v6 c004 失败的离线归因](s3-development-v6-offline-diagnosis.md)
- [S3 v6 c004 真实诊断记录](s3-development-v6-real-review.md)
- [S3 v6 c004 constraint_refs 范围规则核对](s3-development-v6-scope-audit.md)
- [S3 开发验收 v7 c004 真实诊断记录](s3-development-v7-real-review.md)
- [S3 开发验收 v8 c004 真实诊断记录](s3-development-v8-real-review.md)
- [S3 增量补充 v3 真实执行记录](s3-incremental-followup-real-review.md)
- [S3 局部映射主体离线复现与摘要透传修订](s3-local-mapping-offline-review.md)
- [c004 局部映射提取失败：离线诊断与候选修订](s3-local-mapping-v6-offline-diagnosis.md)
- [c004 真实回查续答拒绝：条件与证据引用离线诊断](s3-lookup-condition-offline-diagnosis.md)
- [S3 提案结构错误：离线复现与 v2 修订](s3-proposal-shape-offline-review.md)
- [S3 小型真实验收记录：c003 完成，回查未触发](s3-small-real-acceptance-review.md)

返回 [文档导航](../README.md)。
