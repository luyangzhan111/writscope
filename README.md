# WritScope（约界）

研究编码任务中的兼容性约束与用户授权状态维护：结合历史要求、候选补丁和影响证据，判断变更是否处于有效授权范围内，并提供可追溯依据。

**当前阶段：M1、M2a 已验证；M2b 新版 `source_quote_v1` 六任务真实开发运行得到 5/6 有效输出；M3a 最小增量状态内核已离线实现并验证。** 首次旧引文位置契约的六个无效输出原样保留。M3a 使用手工结构化提案测试状态机制，尚无模型逐事件提取或增量方法成绩；没有方法优劣、成本优势或泛化结论。

## 第一版边界

- Python / FastAPI 合成样例；固定候选补丁的离线复核。
- 聚焦 HTTP 输出字段存在性与值语义；分别输出 `impact`、`authorization`、`decision`。
- 比较完整上下文直接复核、完整上下文清单复核（核心强基线）、增量授权状态复核。
- 各方法使用同等原始证据；提取、更新、回查、验证及失败重试全部计费。
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

协作约定见 [AGENTS.md](AGENTS.md)。这些设计文档含参考答案，不能直接作为被测方法的输入。

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

首次 M2b 实际接通与独立评分已执行；原始引文位置失败见 [真实接通记录](docs/m2b-real-review.md)。新版 `source_quote_v1` 让模型提交唯一原文、由运行器派生位置；[新一次真实开发运行](docs/m2b-quote-real-review.md)得到 5/6 有效输出，独立保存，不改写首次负结果。M3a 的离线内核已落地；模型提取集成与首轮 30 实例开发评测仍待后续决策。项目仅为本地仓库，未发布 GitHub；许可证及包名发布可用性待定。

## 运行 M3a（无模型调用）

```powershell
.\.venv\Scripts\python.exe -m writscope.m3a run --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --proposals evaluation_private/case_001/m3a_manual_proposals/c002.jsonl --output reports/local/m3a-demo-c002
.\.venv\Scripts\python.exe -m writscope.m3a verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --run reports/local/m3a-demo-c002
```

示例输出目录必须事先不存在。私有目录中的提案是**人工诊断输入**，仅用于验证状态内核；不会发送给两种完整上下文方法，也不能当作增量方法预测。实际三历史运行与边界见 [M3a 验收](docs/m3a-review.md)。
