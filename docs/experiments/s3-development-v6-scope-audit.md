# S3 v6 c004 constraint_refs 范围规则核对

日期：2026-10-26。作者进行的离线机制核对；新模型请求0、未读取私有参考标签。保存的真实响应、拒绝、评分和费用均未改动。

## 核对依据

- [数据契约](../../specs/data-contracts.md)规定：每条 `Authorization.constraint_refs` 指向该授权建立例外的约束；必须为活跃约束，并与授权至少有一个完全相同的 `Subject`。Subject 包含协议、方法、路径、API版本、位置和字段。授权 subject 的 `field` 还须等于 `change_details.old_field`。
- v6实际使用的 [BatchStateEngine](../../scripts/m3j_transition_batch.py) 继承 [scope_ref_v2](../../scripts/m3f_scope_policy.py)：逐条要求引用存在且active，并至少与一个授权subject完全相同；跨字段/版本/路径的引用拒绝。批次任一提案失败则整批不提交并停止轨迹。该检查验证active与scope，不独立判定约束谓词是否对授权语义适用。
- 影响评估 `src/writscope/state.py::_affected_constraints` 只把 subject 完全相同、且谓词类型与变更类型相关的活跃约束列入该 impact 的 `needed` 集合；授权必须引用所有 needed 约束。对本次影响主体 `/api/v1/profile` response.body `status`，needed 集合仅含 `c_v1_profile_status`。

## e005 引用矩阵

| 引用 | 约束 Subject.field | e005 授权 Subject.field | 完全匹配 | 是否为本次 impact 的 needed constraint |
| --- | --- | --- | --- | --- |
| `c_v1_profile_status` | status | status | 是 | 是 |
| `c_minimal_change_no_new_api` | state | status | 否 | 否 |
| `c_page_local_mapping` | state | status | 否 | 否 |

因此，e005 的完整 `constraint_refs` 按当前规则应被拒绝；错误来自两条 state 范围引用。状态内核并未误拒；不应通过放宽规则或自动删掉引用来接受原响应。严格地说，只能得出这些额外引用越界，以及状态分支原子停止；不能由离线矩阵宣称“删掉两项后原模型输出应当通过”或追改真实评分。

## 另一个上游提取问题

e001 的 `c_page_local_mapping` 和 e004 的 `c_minimal_change_no_new_api` 都被模型存入状态约束，Subject.field 为 state。首例设计将客户端本地映射和“少改动/不增加接口或请求”定义为任务背景，而不是 API 响应字段约束；v7 提示也要求客户端/页面映射进 task context。它们在本次被状态引擎结构上接受，但该接受不能证明语义类别正确。对本次 status impact，它们不在 needed 集合；也不该作为 status grant 的 refs。这个上游类别问题与 `constraint_refs` 范围校验是两个问题。

提示 v7 的“Put applicability in constraint_refs; keep broader active constraints”与本次过度引用并存。它可能被读成“引用所有活跃约束”，但这只是基于文本与输出的解释性假设，不能由单例证明因果。准确规则可表达为：**保持未被替代的约束继续 active；每条 `constraint_ref` 只能列出与授权 subject 至少一个完全匹配、且确实为该授权建立例外的约束。不得为了保留约束而把不匹配 subject 的ID附加到 grant。** 这只是下一版本提示的离线修订建议；本次哈希绑定的 v7 不改。

## 结果及边界

离线重建 e001–e004 全部通过，重建的 e004 后状态哈希为 `e00254afca66d5332860e3e200cbce2ee9b69f3ce3f410896b21e3eedd2e101c`。e005 原始授权 subject 为 status，三条 refs 的核对结果为1条匹配、2条不匹配；impact 所需引用仅为 `c_v1_profile_status`。完整字段见 [本地诊断矩阵](../../reports/local/s3-development-v6-scope-audit-20261026/diagnostic.json)。来源 run、calls、响应和策略的哈希在该矩阵中记录。

本分析不认可授权自然语言、不判定 candidate 条件是否满足、不把模型的`satisfied`提升为已回查事实，也不自动构造替代提案。v6真实运行仍为4/5接受、lookup `not_exercised`、无终点且 `not_satisfied`；该计划授权已用。后续仅可另开版本离线澄清提示和任务背景/约束类别规则。任何新的真实诊断须新冻结并单独取得预算及外发授权；S3完整验收和独立审核仍未完成。
