# S3.4 私有审核包 v2 与局部例外对齐

日期：2026-10-25。用途：评测器侧离线诊断。实现为 [state_alignment_v2](../scripts/s3_4_state_alignment.py)，输入为 c001/c002 的独立版本审核包；原 c003 v1 和两份有限历史审核保持可回放。该契约不改变任何模型输出格式、原策略或原评分。

## 审核包

`schema_version=s3_4_annotation_v2`，顶层字段为：

```text
case_id, decision_point, public_bundle_hash, source_spec_sha256,
policy_id, annotation_status, reviewer_ids, review_notes,
annotation_hash, fact_catalog, events
```

`annotation_hash` 为排除自身字段后的规范 JSON 哈希。公开输入哈希、首例规格字节哈希、五个连续可信事件及各自前缀哈希均绑定。只有 `reviewed` 条目可编译；本批审核者为 `codex_single_spec_audit`，仍是单人项目规格审核，不是独立金标准。

每个事件明确保存 `event_id, seq, visible_prefix_hash, context_items, proposals, expected_poststate_facts, rationale, item_review_status`。可信身份由公开事件核对；提案只支持 `add_constraint / grant / no_change`，使用现有 M3j 原子批次契约；任务背景使用 M3i 精确用户引文及公开验证引用。未来事实、重复或未知预期事实、非可信用户来源、非唯一引文、未知字段及哈希不一致均拒绝。

`fact_catalog` 的键是评测器写下的语义符号，不是引擎生成 ID：

| kind | 独立预期字段 |
| --- | --- |
| task_context | 来源事件、原文、verification_refs |
| constraint | 来源事件/原文、subjects、predicate、lifecycle、interpretation、valid_from、supersedes、updated_by |
| authorization | 来源事件/原文、subjects、change_type/details、conditions、constraint_refs、lifecycle、interpretation、valid_from、supersedes、updated_by |
| exception | authorization_ref、constraint_refs、subjects、change_types、conditions、valid_from |

catalog 中的 `constraint_refs`、`authorization_ref` 指向 catalog 符号。每个前缀的预期集合必须包含其引用对象且不含未来事实。约束/授权的 `valid_from` 必须与可信来源事件一致；例外时点与授权一致。`expected_poststate_facts` 在编译前由审核包独立写出，不由引擎输出反填。

## 编译与身份对齐

原观察先按各自原版本无网络验证；成功后才读取审核包。从空状态按前缀运行 M3i 任务背景账本和 M3j/M3f 状态批次，逐步将生成事实与独立预期比较。拒绝或不一致即 `oracle_compile_mismatch` 并停止。人工轨迹使用显式新策略，观察轨迹继续使用原 `StateEngine`，两者的哈希不可互换。

对齐键为类型、来源事件、唯一精确引文及排序后的完整 subjects 的规范哈希。新旧运行的约束/授权 ID 都解析到这个来源和对象键；例外 ID 则解析到其授权键与约束键集合。重复键视为 `alignment_ambiguous`；缺失、重复或类型错误的引用视为 `alignment_invalid`，不猜测对应关系。对齐只确定候选身份，不证明语义等价。

投影保留 predicate 原值、版本/路径/字段、变更种类与细节、所有条件、生命周期、来源时点和例外边。只排序集合性质的 subjects、条件列表和引用列表；不自动迁移 predicate 的字典布局或条件值。ID 改名可保持投影相同；改变候选哈希、版本或例外范围仍会产生差异。相同来源/对象但 predicate 不同的两个条目也不能被合并。

本版本只覆盖活跃约束、新许可和局部例外；非空 `supersedes / updated_by` 或非 active 生命周期明确拒绝为 `alignment_unsupported`。c001/c002 的全部五个实际前缀均处于该子集；撤销、替代和重授权的通用对齐须另行版本化。

## 归因与输出

提案或状态中的 predicate 表示不同先记 `candidate_representation_difference_unresolved` 或继承差异，不直接当成语义错误。只有对齐后的先前状态和提案均相同而后状态不同，才可记内核/策略错误候选。M3c/M3d 的旧契约没有任务背景槽，任务背景缺口记为 `observed_contract_task_context_unavailable`，不能倒推为该轮提取模型的错误。

每例分别写 `diagnostic.json` 和 `evaluator_trace.json`，均标为 `visibility=evaluator_only`。前者含逐前缀差异、ID 映射、来源/源码哈希和独立的确定性范围检查；后者保存人工状态、任务背景、批次记录及原策略观察状态，供离线重建。输出不得加入模型输入白名单、请求构造器或工具沙箱。此入口无 provider 执行分支，不调用模型；原版本验证器仅重建原公开请求以核验原记录。

模型在人工状态下的复核为 `not_run`，人工时间和本次离线耗时未测量时保持 unknown；既有运行成本引用各自历史审核，不重复记为新费用。不回填 `labels.json.reference_state_ref`，不生成方法评分或费用收益比较。
