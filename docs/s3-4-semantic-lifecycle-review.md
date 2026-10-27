# S3.4：predicate 判读与六类生命周期边界

日期：2026-10-25。执行类型：**单人规格语义审核 + 评测器侧合成机制诊断**。新增真实模型请求 **0 次**。本步承接 [c001/c002 全前缀编译](s3-4-c001-c002-state-compilation.md)，处理其中尚未判读的 predicate 表示差异并验证生命周期边界；原真实请求、原状态、原评分和 v2 诊断均保留。

## 输入与保存证据

[新入口](../scripts/s3_4_semantic_boundary_audit.py)先无网络重验两份保存的 v2 诊断，再读取 [predicate 单人审核](../evaluation_private/case_001/s3_4_predicate_review_v1.json)和[六分支合成边界](../evaluation_private/case_001/s3_4_lifecycle_boundaries_v1.json)。审核仍属 **Codex 单人项目规格审核**，不是独立金标准。私有材料及派生声明只在评测器中使用，未进入模型请求或工具沙箱。

[本地审核结果](../reports/local/s3-4-semantic-boundary-20261025/audit.json) SHA-256 为 `69acd8bd92ad5a5bf8f0f7aa552a23f2ac4ba11ec81353d70a11f1b118675893`；语义审核包 SHA-256 为 `d244f3643f4da1371cc3319d9680d6be53466d157397b16f90ded59894c480a9`，边界材料为 `6724fd7013c0a57236685490d6f7ec9db25b33fe326c632ad7f317aeaa81797a`。`run` 后另行 `verify` 重算通过。字段及停止规则见[契约 v1](../specs/s3-4-semantic-boundary-v1.md)。

## 两处表示差异的判读

| 来源 | 观察写法 | 与人工表示的关系 |
| --- | --- | --- |
| M3c c001 e001 | HTTP 状态、响应体、类型及禁止额外键嵌在 expected；qualifiers 保留 exact_response 与 value_meaning | 在逐项审核的声明上等价 |
| M3d c002 e001 | expected 为响应体；qualifiers 使用 value_type、meaning 等字段 | 在此确切 predicate 中与人工 status_type、value_meaning 的声明等价 |

两者均明确表达用户原文中的 HTTP 200、响应体只能有 `status="active"`、值为字符串且表示账户启用。结论为 `equivalent_on_reviewed_claims`，只绑定两份原 predicate 哈希及共同人工表示；未知字段、漏掉含义或附加条件不能自动忽略。

这为 v2 的 e001 表示差异及后续继承差异补充了语义判读，**未把旧标签改成另一轮评分**。它不取消 c003 的其他提取表示候选，也不证明任意新 predicate 的语义正确。c001/c002 旧入口没有任务背景持久化的能力缺口仍然存在。

## 六个边界分支

所有分支都在评测器中从 c002 公开前缀和已审核人工提案重建，新增事件只存在于独立合成副本，不是原 c002 的真实续跑。共实际尝试 7 条扩展事件，2 条在拒绝后保持未尝试。

| 分支 | 预期与实际一致的边界 |
| --- | --- |
| b001 撤销后重授权 | 撤销使覆盖变为 not_covered/revise；原宽约束活跃，历史例外保留；新授权恢复 covered/allow，旧撤销记录保留 |
| b002 同范围授权替代 | 旧许可为 superseded，新许可活跃，两个例外保留；由新许可覆盖当前候选 |
| b003 活跃许可标为歧义 | 不撤销原许可，但范围判断变为 ambiguous/ask，保留更新来源 |
| b004 同范围约束替代 | 原约束为 superseded、新约束活跃；旧许可及例外仍引用旧约束，不自动覆盖新约束，结果 not_covered/revise |
| b005 授权替代错用约束 ID | 类型错误被拒绝，状态条目未突变；无有效终点，后续扩展不尝试 |
| b006 工具冒充用户撤销 | 工具内容不具用户权限，被拒绝并停止；原许可仍保留，无有效终点 |

前四类生命周期快照都被 v2 投影明确拒绝为 `alignment_unsupported`；没有删除 revoked/superseded/updated_by 等历史字段来制造对齐成功。边界证据说明原引擎在这些手写提案上按预期运行，**通用生命周期图对齐尚未实现**。

## 验证与剩余工作

[新增测试](../tests/test_s3_4_semantic_boundary_audit.py) **5 项通过**，覆盖禁止 provider 构造的完整审核/保存后回放、闭集投影拒绝未知/缺失/冲突字段、保留状态码/含义/字段值变化、原 predicate 哈希与独立声明绑定、独立边界预期篡改拒绝、先验旧诊断及新保存结果篡改拒绝。连同原 v2 的 7 项，共 **12 项定向测试通过**。六个分支的全部批次另经确定性重放，失败分支未发出有效终点评估。

原真实模型的调用与费用账本不变；本步离线耗时及人工审核时间未测量，记 unknown。合成分支不增加独立场景样本，不参与方法质量或成本排名。

Q022 中本批表示差异判读与六类边界用例已经完成。后续若继续补齐通用生命周期对齐，应另立版本，显式保留撤销、替代、歧义及历史例外关系，并用本次边界作回归。独立审核、真实模型回查、同轮三方法报告与 S4 冻结仍缺，S3 退出门槛未全满足。

无网络复验：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_4_semantic_boundary_audit verify --output reports/local/s3-4-semantic-boundary-20261025
```
