# S4-A 运行器与评测器接口约定 v1

2026-10-29。供第 3、4 项实现使用；这是接口设计，不声称运行器、评测器、鉴权或真实执行已经实现。字段名为实现约定，最终 schema、源码及计划须版本化；本约定不能作为用户授权凭据。

## 执行前与任务身份

- `plan`: `version`, `plan_sha256`, `track`, `case_ids`, `methods`, `repetitions`, `jobs[]`, `data_bundle_hashes`, `prompt_hashes`, `schema_hashes`, `source_hashes`, `model_config`, `tool_policy`, `failure_policy`, `limits`, `ordering`, `reference_version_hash`。`jobs[]` 明列 `(case_id, method, track, repeat_index)`，唯一且覆盖冻结计划；方法输入不包含配对映射、参考标签或参考哈希内容。
- 每次启动前校验计划、源码、公开包、提示词、价格、范围及一次性启动；明确授权和外发范围经可信操作流程核验后才可读取凭据。JSON 自报用户身份或 `approved=true` 不能单独构成授权。
- 不将人工参考状态、标签、隐藏测试或修复补丁交给方法。目录及白名单不是 OS 沙箱；当前不开放任意代码执行。

## 持久化轨迹：运行器输出

运行器按预列任务保存 `run_manifest` 与每任务记录。身份字段为 `run_id`, `plan_sha256`, `case_id`, `method`, `track`, `repeat_index`；记录 `status=completed|failed|not_run`, `failure_kind`, `failure_stage`, `public_bundle_hash`, `attempts[]`, `lookups[]`, `transitions[]`, `assessment`, `final`, `terminal_conflict`。每次 attempt 记录唯一 ID、阶段、请求/响应或错误的受控引用及哈希、发送前预留、开始/结束时间、provider 标识、finish reason、token/缓存/reasoning 用量及费用来源。缺失为 null/unknown，不假定为零；失败和发送后未收到响应的尝试仍计入账本。

预留与调用额度须在发送前持久化；进程中断也能识别已发送或状态不明的尝试。逐项记录哈希、源绑定、实际顺序及停止原因；一般故障停单轨，完整性故障和额度故障停全轮。失败不合成 final；`not_run` 不得有 provider 尝试。总计划分母始终保留。此约定不要求自动续跑。

## 验证交接与评测器输入

评测器**先验证**计划哈希、任务唯一性和完整分母、公开输入、引用/来源、状态转换、保存的响应及账本，离线重放可验证的路径；随后才读取评测器侧参考、配对和争议处理表。验证结果由可信实现计算，不信任轨迹自报 `verified`、`real_model` 或 JSON 用户身份。模拟、真实、旧版本和未完成任务必须可区分；模拟标记即使被改写也不得进入真实评分。无法验证真实性或完整性时拒绝评分并保留证据。

评测器只接收已验证轨迹的不可变引用、冻结计划和评测器侧参考清单；不向运行器或方法回传参考。每条任务输出原始轴判断或无有效输出、合法性/失败分类、引用解析结果及与冻结参考的比较；评分聚合由参考和计划确定，不按预测 impact 或成功状态筛选分母。

## 评测器输出与兼容性

`evaluation_report` 包含 `version`, `plan_sha256`, `run_manifest_hash`, `reference_hash`, `disposition_hash`, `verification`, `planned_jobs`, `rows[]`, `metrics`, `coverage`, `cost`, `limitations`。每个 metric 提供数值分子、计划分母、未输出数和适用范围；另报三轴、全部轴、按 repeat 成对、两歧义例、误放行、传统误拦截、含缺失的合法未放行及引用质量。候选 B 诊断单列，不能覆盖主结果。费用区分已知小计、未知项和完整总额；模型实际账单未提供时为 unknown。

各文件使用独立新版本与内容哈希；不能修改历史 P2/P4 轨迹及评分。接口测试覆盖重复/缺失任务、未来事件、冒充来源、无效终点、状态冲突、回查续答、预算耗尽、进程中断、哈希篡改、伪造真实标记、unknown 用量及全轮停止。R=3 的调度及这些测试均待后续实现。
