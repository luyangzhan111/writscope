# WritScope（约界）

WritScope 帮 API 维护者在合并前检查，旧客户端会失去哪项响应字段保证，以及哪条仍然有效的许可覆盖了这次变更。它读取本地 OpenAPI 文件，在报告中保留字段位置、授权原文和撤销记录。

例如：页面需要 `state`，但旧 API 必须保留 `status`。客户端本地映射的许可，能否覆盖服务端删除旧字段的补丁？WritScope 分别记录补丁影响 `impact`、有效用户授权 `authorization` 和最终决定 `decision`，保留来源、作用范围、撤销及替代关系。

## 审查自己的 API 变更

准备变更前后的 OpenAPI 3.0 或 3.1 JSON 文件。在装有 Git 和 uv 的终端执行一次：

```powershell
git clone https://github.com/luyangzhan111/writscope.git
cd writscope
uv sync --locked --python 3.12.4
```

把下面两个文件名换成你的文件路径。方法和路径使用契约里声明的值，包括花括号中的参数名：

```powershell
uv run --offline --locked writscope review --before before.openapi.json --after after.openapi.json --method GET --path '/api/v1/orders/{order_id}'
```

打开终端打印的 `index.html`。如果旧客户端依赖的 `status` 从必需字段中消失，报告会指出它的位置。有可信维护者台账时，用 `--ledger` 指向它。匹配的许可可以让本次改动通过，兼容性影响仍显示为 `breaking`。首版检查指定接口的 HTTP 200 JSON 响应；遇到无法判定的结构，结论为 `investigate`，需要人工复核。安装依赖后，复核可离线运行，无需模型密钥。

手头没有契约文件时，可用下面的命令在本机运行仓库自带的 FastAPI 订单接口，查看未授权、授权、撤销和兼容修复四份报告：

```powershell
uv run --offline --locked python -m examples.review_fastapi.run --repeat
```

[复核指南与台账格式](docs/offline-review.md) · [真实案例：Immich 接口迁移](docs/case-studies/immich-server-ping.md) · [MIT 许可证](LICENSE)

## 几分钟看懂三个案例

在源码目录安装依赖后运行：

```powershell
uv sync --locked --python 3.12.4
uv run --offline --locked writscope demo
```

命令会在当前目录的 `.work/writscope-demo-<唯一标识>/` 新建 `index.html`、`demo.json` 和 `manifest.json`，并打印文件路径。直接打开 `index.html` 即可查看；也可使用 `uv run --offline --locked writscope demo --open`。页面自包含，不需要启动服务。首次安装依赖可能联网，安装完成后的演示可离线运行，不读取模型密钥、不发送模型请求。

三个案例使用相同补丁和代码影响，只改变最后一条用户消息：

| 案例 | 用户允许的范围 | 影响 | 授权 | 决定 |
| --- | --- | --- | --- | --- |
| c001 | 保留 v1 契约，只做客户端本地映射 | breaking | not_covered | revise |
| c002 | 明确允许本次 v1 字段替换 | breaking | covered | allow |
| c003 | 只允许未来 v2 字段替换 | breaking | not_covered | revise |

页面可切换案例、查看历史与状态，并对照补丁、来源引文及逐影响结论。**明确授权的破坏仍记为 breaking**；`allow` 仅表示通过这里评估的兼容性与授权检查。

这是人工预置提案驱动的结构演示，使用现有状态内核计算；代码影响取自已保存的 M1 公开证据，本轮不执行候选代码。它没有运行模型，不提供模型准确率或方法比较成绩。完整命令、输出说明与开发入口见 [离线演示指南](docs/offline-demo.md)。

## 项目怎样工作

1. 从截止点前的可信历史与公开工件建立输入，排除参考答案、隐藏测试和参考修复。
2. 将用户约束、局部授权及其条件表示成状态，保留原文来源与生命周期。
3. 对每个已核验影响，检查对象、API 版本、字段、操作、条件及有效授权。
4. 分别输出影响、授权覆盖和最终决定，保存请求、失败、状态转换与成本，支持回放。

研究比较保留三种方法：完整上下文直接复核、完整上下文清单复核（核心强基线），以及逐事件增量维护后复核。三方法使用同等原始证据与工具权限，增量提取、回查、终点和失败成本全部计入。程序验证来源、结构和范围，不证明自然语言提取语义正确。

## 最新研究记录与边界

本地保存的 S4 方法 v8 已完成同一冻结方法的两块开发比较，每块为 30 例 × 三方法 × R=3，共 270 轨：

| 真实运行块 | 已结算 | 有效终点 | 失败 | 未运行 |
| --- | ---: | ---: | ---: | ---: |
| 等配置 | 270 | 244 | 26 | 0 |
| 预算匹配 | 270 | 243 | 27 | 0 |

两块分别通过保存记录的独立回放与冻结评分。这里的“独立回放”指重新计算记录，不代表独立研究审核。30/30 参考已由项目负责人确认；案例均参与过项目调试，实际独立研究审核为 `not_performed_skipped_by_user`。本批开发样本未观察到增量方法相对完整清单的总体收益，不支持泛化或产品效果结论。两块均未触发真实回查续答或预算耗尽，预算上限的边际影响未单独观测到。

各方法分子分母、失败、费用估算与 unknown 见 [v8 开发比较公开摘要](docs/s4/v8-development-closeout-20261109.md)。摘要从本地保存记录提炼，最新完整原始运行包与逐例审核原文未随本次演示整包公开。服务商实付账单仍未知；历史批准均不提供新的执行权限。当前工作树与 GitHub 已发布版本需分别核对，不能把本地新增功能视为已发布。

## 保留的 S4 发布恢复与回放

以下入口对应 **2026-11-04 的 S4 离线工程公开包 `public-s4-v0.2`**，保留该时点的契约、证据与检查集。它与上面的三案例学习演示用途不同。

当前冻结证据要求 **Python 3.12.4** 与锁文件中的依赖版本，在仓库根目录执行：

```powershell
uv sync --locked --python 3.12.4
uv run --locked python -m scripts.s4_public_release restore --destination .work/s4-public-review
uv run --locked python -m scripts.s4_public_release verify --root .work/s4-public-review
uv run --locked python -m scripts.s4_public_release demo --root .work/s4-public-review
```

目标目录必须不存在；再次恢复时换一个新目录。新发布工具及 `reports/public-s4-v0.2/` 的本轮本地检查单独记录在[发布说明](docs/s4/public-release.md)，不把本文示例命令视为已经通过的远程 CI。

恢复根独立于当前工作树，包含带哈希的原始工程证据。`verify` 只重建保存轨迹；`demo` 使用固定响应，不是模型成绩。历史发布按冻结恢复根回放；当前首页和新维护入口的更新不重签旧发布，也不更新原清单。这些操作不读取 API 密钥、不发起模型请求。安装依赖可能访问软件包源，依赖就绪后的演示和回放可离线执行。任何历史价格、预留金额、批准或已用收据都不提供新的运行权限。

## 2026-11-04 公开包验证范围

本次公开候选使用明确的检查集：旧版已跟踪的 48 个测试文件（47 个测试模块及 conftest）、当前契约的 51 项定向测试以及新增发布/扫描安全测试。它不是全仓库所有历史 S4 测试的重跑；各部分的实际结果及运行根在[发布说明](docs/s4/public-release.md)中单列。

旧首发的 369 项 CI 与本地准备的 482 项分别保留原时点；该次 [远程 CI](https://github.com/luyangzhan111/writscope/actions/runs/41792070076) 在 Windows 和 Ubuntu 24.04 均成功。Ubuntu 日志记录 494 项通过；Windows 检查集、回放与演示步骤成功。Linux 使用单独绑定、仅固定响应的发布适配层，未开放真实发送。历史验收结果不代表本次新演示或全仓库所有历史测试都已验证。双平台发布与精确受测提交见 [发布记录](docs/s4/github-publication-20261104.md)。

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
| 运行演示、理解案例、修改展示层 | [离线演示指南](docs/offline-demo.md) |
| 查看最近开发比较的公开摘要 | [v8 开发比较摘要](docs/s4/v8-development-closeout-20261109.md) |
| 复现旧 S4 公开包 | [S4 导航（11-04 时点）](docs/s4/README.md) · [公开候选说明](docs/s4/public-release.md) |
| 查看早期阶段与历史计划 | [历史项目状态](docs/project-status.md) |
| 理解当前输出与编译规则 | [compact contract v2](specs/s4-compiled-contract-v2.md) |
| 使用历史组件 | [组件级运行指南](docs/usage.md) · [旧导航](docs/README.md) |
| 理解数据与公平评测 | [数据契约](specs/data-contracts.md) · [评测协议](docs/evaluation-protocol.md) |
| 检查成功、失败与历史记录 | [实验记录](docs/experiments/README.md) · [历史归档](docs/archive/README.md) |

协作约定见 [AGENTS.md](AGENTS.md)。冻结材料保留各自时点，部分内容含作者答案，不得整体作为被测方法输入。
