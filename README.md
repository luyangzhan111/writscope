# WritScope（约界）

研究编码任务中的兼容性约束与用户授权状态维护：结合历史要求、候选补丁和影响证据，判断变更是否处于有效授权范围内，并提供可追溯依据。

**当前阶段：准备材料已验收，M1 首例与验证基础设施已实现。** 可以独立复现原始代码、破坏补丁和兼容修复的 HTTP/页面结果。尚无授权检查器、模型实验或方法性能结论；授权答案仍是参考标签。

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

后续为两种完整上下文基线 → 增量授权状态组件 → 首轮 30 实例开发评测。真实调用前仍须确定模型、凭据和预算。项目仅为本地仓库，未发布 GitHub；许可证及包名发布可用性待定。
