# 文档导航

首次了解项目请从仓库根目录的 [README](../README.md) 开始：项目问题、安装、无密钥演示和保存记录回放都在首页。这个目录提供开发与研究文档的分类入口。

## 当前项目

WritScope 研究多轮编码任务中，候选补丁的兼容性影响是否被用户当前有效授权覆盖。分别记录 `impact`、`authorization`、`decision`，并保留来源、作用范围、局部例外和生命周期。

当前为研究开发版：S3 已满足作者审核的开发出口，独立研究审核待补，S4 正式比较未运行。首发代码 `1a7d412` 已在 GitHub Windows 与 Ubuntu 各通过 369 项测试、无密钥演示和历史回放；这些工程检查不证明方法优于强基线。

| 阅读目的 | 文档 |
| --- | --- |
| 查看已经完成什么、仍缺什么 | [项目状态](project-status.md) |
| 使用各阶段组件 | [组件级运行指南](usage.md) |
| 理解事件、状态、引用和输出 | [数据契约](../specs/data-contracts.md) |
| 理解公平比较、隔离和成本口径 | [评测协议](evaluation-protocol.md) |
| 接续开发和了解待办 | [实现计划](implementation-plan.md) · [决策记录](decisions.md) |
| R3 当前维护入口与验收 | [2026-11-02 离线工程验收](s4-r3-offline-acceptance-20261102.md) · [入口契约 v2](../specs/s4-r3-offline-entry-v2.md)；后续接 R4，真实发送仍关闭 |
| R4 独立轨迹验证与评测接线 | [2026-11-02 验收说明](s4-r4-offline-acceptance-20261102.md) · [评测契约 v1](../specs/s4-r4-evaluation-v1.md)；从收到的响应重建，参考只在验证后读取，真实发送关闭 |
| R5 同版本全批与交付恢复 | [2026-11-02 验收记录](s4-r5-offline-acceptance-20261102.md) · [恢复操作](s4-r5-delivery.md) · [契约 v1](../specs/s4-r5-acceptance-v1.md)；固定合成场景、完整计划分母，真实发送关闭 |
| 推进下一阶段评测 | [最新剩余计划与 7 份执行提示词](s4-remaining-plan-review-20261030.md) · [R3 进程中断核对](s4-r3-interruption-review-20261030.md) · [R3 模拟计划与记录核对](s4-r3-proposal-audit-20261030.md) · [R3 检查点统一入口](s4-r3-checkpoint-offline-20261030.md) · [负责人确认与 R3 模拟接线](s4-owner-decisions-and-r3-bridge-20261030.md) · [R3 provider 与中断核对补充](s4-r3-provider-recovery-20261030.md) · [R3 模拟闸门与 R2 接口进度](s4-r3-mock-gate-20261030.md) · [R1/R2 中断审计补充](s4-r1-r2-recovery-followup-20261030.md) · [R2 单账本离线进度](s4-r2-unified-offline-20261030.md) · [虚拟金额预留](s4-synthetic-cost-gate-20261030.md) · [语义审核流程](s4-review-process.md) · [正式比较设计候选](s4-experiment-design.md)；[逐例矩阵](s4-p0-matrix.md)含参考答案，仅供评测器与设计审核 |
| 查看初始需求依据 | [初始交接书](project-brief.md)，保留其历史时点 |

## 代码入口

| 部分 | 入口 |
| --- | --- |
| 首例影响验证和公开输入 | [M1](../src/writscope/m1.py) · [输入契约](../src/writscope/inputs.py) |
| 两种完整上下文基线 | [baselines](../src/writscope/baselines/__init__.py) |
| 状态、原子批次和历史回查 | [state](../src/writscope/state.py) · [batch](../scripts/m3j_transition_batch.py) · [lookup](../scripts/m3k_history_lookup.py) |
| 当前公开演示与回放 | [public_release](../scripts/public_release.py) |
| 最近完整真实开发诊断 | [v8 入口](../scripts/s3_acceptance_v8.py) · [报告器](../scripts/s3_acceptance_report_v8.py)；固定 c004 单例，尚非 S4 通用运行器 |

## 结果与历史

- [实验记录](experiments/README.md)：成功、失败、离线机制检查及作者验收，标明各自证据边界。
- [精选证据包](../reports/public-v0.1/README.md)：359 份原始工件、哈希清单与恢复方式。
- [历史归档](archive/README.md)：旧计划、预检、离线接线、交接和发布记录。
- [GitHub 首次发布](archive/releases/github-first-publication-20261028.md)：首发代码与双平台 CI 的对应关系。

归档中的“未实现”“待授权”等描述属于文档当时状态；新工作以项目状态、实现计划和最新用户授权为准。初始交接书及冻结规格原文保留，文档整体含作者答案，不得作为模型输入。

## 旧路径兼容

顶层的 `m1-review.md`、`m3a-review.md`、`m3e-real-review.md` 和两份 v7/v8 离线记录是简短的兼容入口。它们保留冻结规格中的旧链接，完整原文已进入下级目录。其他旧路径可通过 [迁移清单](archive/migration-map.json) 查找。
