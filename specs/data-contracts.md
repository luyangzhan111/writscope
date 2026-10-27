# 数据契约

版本：`0.1-draft`；更新：2026-10-25。**本文件是全阶段规格。M1/M2 已实现首例、公开输入、三轴输出及基线接通；M3c–M3e 的真实调用是单例开发诊断。M3f–M3r 的后续范围、轨迹、终点请求、统一预算规则、脚本同轨运行、故障审计及新付费预检属离线准备；M3s 已完成 c003 单例完整路径真实诊断，M3t 已做离线收尾复核，随后 S3.4 完成 c003 单例无网络隔离诊断和 c001/c002 有限历史审核。S3 退出门槛未全满足，尚无正式方法比较或提供商实付账单。** 依据：[交接书 §2–5](../docs/project-brief.md)、[评测协议](../docs/evaluation-protocol.md)与[项目状态](../docs/project-status.md)。

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
- 建立局部例外时，每个 `constraint_refs` 应指向活跃约束，且授权与该约束至少有一个完全相同的 Subject（含 API 版本、路径和旧字段）；授权 `subjects[].field` 应与 `change_details.old_field` 一致。宽约束有多个 Subject 时允许只对交集主体建立局部例外。M3f 已离线验证此精确规则；原 M3a 内核尚未集成，不以新规则追溯改判旧运行。
- `change_details={old_field,new_field,preserve_value_semantics}`。允许替换字段不自动允许改变枚举值或含义。

### StateTransition 与 AuthorizationState

转换日志：`transition_id, source_event_id, source_seq, operation, target_ids, scope, before_hash, after_hash, accepted, rejection_reason, extraction_call_id`；操作为 `add_constraint / grant / revoke / supersede / mark_ambiguous / no_change`。

`grant` 是新授权；已有约束 ID 放在授权草稿的 `constraint_refs` 中，以局部例外保留宽约束，`target_ids` 为空。`supersede` 只替换**同类型**的活跃状态项：约束草稿的 `target_ids` 必须是约束 ID，授权草稿的 `target_ids` 必须是授权 ID；不能把约束 ID 用作授权替代目标。自然语言中的“本次候选”须由提取器表达为候选哈希条件，结构检查只能验证所提交哈希，不能自行补齐遗漏的条件。

状态：`state_id, through_event_id, through_seq, source_history_sha256, constraints, authorizations, exceptions, task_context, transition_refs, state_hash`。

`task_context[] = {requirement, source_refs, verification_refs}` 保存页面需求、客户端映射许可和请求数等任务背景；它不伪装为 HTTP 输出字段 predicate，也不产生兼容性破坏授权。首例通过外层验收检查这些任务要求，第一版三轴评分仍只覆盖声明的兼容性边界。

`exceptions` 条目为 `{exception_id, constraint_ids, authorization_id, subjects, change_types, conditions, valid_from}`；生效必须同时满足约束、授权和条件。宽约束保持 active，局部授权以 exception 覆盖；撤销授权会使例外失效，从而恢复原约束，无需重建被删的条目。完整撤销/替代仅在原文明确覆盖全部范围时使用 lifecycle。

处理顺序：提取候选 → 检查 schema/角色/引文/引用/时点 → 检查可确定范围 → 追加转换 → 生成状态。被拒绝候选进入错误记录，不能静默丢弃。用户含糊表达可接受为 ambiguous，而非结构错误。

幂等键为 `(case_id, event_id, extractor_config_hash)`；相同已接受提取记录重放必须确定性产生相同状态。重新调用随机模型是一次新提取运行，不能声称与旧状态必然相同。未来引用、循环替代、自引用、跨轨迹引用均无效。

**M3a 已实现的子集：** `TransitionProposal` 是严格的外部提取结果输入，每条可见事件至多一次转换；它只提交当前事件 ID、序号、`{event_id,quote}` 引文、操作及结构化草稿，内核从可信事件派生跨度、`valid_from` 和完整 Constraint/Authorization，并校验 `contract_ref` 与条件证据引用属于当前公开工件。工具/Agent 角色不能修改授权或约束；`no_change` 可记录事实事件。每次尝试保存 proposal 与 StateTransition 的 JSONL；已消费事件即使提案被拒绝，也推进历史但不修改约束/授权，重复提交不重复更新。JSON/schema 无效输入另存 `input_error.json`，不产生伪造状态。状态哈希包含已消费事件前缀与转换引用；回放逐条核对前后哈希、接受/拒绝及最终逐影响结果。

M3a 的局部例外由授权的 `constraint_refs` 生成，宽约束保持 active；撤销后旧例外作为历史保留，但无 active 授权便不生效。完整替代需新旧显式 subject 集合相同，防止较窄范围静默删除宽范围。`candidate_sha256` 条件由实际候选哈希核对，不匹配即不覆盖，即使提案声称满足；其他尚无独立验证器的条件即使提案称已满足，也只得 ambiguous。匹配按已核验影响的 subject、版本、操作、旧/新字段、有效例外逐项判断；**程序不证明自然语言提取语义正确**。`task_context`、一事件多转换和真实提取/回查成本仍留后续 S3，详见 [M3a 验收](../docs/m3a-review.md)。

**M3b 已实现的接线子集：** `ExtractionPayload={operation,source_quote,target_ids,constraint,authorization}` 是提供商唯一需要生成的字段；运行器根据当前可信事件补全 `case_id/source_event_id/source_seq/extractor_config_hash/extraction_call_id` 并交给 M3a。`ModelRequest.method=incremental_extract`、`output_contract=transition_quote_v1`，每步 `public_input` 只含当前及之前的原始事件、同一白名单公开工件和此前状态；共同基础证据哈希与完整上下文基线相同。M3b 当前 CLI 只接受 `ScriptedMock`，每事件一次调用，任一无效响应或拒绝转换即停止，无最终判断；保存每步请求、原文响应、错误和成本字段。真实提取 token/费用未知，不把模拟调用当真实模型调用，详见 [M3b 验收](../docs/m3b-review.md)。

**M3i 任务背景离线子集：** 独立版本化 `task_context_quote_v1` 账本每事件接受一个原子批次，批次内可有多条用户原文的唯一精确引文；无条目批次也推进历史。运行器核对可信事件角色、事件顺序、引文位置及公开验证引用，生成来源位置、稳定 ID、前后哈希，并可从批次记录独立回放。`requirement` 目前等于原文引文，不作语义归纳；该账本与旧 `AuthorizationState.task_context=[]` 分开，**尚未接入模型输出、正式增量运行器或最终模型复核**。同一事件多条任务背景引文不等于同一事件多项约束/授权转换；后者的离线内核见 M3j。见 [M3i 记录](../docs/m3i-task-context.md)。

**M3j 同事件多转换离线子集：** 独立 `transition_batch_quote_v1` 响应形状为 `{items:[ExtractionPayload...]}`，每事件 1–8 项，`no_change` 只能独占批次。运行器从可信事件补齐全部身份，逐项核验唯一引文和 M3f 范围规则；按条目顺序暂算，全部有效才原子提交。失败批次记录失败索引和原因、消费该事件、清空临时更新并停止轨迹；日志保存前后哈希并能独立回放。仍是手工构造的离线机制输入，**尚未接入 provider、M3i 任务背景或终点复核**，见 [M3j 记录](../docs/m3j-transition-batch.md)。

**M3k 截止点前回查离线子集：** `history_lookup_v1_offline` 仅按可信事件 ID 返回 `decision_point` 前含该点的原始 `ConversationEvent`；混入未来/未知 ID 的整次请求拒绝。显式次数与单次事件数上限、拒绝计数、耗时、请求/结果哈希及 JSONL 回放已实现。回查源是公开输入副本，不接受任意路径；工具事件仍保持原角色。模型发起回查及承载结果的 token/费用尚未接入，不能以本地读取的零模型调用替代完整回查成本。见 [M3k 记录](../docs/m3k-history-lookup.md)。

**M3l 同轨离线集成：** `TracePlan` 按公开事件顺序提供人工任务背景引文、人工回查 ID 和脚本响应；每步顺序为当前时点的回查、任务背景账本、原子状态批次。总回查次数跨事件累计；任一环节失败即停，不形成终点 `assessment.json`。完成时仅输出结构性 `ScopeAssessment`，与拟议的终点模型 `ReviewResult` 分开。保存计划、逐步轨迹、两份状态、源码/公开输入/工件哈希，可离线重建；真实模型调用和终点复核尚未接入。见 [M3l 记录](../docs/m3l-offline-trace.md)。

**M3c 真实提取开发子集：** 独立入口复用 M3b 请求契约与 M3a 内核，对 c001–c003 计划 15 个事件执行一次预算冻结的 DeepSeek 试跑；每步只在动态请求构建后预留本地估算费用，记录真实响应和提供商用量。首次无效输出/拒绝即停，未完成案例没有 `assessment.json`；运行目录内保存预检、每步请求/响应/调用、转换、状态、完整案例的评估及哈希，评分命令独立读取私有草稿标签。实际 10 次后 c002 e005 因状态目标错误停止，c003 未尝试；见 [M3c 记录](../docs/m3c-real-pilot.md)。本次没有真实最终复核模型调用，结构性终点组合不构成完整正式 incremental 方法。

**M3d 离线归因：** 原始 c002 `prior_state` 的确含有模型引用的约束 ID，状态拒绝是授权 `supersede` 目标类型不匹配；模型响应还遗漏“本次”候选哈希条件。新增版本化提取指令 v2，未更改历史输出或结构契约，也没有新模型调用，见 [M3d 诊断](../docs/m3d-offline-diagnosis.md)。

**后续独立闸门：** 用户单独批准两次各 2 元本地上限后，M3d c002 与 M3e c003 各完成五次 v2 真实逐事件请求；两例的当前候选终点三轴均与开发参考一致，见 [c002](../docs/m3d-real-review.md)与[c003](../docs/m3e-real-review.md)。c003 的模型输出却将 v2 授权的 `constraint_refs` 指向 v1 约束，结构内核仅验证 ID 存在并生成跨版本例外；当前 v1 决策未受影响。这是实现与本契约语义之间的缺口，需版本化离线修订，原始记录不追改。这些开发诊断不构成三方法比较。

**M3f 离线规则：** 独立策略已校验约束引用的活跃性、精确主体交集与授权旧字段一致性。保存的 M3e 原响应在新规则下于 e005 被拒绝，新增模型调用 0 次；原 M3a 内核与真实运行入口未变，见 [离线验收](../docs/m3f-offline-scope-review.md)。

**M3g 离线运行器：** 独立入口把 M3f 策略接入逐事件请求、状态转换、失败停止与独立回放，并保存策略、来源入口、提示词、公开输入和代码哈希。c002 保存响应五步通过，c003 保存响应在 e005 拒绝；新增模型调用 0 次。该入口不改原 M3a 内核及 M3c/M3d/M3e 真实记录，不是已授权的付费运行器，见 [M3g 验收](../docs/m3g-offline-integration.md)。

**M3h 真实路径草案：** c003 独立入口用 M3f 策略处理每次模型提案，并绑定价格文件、局部费用预留、失败停止与回放；离线模拟用量另列，真实 token/账单未知。未记录新授权时拒绝真实路径；2 元历史单价预检是草案，不是批准。本入口仍只生成提取状态和结构性 `ScopeAssessment`，尚无最终模型 `ReviewResult`；其计划输入输出见 [终点复核草案](final-review-v1.md)，验证见 [M3h 准备](../docs/m3h-real-path-prep.md)。

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

模型输出契约 `source_quote_v1`：两种完整上下文方法共同使用；所有 `source_refs`（顶层、`derived_items`、逐影响授权行）只提交 `{event_id, quote}`，不提交字符位置。运行器仅在决策点前的可见事件中查找该原文；**恰好出现一次**才派生 Unicode 字符 `[start,end)` 并写入持久化 `ReviewResult`。缺失、重复、未来事件或非用户授权引用均拒绝。这个结构校验不证明引用在语义上足以授权。首次真实运行使用历史 `source_span_v0`（模型直接提交位置），其错误与评分不按新契约重算；历史 M2a 脚本仍可按原严格跨度读取。新真实请求保存 `output_contract` 及新的提示词/JSON schema 哈希，不与历史记录混同。

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
- S3.4 的 `oracle_diagnostic` 采用[私有逐事件审核包和只读双轨回放](../docs/s3-4-isolated-diagnostic-design.md)；当前只作评测器侧确定性分析，未审核条目不能称“正确状态”，参考包及其派生答案不得进入模型请求或工具沙箱。
- 已执行的 c003 v1 子集单独保存[审核包](../evaluation_private/case_001/s3_4_c003_annotation_v1.json)及[隔离诊断](../docs/s3-4-c003-isolated-review.md)：单人项目规格审核、原运行先回放、逐事件语义事实核对和成本重算；其 `reference_state_ref` 暂不回填通用标签，诊断记录不可进入三方法成绩。
- c001/c002 后续采用独立 [s3_4_annotation_v2](s3-4-annotation-v2.md) 完成两条固定历史全部五前缀的人工状态编译，包含任务背景、独立预期事实与局部例外图。原观察先按原版回放；来源/对象键解析运行 ID，歧义或无效引用停止，条件与 predicate 原值保留。输出标记 `evaluator_only`，不进入请求白名单或通用标签；只支持活跃 add/grant/no_change，生命周期更新另行版本化，见 [执行记录](../docs/s3-4-c001-c002-state-compilation.md)。
- [语义判读与边界 v1](s3-4-semantic-boundary-v1.md)另绑定两份原 predicate 哈希及独立声明，仅在三种已审核布局上投影；未知、遗漏或冲突字段拒绝。合成扩展材料单独保存角色、提案与独立期望，首个拒绝停止，禁止当成原历史续跑或新增独立样本。输出保留原诊断与审核材料哈希，不追改评分或放行 v2 生命周期图。
- [生命周期图 v3](s3-4-lifecycle-graph-v3.md)独立保留本批跨事件的完整生命周期、更新来源、同类型替代边及历史例外。严格验证状态哈希/可信前缀/来源时间/引用，结合批次重放及 ID 改名控制比较投影；单独快照图不能证明更新合法。拒绝分支无有效终点，输出仍为 evaluator_only，不修改 v2 或模型契约，见 [v3 验收](../docs/s3-4-lifecycle-alignment-review.md)。
- [离线验收矩阵](../docs/s3-4-offline-acceptance-matrix.md)的本地 evidence 快照只记录工件/代码哈希、已有验证入口及核对结果、成本来源与未完成边界；仍为 evaluator_only，不构成模型输出、评分结果或新付费授权。报告须区分可收尾的开发离线子集、unknown 账单、未发生的真实回查和现行隔离规则禁止的人工状态模型实验。
- [S3 退出准备 v1](s3-exit-preparation-v1.md)规定公开来源/空白独立审核包与另立的未授权回查计划。冻结来源/模板和可填响应分开，完整性验证不自动采信响应中的身份/完成声明；候选请求不读取审核材料。费用与新授权留空，脚本状态只作传输，只有可信真实运行来源和非强制触发策略才能提供自主回查证据；未触发/失败不擅自补跑。
- `status=planned / running / succeeded / failed / budget_exhausted / cancelled`；计划没有伪造时间与计数，实际状态必须有相应记录。
- 每次 `CallRecord` 含 `call_id, run_id, stage, attempt, parent_call_id, request_hash, request_ref, response_ref, started_at, ended_at, latency_ms, input_tokens, output_tokens, cached_tokens, reasoning_tokens, usage_source, currency, billed_cost, cost_estimate, pricing_source, error_class`。
- stage 包含 extract/update/lookup/review/validate；程序更新与文件回查没有模型 token 时明确记 0、记录耗时；提供商没返回 token 则 null + 原因。reasoning/cache token 是否为子集随 provider 记录，不重复累加。
- 费用区分账单与估算，保存单价及日期。`totals` 包含全部尝试和状态维护总和、工具/模型分别耗时及端到端墙钟；并发时墙钟不等于各调用时长之和。
- `error_class` 至少含 invalid_input / extraction_error / state_update_error / invalid_output / model_error / timeout / tool_error / patch_apply_error / isolation_violation / budget_exhausted。
- 前序维护每个轨迹每次重复都计入最终决策；首轮不跨方法/实例共享缓存抵扣。失败耗费、重试、预算耗尽保留，不删出分母。
- 保存请求与响应前移除凭据；原始授权原文不得因脱敏而改变语义。任何必要替换必须三方法一致、写入 manifest。

## 10. 最小契约验收与当前实现

M1 已验证 ID/序号/截断、文件白名单、哈希绑定、三轴参考标签组合、用户来源和公开输入排除私有材料。M2a 已实现 verified_impact 固定包的 Pydantic 读取、完整内容入参、两个版本化提示词、严格 ReviewResult、脚本化 provider、逐次尝试、离线用量及独立评分报告；具体字段见 `src/writscope/contracts.py`、`runner.py` 与 `evaluation.py`。M2b 已实现 DeepSeek 单后端、六请求预检、本地费用预留、实际用量字段、失败停机及保存后独立评分；首次真实六任务收到响应和用量，但来源引文位置不满足当时严格契约，见 [真实接通记录](../docs/m2b-real-review.md)。`source_quote_v1` 的离线修订见 [引文契约验收](../docs/m2b-quote-contract-review.md)；新版真实六任务得到 5/6 有效输出，另一个因缺少必填字段无效，见 [新版真实验证](../docs/m2b-quote-real-review.md)。M3a 已实现独立的严格提案/状态/转换契约与离线回放、局部例外和逐影响范围匹配，见 [验收](../docs/m3a-review.md)；这些手工提案诊断不进入三方法排名。M3b 将脚本提供商逐事件接入该内核，保留每次请求/响应、错误及离线成本字段，仍不产生可排名的模型成绩。M3c 用同一提取契约执行一次真实开发试跑，记录 10 次调用和一次状态转换拒绝；完整方法比较仍未形成。现有 M2/M3c manifest 是完整 RunManifest 的实现子集；提供商账单费用仍未知；完整 S3 还需任务背景、真实最终复核及全部前序成本下的正式方法集成。

M3m 的离线终点请求复用 `source_quote_v1` 与三轴 `ReviewResult` 校验。请求的 `evidence_sha256` 只哈希与完整上下文基线相同的公开包；方法生成的 `current_state`、`task_context`、已发生 `history_lookup` 另存于请求并整体哈希。响应中的状态条目 ID、类型及原文来源必须与当前状态对应。此为结构校验，真实模型语义与总成本尚未验证，见 [M3m 记录](../docs/m3m-final-review-offline.md)。

M3n 的完整运行预算草案把五次事件提取、最多两次回查续答及一次终点复核计入同一上限。只读本地回查另计次数/耗时；每次 provider 尝试发送前占用本地预留，失败或用量缺失不释放。费用与账单仍以估算/实付/unknown 分开，草案没有价格和获批总额，不能启动真实运行，见 [M3n 记录](../docs/m3n-budget-envelope.md)。

M3o 的 `interaction_quote_v1` 响应以 `kind` 区分 `lookup_request` 和 `extract`，两者不可混合；提取结果同时包含任务背景精确引文与一至八项状态变更，可信事件身份由运行器补齐。回查按原事件时点与全轨额度重放；逐事件请求只含可信前缀、先前状态/任务背景和当前事件已发生的回查结果。终点请求从完整状态构造并显式绑定模型配置，仍沿用 `source_quote_v1`。契约已离线验证，真实同轨运行未接入，见 [M3o 记录](../docs/m3o-interaction-contract.md)。

M3p 将该契约接入仅脚本 provider 的版本化同轨入口：响应选择回查或任务背景加状态批次，只有两者均接受才提交事件；完整五事件后才允许终点 `ReviewResult`。同一账本覆盖五次初始提取、最多两次回查续答及一次终点模拟尝试；逐次请求、响应、模拟用量、预留、状态与终点均保存并可回放。回查耗时单独审计，不写入后续模型请求。此处所有结果仍为脚本诊断，见 [M3p 记录](../docs/m3p-scripted-runner.md)。

M3q 为该脚本入口补充九类故障/预检拒绝和三历史公开输入审计。报告映射 `case_id`、方法、运行状态、观察三轴、公开证据/工具哈希、尝试数、模拟 token 与 unknown 账单；`expected` 和 `correct` 留空。与完整上下文基线的基础证据及工具权限一致不等于预算匹配或准确率比较。真实 provider 耗时、M2 式运行 ID、实际用量和账单仍待后续入口补齐，见 [M3q 记录](../docs/m3q-fault-evidence-audit.md)。

M3r 的 `preflight_no_model_call` 记录固定 c003 开发目标、`deepseek-flash` 配置、交互/终点提示词哈希、基础公开证据、八次调用及失败上界、价格来源/日期、本地保守换算系数与全程最坏预留。`status=draft_unapproved`、`new_paid_authorization=null` 和 `actual_model_calls=0` 描述 M3r 原始提案；授权闸门先核验精确计划哈希/目标/金额/尝试数，再可读取凭据。M3s 接通真实 provider 入口与保存后回放，另核验运行器哈希和价格复核时效；无网络模拟结果与真实结果分开标记，缺失费用保持 unknown。后续获得独立授权并运行 c003 完整路径一次，见 [M3r 预检](../docs/m3r-real-preflight.md)、[M3s 离线闸门](../docs/m3s-real-runner-offline-gate.md)与[真实记录](../docs/m3s-real-review.md)。

M1 的实际文件格式：

- `manifest.json` 为评测器侧固定文件哈希、case_ids 与 decision_point；不发给方法。原始事件每行包含 schema_version，合成角色来源由 manifest 固定。
- `labels.json` 以 `kind=reference_labels_not_predictions` 包装 ReferenceLabel 列表，另有私有测试哨兵；标签是交接书派生的项目参考，无独立人工标注声明。
- `report.json` 是精简的 FixtureValidationRecord：run_id、execution_kind、时间、Python/依赖/锁文件/源码哈希、三个副本的实际 observation、进程命令/退出码/耗时/日志引用、JUnit 结果、输入包哈希及 failures。`authorization_evaluation=not_run`，model_calls/tokens/cost 均为 0；它不假装满足完整模型 RunManifest。
- `impact.json` 是依据公开检查实际观测生成的 ImpactEvidence；不含授权/最终决策，且不包含兼容参考修复或隐藏测试输出。模型调用记录和状态在 M1 不生成。

## S3 小型真实验收冻结契约 v1

[小型真实验收](../docs/s3-small-real-acceptance-freeze.md)使用独立 s3_small_real_preflight_v1 / s3_small_complete_incremental_real_v1。新计划保留 draft_unapproved/new_paid_authorization=null，并绑定实际入口 SHA-256、上游离线候选记录、分类源码、提示词、公开输入、模型/价格和单次八尝试规则；授权另以 S3_small_c003_lookup_acceptance_v1 精确匹配计划、入口、c003、3.00元与八次上限，来自新的用户直接批准。真实发送前按计划哈希排他占用本地启动记录，不自动重用或释放；失败亦保留预留。响应用量超出已预留token范围即停止并可回放；lookup_evidence 只由可信运行来源、回查链和续答日志计算，模拟永远为 offline_transport_only，完成但未触发为 not_exercised。冻结当时尚未批准或发起新真实请求，不能以授权文件自报代替用户批准。

本次执行更新见[真实记录](../docs/s3-small-real-acceptance-review.md)：用户直接批准后新增6真实请求，五事件及终点有效、回放通过；lookup_evidence=not_exercised、actual_model_calls=6、lookup_attempts=0。授权收尾另存，不修改原draft提案或历史结果；本地预留0.844281元、峰时估算0.131598元、实付unknown，剩余可授权启动次数为0。

## S3 共同执行与报告 v1

[共同执行冻结](../docs/s3-common-round-freeze.md)采用s3_common_round_v1和s3_common_report_v1。plan列九任务、轮转顺序、模型/价格/公开输入/提示词/代码/环境哈希与三十次上界；新授权须匹配S3_three_method_three_case_development_round_v1、计划/入口、三案例/三方法、单轮、12.00元和30请求。calls保存每次请求/响应/用量/预留，jobs保存各轨状态/转换/背景/回查及失败；全组首故障后不再发送，剩余任务not_run。共同成本额外单列基线review，使review/extract/lookup_continuation/final_review四阶段与总账一致；本地重复重建计时逐次累计。report命令先重建本轮，再在评测器通道读标签；模拟或未运行报告metrics=null，不以空位填0%模型成绩。独立审核暂缺与未知费用明确保留，准备时本轮未批准或真实执行。

2026-10-26本轮执行更新见[真实记录](../docs/s3-common-round-real-review.md)：status=stopped_on_failure，actual_model_calls=14；四completed、一stopped_on_failure、四not_run，失败在job_005/c002/e005。保存回放后report命令读取作者草稿生成九行真实报告，未运行保留原分母；本次授权已用，remaining_authorized_rounds=0，原draft与所有响应不改。预留1.937553元、估算0.310446元、实付unknown。

## interaction_quote_v2操作形状

[新契约](interaction-quote-v2.md)由独立scripts/s3_interaction_v2.py定义，输出仍为可选lookup_request或extract；no_change单项和1–8项变更分开，grant/add必须空目标及空supersedes，替代必须同类型新载荷与匹配目标，撤销/标歧义必须目标且无载荷。先检查v2结构，再将原响应交给v1解析和原范围内核，不修补响应。模型请求仅替换提示词/schema/output_contract，完整公开前缀不变；配置哈希另含v2版本。当前只离线使用，通用提示词不附案例答案，原模型失败/评分不改，人工结构控制不作模型成绩；真实适配和新预算尚未集成。

## s3_common_offline_v2

[离线同轮契约](s3-common-offline-v2.md)固定九任务、三历史/三方法/R=1和轮转顺序，plan绑定v2配置/提示词/首次请求/脚本/公开证据/权限/组件hash；每增量最多8脚本尝试、每基线1次，全组最多30且首错停止。各job保存其状态、背景、转换、回查、脚本调用及可选终点；根summary/报告保留not_run行及四阶段模拟账表。verify逐轨与根汇总均从固定来源重建，不靠重写hash接纳篡改。actual_model_calls=0、metrics=null、actual_price/paid_budget为空；只有run/verify，无付费或评分入口。

## s3_common_real_v2授权与响应边界

[新闸门契约](s3-common-real-v2-gate.md)将s3_common_real_v2与interaction_quote_v2、新scope、当前运行器/计划/price hash绑定，保存九份首次请求供重建。每次发送前检查任务/全组次数和预留，原响应、用量/错误、授权引用和usage_kind分开保存。真实执行另存authorization.json及hash，回放按started_at_utc校验核价并核对一次性启动；模拟不许携带授权或被改标真实后读标签。非法用量原值保留、汇总unknown。候选12元/30次仅draft_unapproved，new_authorization=null，无本步新付费授权；69项测试和六份模拟通过。


## 2026-10-26：v2 新预算真实轮次已执行

用户在上一条12.00元/最多30次具体候选之后指示“新预算冻结与真实同轮执行”，据此建立独立本轮授权并重新核对官方价格；单并发、90秒、无重试、全组首次故障停止。实际3次请求，c001两个基线完成且三轴与作者草稿一致，增量在e001因授权主体state与old_field=status及引用范围不一致而拒绝；其余六任务未运行。原失败不修补，九行报告保留缺失与原计划分母，保存重建通过。预留0.416688元、保守估算0.080841元、账单及reasoning token unknown。根错误摘要为null，详细拒绝理由保存在转换日志，本报告派生说明而不改原记录。

本轮授权已用，不补跑；完整九任务、自主真实回查和实际独立审核仍缺，S3未完整签收、S4未运行。下一步先离线复现主体/局部映射问题与错误摘要透传缺口，新付费轮次另行冻结授权。见 [v2真实同轮记录](../docs/s3-v2-real-round-review.md)。


## failure_summary_v1

[摘要透传契约](failure-summary-v1.md)只在相同调用/事件的状态拒绝错误缺失且转换证据唯一一致时补齐派生摘要，旧记录不回写，原范围/状态/成本/停止规则不变。独立离线诊断先通过原verify，再保存raw_failure与新failure以及响应哈希；run/verify无真实入口，metrics=null、new_model_calls=0。10项测试和原响应重建通过；真实集成与提取语义修订尚未实施，见 [离线记录](../docs/s3-local-mapping-offline-review.md)。


## S3 coverage follow-up v1（待授权）

新scope使用interaction v3明确区分客户端本地映射和服务端契约授权；九轨R=1，基线原提示词不变，最多30请求/12.00元本地上限。状态/语义/Provider错误停止单轨并继续其他轨，预算和完整性失败停止全轮。三条增量轨道保留模型自主lookup，未触发不能宣称已验收且不自动补跑。作者审核只作为开发参考，独立审查仍缺。离线预检、九首请求和未运行报告草稿哈希见[冻结草案](../docs/s3-coverage-followup-freeze.md)及[evidence](../reports/local/s3-coverage-followup-evidence-20261026/evidence.json)；计划1fa3e130…，draft_unapproved，真实请求0。
