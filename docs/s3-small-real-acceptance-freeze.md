# S3 小型真实验收冻结与预算申请 v1

日期：2026-10-25。状态：**范围、价格快照、入口和预算提案已冻结，等待新的用户付费批准，真实请求 0 次**。本次是 c003 已调试开发实例的路径验收，不是完整 S3、独立样本或方法比较；独立审核仍在等待实际审核者填写。

## 冻结范围

| 项目 | 本次唯一范围 |
| --- | --- |
| 案例 / 次数 | c003；从空状态运行一次，不复用 M3s 的前缀状态，不自动补跑 |
| 方法 | incremental 完整路径；本次不运行 full_direct / full_checklist，也不排名 |
| 模型 / 接口 | DeepSeek `deepseek-flash`，固定 Chat Completions 端点；官方当日映射 DeepSeek-V4.1-Flash-1017，提供商别名不能钉死权重，须记录实际返回模型 |
| 配置 | temperature=0，thinking disabled，JSON 输出，每次至多 4096 输出 token，禁止 provider 搜索与任意代码执行 |
| 输入 | 原公开包、完整当前可见历史前缀、原交互及终点提示词；同公开证据和权限，不附作者状态、标签或审核者答案 |
| 回查 | 模型自行请求；只读已见原文，最多两次、每次最多两条事件；不隐藏原文、不强化提示强制触发 |
| 调用 | 5 次逐事件初始提取 + 最多 2 次回查续答 + 1 次终点，合计至多 8 次 provider 尝试 |
| 失败 | 单并发，每次 90 秒，无重试；provider、超时、用量缺失/越界、输出、回查、状态或预算首次失败即停；保留费用与原始失败 |
| 大小 | 每次请求及解析后的响应文本分别至多 100000 UTF-8 字节；逐次预留，失败不释放预留 |
| 扩展 | 不含其他案例、提示词修订、强制触发、补跑、方法比较或人工状态入模 |

c003 已在 M3s 上运行过且未触发回查，本次同样可能不触发。此风险是冻结的接受条件之一；不为凑回查次数追加费用或改变提示词。

## 验收输入与输出

版本化的[预检](../scripts/s3_small_real_preflight.py)和[真实运行器](../scripts/s3_small_real_runner.py)另立于旧 M3r/M3s，复用未改动的 M3p 交互流程。本次新计划绑定此前的[离线候选](s3-exit-preparation.md)，没有改写候选、历史真实响应或评分。

执行输入是本次冻结计划、首请求、当前源码及**来自本次用户直接批准**的独立授权凭据。运行器先验证计划/源码/案例/金额/次数/价格复核时间，再读取环境密钥；真实发送前以计划哈希建立本地排他的一次性启动记录，其他输出目录也不能复用同计划。该记录是本地防误补跑机制，不是操作系统或提供商账单权限边界。启动已占用后失败也不自动删除记录。

每次实际发送前保存请求、字节哈希、预留和尝试记录；保存响应原文、provider ID/实际模型、finish reason、用量和阶段成本。终点只有在五事件全部接受后才允许；最后保存状态、任务背景、回查原文链、终点三轴及错误。离线重建须验证请求、状态、费用、失败停止与结果分类，参考评分只可在回放后另行读取评测器私有材料。

判定预先固定为：

- **完整的小型回查验收通过**：真实执行完成五事件和有效终点，模型自行请求的回查、原文返回、续答及终点证据链完整，保存后独立回放通过。
- **完成但未回查**：记 `not_exercised`；只能说明常规真实路径完成，回查验收保持未验证，不自动补跑。
- **失败或不完整**：保存失败及全部已发生尝试，不生成有效终点或补造回查证据；回查链数量不齐单列。
- **脚本 / 强制控制**：分别只能是 `offline_transport_only` / `prompted_transport_control_only`，不能满足自主回查验收。

语义三轴、误放行/误拦截和来源问题另行核对，参考仍标为项目作者草稿，不假称独立金标准。路径成功不证明模型语义准确；本次任何结果都不能关闭完整 S3 或同轮三方法报告缺口。

## 费用提案与新授权边界

2026-10-25 13:11:46 UTC 核对[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)：Flash 峰时缓存未命中输入 $0.3 / 百万 token、输出 $1.2 / 百万 token。本地采用显式保守系数 10 元/美元，**不是实时汇率**，对应输入 3 元/百万、输出 12 元/百万。即使当前非峰时仍按此上界预留。

每次预留 = `((请求字节数 + 8192) × 3 + 4096 × 12) / 1000000` 元。

满额最坏预留 = `8 × ((100000 + 8192) × 3 + 4096 × 12) / 1000000` = **2.989824 元**。拟申请 **3.00 元人民币本地预留上限**，本次只允许一次轨迹、最多八次尝试。失败尝试保留预留，未知用量与账单不记零；提供商实际账单可能不同，本地上限不是提供商硬限额。

授权范围为 `S3_small_c003_lookup_acceptance_v1`；M3s 等旧授权已用，不能复用。审批后运行前还须在 24 小时内复核官方价格；若涨价超出冻结上界或计划/入口变化，停止并重新冻结申请，不能悄悄扩大原授权。

## 冻结工件与已运行检查

- [最终计划](../reports/local/s3-small-c003-preflight-20261025-final/plan.json)、[首请求](../reports/local/s3-small-c003-preflight-20261025-final/first_request.json)和[预检记录](../reports/local/s3-small-c003-preflight-20261025-final/run.json)已 prepare 后独立 verify 成功；状态仍为 `draft_unapproved`。
- 计划规范 SHA-256：`d735cdf48579d7ebf24b44dbd79426eba89a74d79e864189e125b9b8e4a62d28`。
- 真实入口 SHA-256：`a8a46eee83de40207f5880eea381280fb048b3161997cd0f08e34cdf6f01a9c5`；预检源码：`77eff0dfd62818a66ea3ae6b9e58c0bd91d63d9d1da4545a2fce7ecc111686da`。
- [本次入口测试](../tests/test_s3_small_real_acceptance.py) 18 项及[既有隔离/准备测试](../tests/test_s3_exit_preparation.py) 11 项，合计 **29 项通过**；覆盖完整八次、首次错误、缺用量、无效输出、超时、跨版本范围拒绝、终点失败、Decimal 费用、请求/分类篡改、旧授权/缺授权、过期核价、错误源码绑定、排他启动及用量越界。
- 三份独立保存后回放的演练：[两次回查 / 八次脚本尝试](../reports/local/s3-small-c003-sim-two_lookup-20261025/run.json)、[不回查 / 六次脚本尝试](../reports/local/s3-small-c003-sim-no_lookup-20261025/run.json)、[首次 provider 错误 / 一次脚本尝试](../reports/local/s3-small-c003-sim-provider_error-20261025/run.json)。三者新增真实请求均为 0，所有回查均只记离线传输控制。

Windows 受限环境的 asyncio 本地通信曾阻塞测试；终止后在获准的本地执行环境完成上述脚本测试和回放，全程没有真实 provider 请求或密钥读取。早期 `s3-small-c003-preflight-20261025` 目录为实现过程草案，本次仅以带 `-final` 的冻结目录为执行输入。

无网络复验入口：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_small_real_preflight verify --output reports/local/s3-small-c003-preflight-20261025-final
.\.venv\Scripts\python.exe -m scripts.s3_small_real_runner verify --preflight reports/local/s3-small-c003-preflight-20261025-final --output reports/local/s3-small-c003-sim-two_lookup-20261025
```

用户批准前不创建 approved 凭据、不读取模型密钥、不执行 real。预算申请本身不代表付费批准。
