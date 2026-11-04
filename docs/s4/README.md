# S4 当前工程导航

2026-11-04。项目首页仍在根目录 [README](../../README.md)。本页负责当前 S4 工程与发布入口；[旧 docs/README](../README.md)有历史哈希绑定，保留原字节。

## 当前状态

S4-A 正处于项目内审核的开发比较准备。当前 compact contract v2 候选 `0ef9be84a94f272109535b483813b5eb91f4ddb7fc158f70a8d9c89e340c17d7` 的**单例离线工程验收已完成**：263 份文件绑定、51 项定向测试、9 组控制共 67 次模拟尝试；新目录核对 1,038 份清单工件，11 份报告重算一致，942 份既有文件保持原字节。

这些是工程验证。真实 v5 三方法均失败，无有效主输出；no_change 未实际触发，增量未到终点。原结果不改分。当前版本的 270 轨全批未运行，新提示与契约的真实模型行为尚未验证；没有方法优势、成本收益或泛化结论。

参考为 provisional，逐例未签收；独立审核为 `not_performed_skipped_by_user`，不是通过。保留完整上下文清单强基线、等配置主块与完整成本，预算匹配后置。研究出口与新模型执行、费用及外发授权均独立于此次发布整理。

## 推荐阅读顺序

| 目的 | 当前入口 |
| --- | --- |
| 复现公开候选 | [发布说明](public-release.md)：新恢复根、归档、验证范围与扫描 |
| 理解本次已实现和实测边界 | [契约收敛与联合验收](../s4-contract-convergence-offline-20261104.md) · [完成状态](../../reports/s4-contract-convergence-v2/completion.json) |
| 理解模型输出与确定性编译的分工 | [compact contract v2](../../specs/s4-compiled-contract-v2.md) |
| 检查最近保存的真实失败 | [v5 真实复核](../s4-owner-pilot-real-v5-review-20261104.md) |
| 查看阶段和剩余依赖 | [项目状态](../project-status.md) · [原剩余计划](../s4-remaining-plan-review-20261030.md) |
| 检查数据、争议与方法设计 | [项目内方法决定](../s4-project-method-decisions-v1.md) · [评测协议](../evaluation-protocol.md)；含作者解释，仅供设计/评测侧 |
| 查看旧版本全批与恢复 | [R5 历史验收](../s4-r5-offline-acceptance-20261102.md)：仅对应当时版本，不能替代当前 270 轨 |

## 维护与历史入口

| 部分 | 入口与用途 |
| --- | --- |
| 当前离线执行/回放 | [s4_contract_convergence_offline](../../scripts/s4_contract_convergence_offline.py)：prepare/offline/verify，真实发送关闭 |
| 当前契约 | [s4_compiled_contract_v2](../../scripts/s4_compiled_contract_v2.py)：一次声明来源、共同覆盖映射、操作分型 |
| 固定控制 | [s4_contract_convergence_acceptance](../../scripts/s4_contract_convergence_acceptance.py)：公开输入编制的模拟响应，不是模型答案 |
| 本次公开包装 | [s4_public_release](../../scripts/s4_public_release.py)：恢复独立根、离线验证与固定演示；实测结果单列于[发布说明](public-release.md) |
| 历史 S3 包装 | [public_release](../../scripts/public_release.py)与[原精选证据](../../reports/public-v0.1/README.md)，保留旧版本用途 |

新契约与新发布包装都复用已有执行内核、账本和 R4。旧入口、提示词、批准、失败、费用和评分保持原版；不能把多个调试版本拼成公平比较。历史材料中的“待实现”“待授权”属于各自时点。

## 发布后的剩余工作

本轮先完成公开文档、证据选择、扫描、恢复与明确 CI 检查集。当前工具和归档的最终实测结果见[发布说明](public-release.md)，新远程 CI 尚未运行，本轮不更新 GitHub。

后续研究仍需处理负责人语义决定与冻结依赖，将当前版本推广至 30×三方法×R=3 并做同版本全批验收，再按具体新计划决定真实运行。一次性旧批准及剩余预算均不能复用。无需重新寻找独立审核者；未执行状态如实保留。
