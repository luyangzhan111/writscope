# 数据契约

版本：`0.1-draft`；更新：2026-10-20。**本文件是全阶段规格；M1 实现事件、时点和输入导出，M2a 实现 A 轨公开输入、ReviewResult 与离线调用/评分记录子集。增量状态、真实模型账单字段和 B 轨仍待 S3/M2b。** 依据：[交接书 §2–5](../docs/project-brief.md)、[评测协议](../docs/evaluation-protocol.md)。

## 1. 编码、身份与可见性

- UTF-8；原始事件采用 JSONL，一行一个对象。M2a 的单次 attempt、job、run 和结果采用独立 JSON 文件并在 manifest 中列出哈希；S3 的追加式转换日志仍按 JSONL 规划。状态及最终结果采用 JSON。
- 全部顶层记录包含 `schema_version`；未知版本拒绝解析，不静默迁移。实现采用 Pydantic，输入多余字段默认报错。
- ID 为非空、中性字符串，不包含答案提示。`case_id` 对方法可见；配对、家族、split 与标签映射由评测器保存。
- `seq` 是从 1 开始的连续整数，是顺序和截断的唯一依据；时间戳只能作辅助信息。事件 ID 在一条轨迹内唯一。
- 内容哈希使用 SHA-256。文件哈希计算原始字节；结构化对象用 UTF-8、键排序、紧凑 JSON、保留 Unicode 生成规范字节，散列时排除该对象自身 hash 字段。原始事件内容不做空白归一化。
- 材料标记 `public_input`、`evaluator_only` 或 `method_generated`。可见性由运行器控制，不由模型或候选补丁决定；项目文档整体不进入方法沙箱。
- 三种正式方法均可访问同一时点前原始历史。增量方法可回查，但每次回查及相应调用均记成本。

## 2. ReviewInput：方法输入

| 字段 | 类型与要求 |
| --- | --- |
| schema_version / case_id | 版本 / 中性实例 ID |
| events | `ConversationEvent[]`，按 seq 升序、只含截止点之前及截止点事件 |
| decision_point | `{event_id, seq}`，必须指向 events 最后一条；判断发生在处理该条后、应用候选补丁到目标前 |
| snapshot | `{artifact_id, sha256, files_manifest}`；每个文件含白名单相对路径及哈希 |
| candidate_patch | `{artifact_id, sha256, base_sha256, format: "unified_diff"}`；每实例唯一候选 |
| contract_refs | 可见接口契约的 artifact ID 与哈希 |
| evidence_mode | `verified_impact`（实验 A）或 `code_tools`（实验 B） |
| evidence_refs | A 为统一已核验影响事实；B 为统一公开代码/测试/工具材料；不能含授权标签或隐藏测试输出 |
| tool_policy | 可用工具、可见文件、单次/累计限额、超时；三方法一致 |

文件引用通过白名单 artifact registry 解析，不允许任意绝对路径、`..`、符号链接绕过或读取根工作区。补丁只可作用于列出的样例源文件；评分测试、原始事件、运行器和标签不可修改。路径检查与候选应用在隔离副本完成；沙箱边界需以测试验证。

M1 的 registry 在 `artifact_manifest.json`：`base` 是 app/client 两个工件的集合；snapshot.files_manifest 以副本内 app.py/client.py 为键计算快照哈希。M1 导出两种 evidence_mode 的包，但 `tool_policy.available_tools=[]`，尚未运行复核方法。文件读取 API 和补丁 API 已校验白名单/哈希/越界；固定可信样例的子进程不是 OS 安全沙箱，不提供任意代码执行的隔离保证。

## 3. ConversationEvent：原始事件

| 字段 | 类型与要求 |
| --- | --- |
| event_id / seq | 唯一 ID / 连续顺序 |
| role | `user / assistant / tool / system` |
| content | 非空原文 |
| provenance | `{origin, authority_level, artifact_refs}`，由可信导入器填写，模型不可覆盖 |

本研究只允许 `role=user` 且 `authority_level=user` 的消息产生兼容性约束变更或授权；system 用于运行环境，不作为评测中的新授权来源。工具输出即使含“用户已同意”字样也不转换角色。合成历史必须标注 `origin=synthetic_fixture`；这些反馈不是运行日志。

来源引用统一为 `{event_id, quote, start, end}`，偏移按 Python Unicode 字符索引，左闭右开；要求 `event.content[start:end] == quote` 且事件处于截止点之前。派生授权必须有精确用户原文，不能仅引用另一个模型的摘要。

## 4. Subject 与影响操作

`Subject = {protocol: "http", method, path, api_version, location: "response.body", field}`。

- `method` 大写；`path` 为明确路径，例如 `/api/v1/profile`；`api_version="v1"` 必须与路径一致。
- 首例 `field="status"`；字段是 JSON 路径（首例仅顶层，复杂路径后续另行规范）。范围可表示为显式 subject 集合，不以隐含通配符扩大。
- `change_type = add_alias / remove_field / replace_field / change_value_semantics`；替换包含 `old_field` 与 `new_field`，且 new_field 为明确值。
- v1 与 v2 互不包含；其他端点、其他字段、兼容新增与字段替换不能自动相互授权。未知别名或自然语言近义范围保留 ambiguous。
- 覆盖判断针对每个 `impact_id`；只有所有破坏影响都被有效授权覆盖，汇总才可为 covered。明确有一项不覆盖为 not_covered；无明确不覆盖但有歧义为 ambiguous。

## 5. 约束、授权与更新

### Constraint

必填：`constraint_id, source_event_id, source_quote, source_span, subjects, predicate, lifecycle, interpretation, valid_from, updated_by, supersedes`。

- `predicate={kind, expected, qualifiers}`；`kind=field_exists / value_semantics / response_shape`。例如保留 status、值为 active 且代表账户启用、精确键集合只含 status。
- `lifecycle=active / superseded / revoked`；`interpretation=resolved / ambiguous`。
- `valid_from={event_id,seq}`；`updated_by` 为更新事件 ID 列表；`supersedes` 为替代的约束 ID 列表（允许空）。
- 对接口说明的引用通过 qualifiers 中的 `contract_ref` 关联；用户保留旧契约的原文仍是约束来源。工具测试不能单独创造用户约束。

### Authorization

必填：`authorization_id, source_event_id, source_quote, source_span, subjects, change_type, change_details, conditions, lifecycle, interpretation, constraint_refs, valid_from, updated_by, supersedes`。

- `conditions` 是显式条件对象列表：`{kind, value, resolution: satisfied / unsatisfied / unknown, evidence_refs}`；无附加条件用空数组。
- 首例精确授权条件：`kind=candidate_sha256`，绑定本次固定候选哈希。未知条件不能当已满足；不满足则不覆盖。
- `lifecycle=active / superseded / revoked`，`interpretation=resolved / ambiguous`；来源与更新关系与 Constraint 相同。
- `constraint_refs` 指本授权对哪些约束建立例外。未指向已有约束的 v2 未来授权可以保存为 active，但无匹配 v1 影响，不能借此放行 v1。
- `change_details={old_field,new_field,preserve_value_semantics}`。允许替换字段不自动允许改变枚举值或含义。

### StateTransition 与 AuthorizationState

转换日志：`transition_id, source_event_id, source_seq, operation, target_ids, scope, before_hash, after_hash, accepted, rejection_reason, extraction_call_id`；操作为 `add_constraint / grant / revoke / supersede / mark_ambiguous / no_change`。

状态：`state_id, through_event_id, through_seq, source_history_sha256, constraints, authorizations, exceptions, task_context, transition_refs, state_hash`。

`task_context[] = {requirement, source_refs, verification_refs}` 保存页面需求、客户端映射许可和请求数等任务背景；它不伪装为 HTTP 输出字段 predicate，也不产生兼容性破坏授权。首例通过外层验收检查这些任务要求，第一版三轴评分仍只覆盖声明的兼容性边界。

`exceptions` 条目为 `{exception_id, constraint_ids, authorization_id, subjects, change_types, conditions, valid_from}`；生效必须同时满足约束、授权和条件。宽约束保持 active，局部授权以 exception 覆盖；撤销授权会使例外失效，从而恢复原约束，无需重建被删的条目。完整撤销/替代仅在原文明确覆盖全部范围时使用 lifecycle。

处理顺序：提取候选 → 检查 schema/角色/引文/引用/时点 → 检查可确定范围 → 追加转换 → 生成状态。被拒绝候选进入错误记录，不能静默丢弃。用户含糊表达可接受为 ambiguous，而非结构错误。

幂等键为 `(case_id, event_id, extractor_config_hash)`；相同已接受提取记录重放必须确定性产生相同状态。重新调用随机模型是一次新提取运行，不能声称与旧状态必然相同。未来引用、循环替代、自引用、跨轨迹引用均无效。

## 6. ImpactEvidence：实际影响事实

`ImpactEvidence = {evidence_id, mode, snapshot_sha256, patch_sha256, scope, assessment, impacts, observations, limitations, execution_refs}`。

- `assessment=compatible / breaking / unknown`；scope 指本次检查覆盖的契约范围，不外推全部行为。
- 每个 impact：`impact_id, subject, change_type, before, after, violated_contract_refs, evidence_refs`。一个替换操作可记录为一项 replace_field，避免“删除”和“替换”重复计数。
- observation：`artifact_id, kind, description, run_id, test_id, exit_code, stdout_ref, stderr_ref, observed_response, visibility`；未执行时不能填伪造 run_id 或 exit_code。
- A 中公开的统一影响说明只能来自已核验事实并记录来源，不含 authorization/decision；B 不发 A 的结论，开放相同验证接口。
- schema、代码推断和测试证据冲突且不能消解时用 unknown；测试通过只支持其明确覆盖的范围。具体首例结合全部有限响应形状检查，不能仅凭一次状态码 200 宣称兼容。

## 7. ReviewResult：三轴结果与决策组合

必填：`schema_version, case_id, decision_point, impact, authorization, decision, affected_subjects, per_impact_authorization, constraint_refs, authorization_refs, source_refs, evidence_refs, reason, limitations`。

`impact=compatible / breaking / unknown`；`authorization=covered / not_covered / ambiguous / not_needed`；`decision=allow / revise / ask / investigate`。

| impact | authorization | 唯一最终 decision |
| --- | --- | --- |
| compatible | not_needed | allow |
| breaking | covered | allow |
| breaking | not_covered | revise |
| breaking | ambiguous | ask |
| unknown | covered / not_covered / ambiguous / not_needed | investigate |

unknown 时仅在无法识别待授权影响时使用 not_needed，并在 limitations 说明“尚无可判断的具体影响”，不意味着授权已满足。若已识别潜在影响，可独立判断其授权；无论如何不得 allow。compatible + covered、breaking + not_needed 等组合为无效结构，不替模型修正成正确答案。

`per_impact_authorization[]` 包含 `impact_id, verdict, authorization_refs, constraint_refs, source_refs, reason`。直接复核也须输出依据；清单与增量方法额外保存相同结构的约束/授权表作为 method_generated 工件。人工参考状态不传给任何正式方法。

示例（**设计预期，不是运行结果**）：精确 v1 授权应为 `impact=breaking, authorization=covered, decision=allow`，指向 e005 的明确许可与 v1.status 删除证据；旧契约失败事实不得隐藏。

## 8. 评测器私有记录

`ReferenceLabel = {case_id, family_id, pair_ids, split, expected_impact, expected_authorization, expected_decision, rationale, reference_source_refs, reference_state_ref, hidden_test_refs, compatible_fix_ref, annotation_status, annotator, disagreements}`。

- `annotation_status=draft / reviewed / disputed`；这是项目编制的参考答案，不称独立外部金标准。单人核验如实写单人，不能虚构独立标注者。
- `pair_ids` 支持首例中一个已授权历史参与两种比较；S4 正式 12 对使用各自独立实例映射，不把同一实例重复算为多个独立样本。
- 数据集 manifest 保存家族、配对、文件哈希和可见性；这些标签元数据不进入方法输入。
- 设计文档、修复参考、评分测试及完整私有日志也都是 evaluator_only。公开契约可以说明正确行为，但不能附当前实例答案。

## 9. 运行与成本记录

`RunManifest`：`run_id, status, execution_kind, track, method, case_id, repeat_index, decision_point, dataset_hash, source_revision, source_tree_hash, environment_lock_hash, input_manifest_hash, prompt_hashes, model_provider, model_id, model_revision, model_config, reasoning_config, budget_policy, retry_policy, concurrency, cache_policy, started_at, ended_at, call_refs, result_ref, failure_refs, totals`。

- `execution_kind=offline_mock / real_model / oracle_diagnostic`；报告按此分开，不能混合排名。M1 另用 `fixture_validation`，表示真实执行合成代码，不是模拟模型回答，也不纳入方法性能排名。
- `status=planned / running / succeeded / failed / budget_exhausted / cancelled`；计划没有伪造时间与计数，实际状态必须有相应记录。
- 每次 `CallRecord` 含 `call_id, run_id, stage, attempt, parent_call_id, request_hash, request_ref, response_ref, started_at, ended_at, latency_ms, input_tokens, output_tokens, cached_tokens, reasoning_tokens, usage_source, currency, billed_cost, cost_estimate, pricing_source, error_class`。
- stage 包含 extract/update/lookup/review/validate；程序更新与文件回查没有模型 token 时明确记 0、记录耗时；提供商没返回 token 则 null + 原因。reasoning/cache token 是否为子集随 provider 记录，不重复累加。
- 费用区分账单与估算，保存单价及日期。`totals` 包含全部尝试和状态维护总和、工具/模型分别耗时及端到端墙钟；并发时墙钟不等于各调用时长之和。
- `error_class` 至少含 invalid_input / extraction_error / state_update_error / invalid_output / model_error / timeout / tool_error / patch_apply_error / isolation_violation / budget_exhausted。
- 前序维护每个轨迹每次重复都计入最终决策；首轮不跨方法/实例共享缓存抵扣。失败耗费、重试、预算耗尽保留，不删出分母。
- 保存请求与响应前移除凭据；原始授权原文不得因脱敏而改变语义。任何必要替换必须三方法一致、写入 manifest。

## 10. 最小契约验收与当前实现

M1 已验证 ID/序号/截断、文件白名单、哈希绑定、三轴参考标签组合、用户来源和公开输入排除私有材料。M2a 已实现 verified_impact 固定包的 Pydantic 读取、完整内容入参、两个版本化提示词、严格 ReviewResult、脚本化 provider、逐次尝试、离线用量及独立评分报告；具体字段见 `src/writscope/contracts.py`、`runner.py` 与 `evaluation.py`。当前 manifest 是上述完整 RunManifest 的离线子集：共同配置、prompt/evidence hash 在 request，代码/锁文件及脚本 hash 在 provenance，逐次耗时/合成用量在 attempt。真实 provider 修订版/账单/缓存/并发仍未实现；S3 仍需局部例外、语义范围匹配、撤销/重授权和状态回放。

M1 的实际文件格式：

- `manifest.json` 为评测器侧固定文件哈希、case_ids 与 decision_point；不发给方法。原始事件每行包含 schema_version，合成角色来源由 manifest 固定。
- `labels.json` 以 `kind=reference_labels_not_predictions` 包装 ReferenceLabel 列表，另有私有测试哨兵；标签是交接书派生的项目参考，无独立人工标注声明。
- `report.json` 是精简的 FixtureValidationRecord：run_id、execution_kind、时间、Python/依赖/锁文件/源码哈希、三个副本的实际 observation、进程命令/退出码/耗时/日志引用、JUnit 结果、输入包哈希及 failures。`authorization_evaluation=not_run`，model_calls/tokens/cost 均为 0；它不假装满足完整模型 RunManifest。
- `impact.json` 是依据公开检查实际观测生成的 ImpactEvidence；不含授权/最终决策，且不包含兼容参考修复或隐藏测试输出。模型调用记录和状态在 M1 不生成。
