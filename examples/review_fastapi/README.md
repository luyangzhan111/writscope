# 自有 FastAPI 订单状态样例

这是为本项目编写的小型订单状态服务，用真实应用执行和本地契约复核连接一个可演示闭环。它不是生产服务、真实用户数据或外部项目案例；只运行本目录固定的自有代码。

路由始终为 `GET /api/v1/orders/{order_id}`。三版都明确允许额外响应字段（Pydantic `extra="allow"`，OpenAPI `additionalProperties: true`），不会把“新增字段是否兼容”交给隐含假设。

| 版本 | 必需响应字段 | 实测旧 `status` 客户端 | 实测新 `state` 客户端 |
| --- | --- | --- | --- |
| before | `order_id`、`status` | 成功 | 缺少 `state` |
| breaking | `order_id`、`state` | 缺少 `status` | 成功 |
| fixed | `order_id`、`status`、`state` | 成功 | 成功 |

`fixed` 保留原必需字段，同时提供新字段；旧契约明确允许额外字段。这里的兼容结论限定为声明的响应契约及本例实际观察，不涵盖完整业务语义。

## 导出真实应用证据

安装项目依赖后，在仓库根目录运行：

```powershell
uv run --offline --locked python -m examples.review_fastapi.export
```

导出器用 FastAPI `TestClient` 分别执行三版应用的 `/openapi.json` 和订单 GET，并实际运行两个客户端。输出默认保存在本目录 `.work/evidence-<唯一标识>/`，也可用 `--output <新目录>` 指定；已有目录拒绝覆盖。

| 产物 | 内容 |
| --- | --- |
| `before.openapi.json` | 原始响应契约 |
| `breaking.openapi.json` | 删除 `status` 后的响应契约 |
| `fixed.openapi.json` | 保留 `status` 并新增 `state` 的响应契约 |
| `observations.json` | 实际 HTTP 状态、JSON、客户端结果与 schema 声明 |
| `manifest.json` | 源码、依赖版本及导出文件哈希与范围说明 |

这一步会执行本目录自有应用，但不会读取或运行任意外部项目代码，不需要网络服务端口或模型密钥。

## 四阶段复核闭环

使用已安装的 WritScope `review` 命令运行：

```powershell
uv run --offline --locked python -m examples.review_fastapi.run --repeat
```

每次新建本目录 `.work/run-<唯一标识>/`。脚本先导出同一组应用证据，再以新的输出目录执行四次本地复核：

| 阶段 | 对照契约与维护者账本 | 预期三轴 |
| --- | --- | --- |
| 01-unapproved | before → breaking，未提供授权 | `breaking / not_covered / revise` |
| 02-approved | 相同变更，明确许可精确输入哈希下的 `status` 删除 | `breaking / covered / allow` |
| 03-revoked | 相同变更，追加撤销原许可 | `breaking / not_covered / revise` |
| 04-fixed | before → fixed，保留撤销记录 | `compatible / not_needed / allow` |

`--repeat` 会将每个阶段复跑至另一个新目录，检查 `review.json`、`index.html` 和 `manifest.json` 字节一致，并核对报告清单中的全部工件哈希。四阶段结论与复跑一致性不符合声明时脚本失败，所有已生成文件保留。未使用 `--repeat` 时仍核对四阶段结论与清单。

各阶段生成 `review.json`、`index.html` 和输入副本；脚本另存 stdout/stderr、`maintainer-approved.jsonl`、`maintainer-revoked.jsonl`、`review-source-binding.json` 及整个运行的 `run.json`。后者记录源文件和工件哈希、阶段命令、退出码、结果与复跑核对，并验证复核过程未改变导出证据。复核实现或展示源码在本次运行中发生变化时会拒绝把混合版本记为通过；不同版本的报告 HTML 不要求字节相同。

维护者账本由本地样例脚本显式编写，许可绑定两个输入文件的精确 SHA-256、路由、方法、字段和操作。它展示本地操作者提供的授权如何被使用和撤销，不代表已验证外部身份、任意 PR 评论或自然语言授权；不进入原研究的公平比较。

也可以直接检查一对已导出的契约，例如：

```powershell
python -m writscope review --before <导出目录>/before.openapi.json --after <导出目录>/breaking.openapi.json --method GET --path "/api/v1/orders/{order_id}" --output <新的报告目录>
```

首版 `review` 只支持其声明的响应 schema 子集；不支持的结构应保留 unknown/investigate，不从一次测试通过推出完整 API 兼容。此样例不启动模型、不产生模型调用费用，原实验工件与评分均不修改。
