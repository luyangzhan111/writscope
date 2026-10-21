# M3a 最小增量授权状态内核验收

日期：2026-10-20；2026-10-21 复核。状态：**离线内核与三历史诊断已实现并运行；模型逐事件提取、正式 incremental 方法及方法性能评测尚未运行。** 本步依据 [研究方向中的第二小步](research-direction-2026-10-20.md)、[数据契约](../specs/data-contracts.md)与 [首例设计](../specs/first-case.md)。

## 已交付

- [状态内核](../src/writscope/state.py) 接收严格的逐事件 `TransitionProposal`，从可信历史验证当前事件、角色、唯一精确引文及公开工件引用，派生来源跨度。`add_constraint`、`grant`、`revoke`、`supersede`、`mark_ambiguous`、`no_change` 均有明确结构；M3a 暂限**每条事件一次转换**。授权与约束保留原文、作用对象、版本、操作、条件、生命周期及更新关系。工具消息可以产生 `no_change`，不能产生授权。
- 局部许可生成独立 exception，**不删除宽约束**；撤销使旧授权失效，旧例外仍作为历史保留，重新授权生成新 ID 与例外。完整 supersede 要求新旧 subject 集合相同，避免较窄范围静默取代宽约束；语义是否真的得到用户许可仍取决于提取质量。
- 每次尝试（包括被拒绝的可信来源错误、重复事件和无效目标）进入追加式转换日志，记录接受状态、原因、前后状态哈希和处理耗时。状态哈希由规范 JSON、已处理原始事件前缀和转换引用确定；[离线 CLI](../src/writscope/m3a.py) 将提案副本、转换 JSONL、状态、逐影响判断及工件哈希分开保存，并能逐条回放验证。JSON/schema 错误另写 `input_error.json`，不产生状态或模型调用。
- 逐影响匹配使用 M1 已核验的 `ImpactEvidence`，精确核对 subject（含 API 版本）、`change_type`、旧/新字段、仍有效的约束例外及条件。`candidate_sha256` 与真实候选哈希重新核对；哈希不匹配即不覆盖，即使提案自称条件已满足。其他没有独立验证器的条件只会得到歧义，不凭提案自述放行。多影响仅在每项均覆盖时整体 `covered`；影响事实、授权范围和最终决定保持分离。
- 手工诊断提案保存于评测器侧 `evaluation_private/case_001/m3a_manual_proposals/`，只为复现内核行为。它们**不是模型生成状态或预测**，不进入完整上下文基线请求或公平排名。内核没有按 case ID 查表或读取私有标签；三种历史的差异来自各自提交的结构化事件提案。

## 实际验证

Windows / Python 3.12.4，M3a 定向测试 **17 passed**；项目完整回归 **75 passed**，`uv lock --check --offline` 通过。测试覆盖首例三历史、v1/v2、别名新增与字段替换、局部/多影响覆盖、撤销与重授权、同范围替代、部分替代拒绝、工具伪许可、未来/重复/伪造/重复引文、无效公开证据引用、歧义、候选哈希条件、语义变更许可、兼容/未知影响路由、无效 JSON/schema 记录、工件篡改与回放。测试中的结构化提案是人工输入，不能据此报告模型正确率。

2026-10-21 复核时修正了错误候选哈希与提案自称满足相冲突时的判断，由 `ambiguous` 收紧为 `not_covered`；随后定向 17 项和完整 75 项均重新通过。M2b 无网络预检的六请求集合哈希仍为 `3ba28e8a6939530101a7a6ad8957d38b095c8b2573c73c1fe7fc9baf26595377`，未将人工诊断输入混入完整上下文基线请求。

三份独立的 `manual_diagnostic` 最终运行位于 Git 忽略的 `reports/local/m3a-c001-verified-20261021/`、`m3a-c002-verified-20261021/`、`m3a-c003-verified-20261021/`，均为 5 条已接受转换、0 条拒绝、0 次模型调用。分别执行 `m3a verify` 后状态哈希一致。2026-10-20 的原始运行保留为修正前记录，不覆盖：

| 历史 | 实际影响 | 结构性授权覆盖 | 组合决定 | 状态 SHA-256 |
| --- | --- | --- | --- | --- |
| c001 未授权 | breaking | not_covered | revise | `e2a435f728e3b71655fc8e7544e8327712e8fb0f5fa0a5bcb84d4bd2e93f10cd` |
| c002 明确 v1 | breaking | covered | allow | `7f63b78e2a8dbd31d204944f38d3d65bfc5d92b77eab37609576fd0294afe5d2` |
| c003 仅 v2 | breaking | not_covered | revise | `01d087c8684a77ceb68fa5e017f6e71addb7cb60f2e760a18b08f69837558b0a` |

三份运行工件集合 SHA-256 依 c001/c002/c003 为 `fca13e8aad5eba8c440f6ab32b868d7b753773c7457d773d2090d2aad58ccafa`、`c2cf5345036401f400e47edc9074d415c267fa486bc58bab49983261098a61db`、`1b8e769f94b39c7460e2f95510cc072627815c178bd9dcf10bbfbe896ef18956`；原始运行目录被 Git 忽略，以上哈希用于核对本地记录，并不表示记录已公开发布。

本地记录保存每次状态更新及一次范围匹配的耗时；这次 c001/c002/c003 的内核更新时间分别为 **1.968 / 1.898 / 2.394 ms**，匹配时间为 **0.128 / 0.195 / 0.234 ms**。这些是单机单次诊断测量，不是模型方法的总成本或稳定性能指标。手工提案没有提供真实提取 token/费用，记录为 `null/unknown`，不能当作零；实际模型调用数确为 0。最终代码树哈希为 `2e0fc3fd29cdbfedec623717c38ef69420626a69c2ba3f1e9550fd0c55a1c2a3`，锁文件哈希为 `d4959a6b13e98f045a7bcf8b0ad597cb96fa3e6fc6dd9910dd81b75ccffb94bc`。

从仓库根目录独立复现 c002，使用**新的、不存在的**输出目录：

```powershell
.\.venv\Scripts\python.exe -m writscope.m3a run --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --proposals evaluation_private/case_001/m3a_manual_proposals/c002.jsonl --output reports/local/m3a-demo-c002
.\.venv\Scripts\python.exe -m writscope.m3a verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --run reports/local/m3a-demo-c002
```

## 边界

本步没有调用 DeepSeek 或任何真实模型，没有自动从自然语言提取约束/授权。**程序检查来源与结构，不能证明提案的语义忠于原文**；一条语义上错误但结构合法的授权仍可能被内核匹配，必须在后续方法评测中计入提取错误。现阶段 `task_context` 留空、每事件仅一个转换、没有跨事件回查调用或一般条件验证器；增量方法提示词/适配、完整前序提取费用、30 例评测与 B 轨仍是后续工作。M3a 的手工三例不能与 M2b 的真实完整上下文成绩比较。
