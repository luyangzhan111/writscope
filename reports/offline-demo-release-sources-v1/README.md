# WritScope（约界）

研究编码任务中的兼容性约束与用户授权状态维护：结合历史要求、候选补丁和影响证据，判断变更是否处于有效授权范围内，并提供可追溯依据。

例如：页面需要 `state`，但旧 API 必须保留 `status`。客户端本地映射的许可，能否覆盖服务端删除旧字段的补丁？WritScope 分别记录补丁影响 `impact`、有效用户授权 `authorization` 和最终决定 `decision`，保留来源、作用范围、撤销及替代关系。

**当前阶段（2026-11-04）：S4-A 离线工程更新已公开，Windows／Ubuntu 24.04 CI 通过；S4 正式比较未运行。** 当前维护入口覆盖完整上下文直接复核、完整上下文清单复核（核心强基线）与增量复核。现有证据不支持方法优势、成本收益或泛化结论。

项目采用 [MIT](LICENSE)，已公开于 [GitHub](https://github.com/luyangzhan111/writscope)。本次更新与实际受测提交见 [双平台发布记录](docs/s4/github-publication-20261104.md)。当前文档从 [S4 导航](docs/s4/README.md)开始；[旧文档导航](docs/README.md)保留其哈希绑定原文。

## 当前证据与边界

| 证据 | 已完成 | 不能据此声称 |
| --- | --- | --- |
| 当前离线候选 `0ef9be84…` | 263 份源码/规格/提示词/测试绑定；51 项定向测试通过；9 组固定控制、67 次模拟尝试；恢复核对 1,038 份清单工件、11 份报告重算一致；942 份既有文件未变 | 真实模型已遵守新契约、当前版本 270 轨已验收或方法有收益 |
| S4 v5 保存的真实单次试跑 | 三方法均因输出契约失败，原始响应、评分、费用与失败记录保留；离线回放通过 | 有效主终点；真实 no_change 已触发；完整增量终点已验证 |
| 旧 R5 全批离线记录 | 当时版本的 30 例×三方法×R=3 共 270 轨完成 | 当前契约版本也已完成 270 轨 |
| S3 历史开发验收 | v8 有真实自主回查、原文返回、续答接受和有效终点，满足作者审核开发口径 | 独立研究验收或 S4 正式比较完成 |

当前参考仍为 **provisional**，逐例未签收；S4 实际独立审核状态为 `not_performed_skipped_by_user`。新契约只编译公开声明的重复字段与操作覆盖，不证明自然语言解释正确，也不追改旧结果。详见 [当前离线验收](docs/s4-contract-convergence-offline-20261104.md)、[v5 真实失败复核](docs/s4-owner-pilot-real-v5-review-20261104.md)与[项目状态](docs/project-status.md)。

## S4 无密钥恢复、回放与演示

当前冻结证据要求 **Python 3.12.4** 与锁文件中的依赖版本，在仓库根目录执行：

```powershell
uv sync --locked --python 3.12.4
uv run --locked python -m scripts.s4_public_release restore --destination .work/s4-public-review
uv run --locked python -m scripts.s4_public_release verify --root .work/s4-public-review
uv run --locked python -m scripts.s4_public_release demo --root .work/s4-public-review
```

目标目录必须不存在；再次恢复时换一个新目录。新发布工具及 `reports/public-s4-v0.2/` 的本轮本地检查单独记录在[发布说明](docs/s4/public-release.md)，不把本文示例命令视为已经通过的远程 CI。

恢复根独立于当前工作树，包含带哈希的原始工程证据。`verify` 只重建保存轨迹；`demo` 使用固定响应，不是模型成绩。这些操作不读取 API 密钥、不发起模型请求。安装依赖可能访问软件包源，依赖就绪后的演示和回放可离线执行。任何历史价格、预留金额、批准或已用收据都不提供新的运行权限。

## 验证范围

本次公开候选使用明确的检查集：旧版已跟踪的 48 个测试文件（47 个测试模块及 conftest）、当前契约的 51 项定向测试以及新增发布/扫描安全测试。它不是全仓库所有历史 S4 测试的重跑；各部分的实际结果及运行根在[发布说明](docs/s4/public-release.md)中单列。

旧首发的 369 项 CI 与本地准备的 482 项分别保留原时点；本次 [新远程 CI](https://github.com/luyangzhan111/writscope/actions/runs/41792070076) 在 Windows 和 Ubuntu 24.04 均成功。Ubuntu 日志记录 494 项通过；Windows 检查集、回放与演示步骤成功。Linux 使用单独绑定、仅固定响应的发布适配层，未开放真实发送。当前版本的 30×3×R=3 全批、真实模型完整增量链和正式比较仍待后续冻结与授权。

## 保留的 S3 首发入口

以下命令对应 `public-v0.1` 的 S3 精选证据，仍保留历史用途：

```powershell
uv run --locked python -m scripts.public_release restore
uv run --locked python -m scripts.public_release demo
uv run --locked python -m scripts.public_release replay
```

旧演示为固定脚本五事件、历史回查、续答和终点；旧回放重建 v8 成功、v7 失败及九任务共同覆盖，均不增加模型请求。迁移适配只在 verifier 内存视图定位旧机器启动路径，原收据和哈希不改，也不能授权付费运行。详情见[旧精选证据说明](reports/public-v0.1/README.md)、[S3 最终审查](docs/experiments/s3-development-final-audit-20261027.md)、[首次公开版整理](docs/archive/releases/first-public-release.md)及[首次 GitHub 发布记录](docs/archive/releases/github-first-publication-20261028.md)。

## 项目范围与文档

- Python / FastAPI 合成样例；固定候选补丁的兼容性与授权复核。
- 三方法使用同等原始证据与工具权限，完整上下文清单复核是核心强基线。
- 提取、更新、回查、终点、验证与失败成本全部计入，缺失用量或账单为 unknown。
- 参考、配对、隐藏测试和参考修复只供评测器使用，不进入方法请求；目录名本身不是隔离。
- `allow` 仅表示通过所评估的兼容性与授权检查，不代表全部质量或安全检查通过。

| 阅读目的 | 入口 |
| --- | --- |
| 接续当前 S4 工程 | [S4 当前导航](docs/s4/README.md) · [公开候选说明](docs/s4/public-release.md) |
| 查看完成程度与剩余工作 | [项目状态](docs/project-status.md) |
| 理解当前输出与编译规则 | [compact contract v2](specs/s4-compiled-contract-v2.md) |
| 使用历史组件 | [组件级运行指南](docs/usage.md) · [旧导航](docs/README.md) |
| 理解数据与公平评测 | [数据契约](specs/data-contracts.md) · [评测协议](docs/evaluation-protocol.md) |
| 检查成功、失败与历史记录 | [实验记录](docs/experiments/README.md) · [历史归档](docs/archive/README.md) |

协作约定见 [AGENTS.md](AGENTS.md)。冻结材料保留各自时点，部分内容含作者答案，不得整体作为被测方法输入。
