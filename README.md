# WritScope（约界）

研究编码任务中的兼容性约束与用户授权状态维护：结合历史要求、候选补丁和影响证据，判断变更是否处于有效授权范围内，并提供可追溯依据。

例如：页面需要 `state`，但旧 API 必须保留 `status`。客户端本地映射的许可，能否覆盖服务端删除旧字段的补丁？WritScope 分别记录补丁影响、有效用户授权和最终决定，并保留可回放的来源链。

**当前阶段（2026-10-28）：研究开发版已公开；S3 开发验收完成（作者审核口径），独立研究验收待外部审核；S4 正式比较尚未运行。** v8 已完成真实自主历史回查、原文返回、续答接受及有效终点；最终复验通过，旧九任务共同报告保留六完成、三失败。现有证据不支持方法优势、成本收益或泛化结论。详情见 [最终审查](docs/experiments/s3-development-final-audit-20261027.md)与[项目状态](docs/project-status.md)。

开发版已上传 [GitHub](https://github.com/luyangzhan111/writscope)：源码、规格、测试与精选历史证据一并提供，许可证为 [MIT](LICENSE)。本地实测见[首次公开版整理](docs/archive/releases/first-public-release.md)，远程验证见[GitHub 发布记录](docs/archive/releases/github-first-publication-20261028.md)。开发文档从[文档导航](docs/README.md)开始；旧交接报告保留在历史归档中。

## 快速开始：无密钥离线演示

要求 Python 3.12 与 uv，在仓库根目录执行：

```powershell
uv sync --locked --python 3.12
uv run --locked python -m scripts.public_release demo
```

演示从带哈希清单的[精选证据包](reports/public-v0.1/README.md)恢复必要依赖，随后用固定脚本完成五事件、历史回查、续答与终点，输出新报告路径。输出应包含 `execution_kind=offline_simulation`、`new_model_calls=0`、`status=completed` 与 `scripted_attempts=7`。报告中的 `offline_transport_only` 表示回查接线控制；脚本结果不计模型成绩。

这些命令无需 API 密钥，不发送模型请求。安装依赖可能访问软件包源；演示和回放在依赖就绪后可离线运行。重复执行会生成独立目录，不覆盖旧记录。Windows 与 Ubuntu 已通过 GitHub CI 的完整测试、演示和历史回放；macOS 尚未验证。

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

## 文档

| 阅读目的 | 入口 |
| --- | --- |
| 了解当前项目与代码入口 | [文档导航](docs/README.md) |
| 查看完成程度与剩余工作 | [项目状态](docs/project-status.md) |
| 使用各阶段组件 | [组件级运行指南](docs/usage.md) |
| 理解数据与公平评测 | [数据契约](specs/data-contracts.md) · [评测协议](docs/evaluation-protocol.md) |
| 检查成功、失败与验收证据 | [实验记录](docs/experiments/README.md) |
| 查找旧计划、预检和交接材料 | [历史归档](docs/archive/README.md) |

协作约定见 [AGENTS.md](AGENTS.md)。实验与归档保留各自时点，部分内容含参考答案，不得整体作为被测方法输入。
