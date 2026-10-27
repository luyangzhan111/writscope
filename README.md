# WritScope（约界）

研究编码任务中的兼容性约束与用户授权状态维护：结合历史要求、候选补丁和影响证据，判断变更是否处于有效授权范围内，并提供可追溯依据。

例如：页面需要 `state`，但旧 API 必须保留 `status`。客户端本地映射的许可，能否覆盖服务端删除旧字段的补丁？WritScope 分别记录补丁影响、有效用户授权和最终决定，并保留可回放的来源链。

**当前阶段（2026-10-27）：S3 开发验收完成（作者审核口径），独立研究验收待外部审核；S4 正式比较尚未运行。** v8 已完成真实自主历史回查、原文返回、续答接受及有效终点；最终复验通过，旧九任务共同报告保留六完成、三失败。现有证据不支持方法优势、成本收益或泛化结论。详情见 [最终审查](docs/s3-development-final-audit-20261027.md)与[项目状态](docs/project-status.md)。

本地首次公开候选版：源码、规格、测试与精选历史证据一起整理；许可证为 [MIT](LICENSE)。上传状态和本次实测见[首次公开版整理](docs/first-public-release.md)。接手开发请读[项目接手报告](docs/project-handoff-20261027.md)，其中工作树和本机路径描述保留编写时快照。

## 快速开始：无密钥离线演示

要求 Python 3.12 与 uv，在仓库根目录执行：

```powershell
uv sync --locked --python 3.12
uv run --locked python -m scripts.public_release demo
```

演示从带哈希清单的[精选证据包](reports/public-v0.1/README.md)恢复必要依赖，随后用固定脚本完成五事件、历史回查、续答与终点，输出新报告路径。输出应包含 `execution_kind=offline_simulation`、`new_model_calls=0`、`status=completed` 与 `scripted_attempts=7`。报告中的 `offline_transport_only` 表示回查接线控制；脚本结果不计模型成绩。

这些命令无需 API 密钥，不发送模型请求。安装依赖可能访问软件包源；演示和回放在依赖就绪后可离线运行。重复执行会生成独立目录，不覆盖旧记录。Windows 已实际验证；Linux/macOS 的验证状态以 CI 实际运行结果为准。

## 重放保存的真实开发证据

```powershell
uv run --locked python -m scripts.public_release replay
```

该命令离线重建 v8 成功轨迹、v7 失败轨迹和九任务共同覆盖，重新生成作者审核报告，并再次核对原始证据字节。输出 `new_model_calls=0`；`v8_saved_calls=7` 等字段指历史调用，绝非本次新调用。v7 应保持终点无效，九任务应保持六完成、三失败。v8 与旧共同轮不能拼接成方法排名。

历史一次性启动收据含原机器绝对路径；迁移适配只在 verifier 内存视图中定位到当前目录，输出逐项记录，原收据和原哈希不改。这种适配只用于离线验证，不能授权新的付费运行。具体规则见[证据说明](reports/public-v0.1/README.md)。

## 测试

```powershell
uv run --locked python -m scripts.public_release restore
uv run --locked python -m pytest -q -p no:cacheprovider
```

先恢复归档依赖，再运行测试。`evaluation_private/` 是评测器材料，可以随仓库提供，但不得进入模型请求；目录名本身不提供隔离。离线预检中的历史价格和预算只是冻结记录，不能当作新的授权或当前报价。

## 第一版边界

- Python / FastAPI 合成样例；固定候选补丁的离线复核。
- 聚焦 HTTP 输出字段存在性与值语义；分别输出 `impact`、`authorization`、`decision`。
- 比较完整上下文直接复核、完整上下文清单复核（核心强基线）、增量授权状态复核。
- 正式比较要求各方法使用同等原始证据；提取、更新、回查、验证及失败重试全部计入总成本。
- `allow` 仅表示通过所评估的兼容性与授权检查，不代表通过全部质量或安全审查。

## 阅读顺序

1. [项目交接书](docs/project-brief.md)：初始需求依据，保留原文。
2. [实现计划](docs/implementation-plan.md)：四阶段任务、依赖和验收门槛。
3. [数据契约](specs/data-contracts.md)：事件、状态、影响、判断与运行记录。
4. [首例设计](specs/first-case.md)：同一 v1 字段替换补丁的三种授权历史。
5. [评测协议](docs/evaluation-protocol.md)：公平比较、隔离、评分与成本。
6. [决策记录](docs/decisions.md)：已确定事项、工程选择与待决事项。
7. [准备阶段审核](docs/preparation-review.md)：环境检查、产物、缺口和进入条件。
8. [M1 验收与运行说明](docs/m1-review.md)：准备复核、实际观测、复现步骤和剩余边界。
9. [M2a 验收](docs/m2a-review.md)：两基线、离线流程、失败/成本记录与实际验证。
10. [M2b 离线验收](docs/m2b-offline-review.md)：DeepSeek 接口、预算边界、预检记录、密钥设置与待运行项。
11. [M2b 首次真实接通](docs/m2b-real-review.md)：六任务实际状态、成本估算、共同校验失败及后续边界。
12. [M2b 引文契约验收](docs/m2b-quote-contract-review.md)：离线修订、来源边界、验证结果和后续条件。
13. [M2b 新版真实验证](docs/m2b-quote-real-review.md)：六任务新运行、有效输出、失败类型与费用边界。
14. [研究方向与接下来的两步](docs/research-direction-2026-10-20.md)：近期论文/开源项目对照、创新边界、技术栈与短期验收。
15. [M3a 离线状态内核验收](docs/m3a-review.md)：追加转换、局部例外、撤销重授、范围匹配及回放记录。
16. [M3b 离线增量管线验收](docs/m3b-review.md)：逐事件请求、脚本响应、成本字段、失败停止与回放。
17. [M3s c003 真实开发诊断](docs/m3s-real-review.md)：完整路径的单例真实运行、回放、评分与费用边界。
18. [M3t 阶段收尾复核](docs/m3t-closeout-review.md)：S3 退出条件、旧失败、比较缺口与 S4 前置项。
19. [S3.4 隔离诊断设计](docs/s3-4-isolated-diagnostic-design.md)：人工状态隔离、逐前缀归因与成本核查的版本化设计。
20. [S3.4 c003 单例诊断](docs/s3-4-c003-isolated-review.md)：单人审核、离线结构对照、原运行成本对账与剩余限制。

协作约定见 [AGENTS.md](AGENTS.md)。上述初始需求和阶段记录保留各自当时状态；最新进度以接手报告、最终审查与项目状态为准。这些设计文档含参考答案，不能直接作为被测方法的输入。

## 运行 M1

在仓库根目录，用 Python 3.12 与 uv 创建/同步独立环境：

```powershell
uv --cache-dir .work/uv-cache sync --locked --python 3.12
.\.venv\Scripts\python.exe -m writscope.m1
.\.venv\Scripts\python.exe -m pytest -q
```

Linux/macOS 使用 `.venv/bin/python` 替代 Windows 解释器路径；当前只在 Windows / Python 3.12.4 实际验证。无需运行 HTTP 服务端口或提供模型密钥。验证器使用 TestClient，在独立副本中执行真实应用代码；每次生成新的 `reports/local/m1-*` 目录，保存 JSON、Markdown、进程日志、JUnit XML 和公开输入包。显式 `--output` 只能指向不存在的新目录，避免覆盖旧记录。

实测报告见 [M1 执行记录](reports/m1-2026-10-18/report.md)，运行细节与权限问题说明见 [M1 验收](docs/m1-review.md)。依赖由 [uv.lock](uv.lock) 锁定；固定样例和输入哈希由 [manifest.json](fixtures/case_001/manifest.json) 约束。

## 运行 M2a（无需密钥）

```powershell
.\.venv\Scripts\python.exe -m writscope.m2 run --output reports/local/m2a-demo-run
.\.venv\Scripts\python.exe -m writscope.m2 score --run reports/local/m2a-demo-run --labels evaluation_private/case_001/labels.json --output reports/local/m2a-demo-score
```

两步分开运行：第一步只读公开输入和固定脚本，第二步才读参考标签。示例的两个输出目录必须事先不存在；再次运行请换新名称。省略 `--output` 会自动创建新的 `reports/local/m2a-*` 目录。默认脚本对三例给出同一判断，故意错判 c002，只用于验收管线。精选实际记录在 [M2a 报告](reports/m2a-2026-10-20/score/summary.md)，方法提示词在 [prompts](prompts)，脚本在 [examples/m2a](examples/m2a)。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## M2b 离线预检（无需密钥、不会联网）

```powershell
.\.venv\Scripts\python.exe -m writscope.m2b preflight --output reports/local/m2b-preflight-demo
```

输出目录须事先不存在；如需重复运行请换名称。预检核对两方法 × 三历史的全部公开输入、相同证据、请求哈希、源码/锁文件哈希与本地预算预留。精选预检见 [preflight.json](reports/m2b-2026-10-20/preflight.json)。首次真实运行已完成，但 **6/6 输出未通过引文位置校验**；单次试跑固定最多六次请求，预算上限 20 元，费用预留只是本地估算，不是提供商账单硬上限。

首次 M2b 实际接通与单独评分已执行；原始引文位置失败见 [真实接通记录](docs/m2b-real-review.md)。新版 `source_quote_v1` 让模型提交唯一原文、由运行器派生位置；[新一次真实开发运行](docs/m2b-quote-real-review.md)得到 5/6 有效输出，单独保存，不改写首次负结果。后续 S3 已完成作者审核开发验收，见 [v8真实记录](docs/s3-development-v8-real-review.md)；独立审核和首轮 30 实例 S4 评测尚未完成。首次公开候选版已采用 MIT；当前未上传 GitHub，也未发布 PyPI 包。

## 运行 M3a（无模型调用）

```powershell
.\.venv\Scripts\python.exe -m writscope.m3a run --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --proposals evaluation_private/case_001/m3a_manual_proposals/c002.jsonl --output reports/local/m3a-demo-c002
.\.venv\Scripts\python.exe -m writscope.m3a verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --run reports/local/m3a-demo-c002
```

示例输出目录必须事先不存在。私有目录中的提案是**人工诊断输入**，仅用于验证状态内核；不会发送给两种完整上下文方法，也不能当作增量方法预测。实际三历史运行与边界见 [M3a 验收](docs/m3a-review.md)。

## 运行 M3b（脚本响应，不调用真实模型）

```powershell
.\.venv\Scripts\python.exe -m writscope.m3b run --bundle reports/m1-2026-10-18/inputs/verified_impact/c001 --script examples/m3b/c001_script.json --output reports/local/m3b-demo-c001
.\.venv\Scripts\python.exe -m writscope.m3b verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c001 --run reports/local/m3b-demo-c001
```

输出目录必须事先不存在。脚本按固定顺序返回响应，不读取请求或参考标签；本例只验证逐事件接线与回放，不能当作模型结果。详见 [M3b 验收](docs/m3b-review.md)。
