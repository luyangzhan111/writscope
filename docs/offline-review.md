# 离线 API 契约复核

`writscope review` 对比两份本地 OpenAPI JSON，识别指定接口的响应必需字段是否失去保证，并依据维护者台账给出影响、授权覆盖和最终决定。它提供给开发者复核自有变更的入口，沿用 WritScope 的三轴结论和可追溯证据。

## 复核自己的文件

按[首页安装步骤](../README.md)安装后，从源码目录执行。把文件名、方法和路径换成实际值。路径需要保留 OpenAPI 中的模板参数，例如 `{order_id}`。

```powershell
uv run --offline --locked writscope review --before before.openapi.json --after after.openapi.json --method GET --path '/api/v1/orders/{order_id}'
```

如果有单独保存的可信许可记录，可追加 `--ledger approvals.jsonl`。省略台账时，报告只会用已提交的材料判断覆盖情况。一个 `not_covered` 结论表示本轮没有匹配的有效许可记录。项目成员可能在其他渠道讨论过这项变更。

| 最终决定 | 开发者下一步 |
| --- | --- |
| `revise` | 打开受影响字段，保留旧契约，或请有权决定的维护者确认这次变更的精确范围。 |
| `investigate` | 查看报告中的证据缺口，人工检查未解析的结构或其他变化。 |
| `allow` | 继续其余审查。若影响为 `breaking`，仍需按团队流程处理客户端迁移。 |

默认在当前目录的 `.work` 下创建独立结果目录，并打印 HTML 和 JSON 路径。也可用 `--output .work/order-review` 指定新目录。报告包括 `review.json`、自包含页面 `index.html`、原始输入副本 `inputs/` 和逐文件 SHA-256 清单 `manifest.json`。从报告目录按页面末尾的说明复算。输入副本及台账包含原文，请按其原有访问范围保管。

命令成功生成报告时退出码为 0。CI 应从 `review.json` 的 `decision` 读取业务决定。输入格式错误、非法台账或输出路径冲突会退出 1。完整报告以最后写入的 `manifest.json` 为准。

真实外部契约的运行步骤和首轮发现见 [Immich 接口迁移案例](case-studies/immich-server-ping.md)。

## 复现 FastAPI 样例

在已经安装锁定依赖的源码目录运行以下命令。

```powershell
uv run --offline --locked python -m examples.review_fastapi.run --repeat
```

样例实际启动三个自有 FastAPI 版本，由进程内 TestClient 请求 `GET /api/v1/orders/{order_id}` 和 `/openapi.json`。旧版响应含 `status`，破坏版改为只返回 `state`，修复版保留 `status` 并增加 `state`。旧契约明确允许额外字段。脚本保存三个 OpenAPI 文件、实际响应、两种客户端的读取结果和各阶段报告。

| 阶段 | 影响 | 授权 | 决定 |
| --- | --- | --- | --- |
| 同一破坏，无有效许可 | `breaking` | `not_covered` | `revise` |
| 同一破坏，精确许可 | `breaking` | `covered` | `allow` |
| 同一破坏，撤销许可 | `breaking` | `not_covered` | `revise` |
| 保留旧字段并新增字段 | `compatible` | `not_needed` | `allow` |

`--repeat` 使用同一输入复算四个阶段，校验每个阶段的 HTML、JSON 和清单字节一致。实际响应是自有样例的本地运行记录，许可是演练台账。[样例用法](../examples/review_fastapi/README.md)

## 许可与撤销

`--ledger` 由本机操作者从可信位置单独指定。建议把台账放在候选变更无写权限的位置。工具按输入原文和顺序执行匹配，维护者身份由操作者确认。

每行是一个 JSON 对象。`event_id` 在整个台账中唯一，`source` 记录可审核的授权或撤销原话。

- `operation: "grant"` 还需 `method`、`path`、`field`、`change_type: "remove_required_response_field"`、`before_sha256` 和 `after_sha256`。哈希取报告中两份输入的 SHA-256。路径使用 OpenAPI 中的精确模板。
- `operation: "revoke"` 还需 `target_event_id`，指向前面仍有效的一条 grant。撤销后该 grant 继续出现在报告里。重新授予时使用新的 ID。

目录中的 `inputs/ledger.jsonl` 在有台账的阶段保存了完整示例。只要任一输入字节、方法、路径或字段不匹配，该许可就不会覆盖影响。每个破坏字段都需要独立覆盖。

## 首版检查边界

- 检查 OpenAPI 3.0 或 3.1 JSON 中指定方法和路径的 HTTP 200 `application/json` 响应。
- 支持顶层简单对象、无额外约束的标量字段、布尔形状策略和同一文档内的 `components/schemas` 引用。缺少目标响应、外部引用、组合结构、已保留字段类型变化等会给出 `investigate`。
- 标量字段可以标记布尔 `readOnly`。出现在响应的 `required` 中时，仍按必需字段检查。`writeOnly` 的响应字段留给人工复核。
- 报告记录旧字段是否仍为必需。字段从必需改为可选也属于保证的丢失。若同时出现新字段，报告只陈述这一已观察到的契约变化。
- `compatible` 和 `allow` 的范围是报告声明的响应契约规则。示例脚本单独保存运行时检查，`review` 本身只解析选择的输入数据。

演示入口、已有实验报告和历史评分继续由各自的版本化工具维护。[离线演示](offline-demo.md) · [历史评测协议](evaluation-protocol.md)
