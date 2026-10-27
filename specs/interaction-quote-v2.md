# interaction_quote_v2：按操作约束提案形状

日期：2026-10-26。实现：[v2契约](../scripts/s3_interaction_v2.py)，提示词：[interaction v2](../prompts/incremental_interaction_v2.md)。仅离线验证；不改写v1请求、提案内核或原真实运行。

## 模型结构

保留 `kind=lookup_request` 或 `kind=extract`。extract 是一条 no_change，或1–8条变更；两类分开定义，结构中不允许混用。所有字段包括null载荷仍为必填。

| operation | target_ids | constraint | authorization | supersedes |
| --- | --- | --- | --- | --- |
| add_constraint | 空 | 新约束 | null | 新约束中为空 |
| grant | 空 | null | 新授权 | 新授权中为空 |
| supersede（约束） | 非空约束ID | 替代约束 | null | 与target_ids逐项一致 |
| supersede（授权） | 非空授权ID | null | 替代授权 | 与target_ids逐项一致 |
| revoke | 非空、同类型ID | null | null | 无载荷 |
| mark_ambiguous | 非空、同类型ID | null | null | 无载荷 |
| no_change | 空 | null | null | 无载荷，只能单独出现 |

`authorization.constraint_refs`引用适用约束，不能借此把grant目标或授权替代列表填成约束ID。supersede必须有新内容，不能作为裸删除。局部授权仍是局部例外，不自动移除广泛约束。

JSON Schema显式展示operation常量、载荷类型、必填null、空/非空列表与批次数量；替代列表相等、重复ID和引用实际类型仍需本地验证。合法结构不证明自然语言授权语义正确。

## 解析与请求

v2先验证操作形状，再把**原响应文本**交给未修改的v1解析器；不清空列表、不删除条目、不猜操作。可信角色、唯一引文、截止点、回查限额及旧状态范围内核继续生效。

请求继续从v1公开白名单构造完整当前可见前缀及先前状态/背景，仅替换提示词、输出结构及 `output_contract=interaction_quote_v2`；配置哈希另含v2版本。请求/配置与v1不同，不沿用旧冻结或授权。

当前provider以JSON对象模式输出，schema是请求内容中的说明，**不是服务端严格结构解码保证**。本地v2校验会拒绝不合法输出；未验证新提示词能提高真实模型合法率。

## 当前离线边界

本步[诊断入口](../scripts/s3_proposal_shape_diagnosis.py)先回放原同轮保存记录，再重建c001/c002提取前缀与三历史15个无状态变更传输预览；不读取参考标签或人工状态，不生成模型成绩。

人工结构控制只用于评测器侧：清空原失败grant的两个错误列表，另存并去掉未执行的裸supersede，检查合法提案能提交且原约束不消失。它不修正或裁决原conditions、语义或作用域，不回填原运行、不用于模型请求、不移植原终点输出。

本步没有v2真实运行器或付费命令。未来接入需要独立版本的执行/回放适配与新预检，费用另行冻结批准。独立审核、完整九任务和自主回查的验收状态不由此改变。
