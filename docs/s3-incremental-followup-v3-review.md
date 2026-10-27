# S3 增量提取补充：回查可达性与无权威工具事件

日期：2026-10-26。根据 S3 v3 九轨真实补充轮的保存失败，另立交互版本、离线运行器与真实运行候选。**本步新增真实请求 0 次，尚未获本轮预算或外发授权。** 原 plan `1fa3e130…`、13 次请求、错误、报告与评分均未改写。

## 真实失败归因

- c001 与 c003 在工具事件处提交了来自旧用户事件的 `source_quote`。v3 提示词要求“当前可信用户事件”引文，却没有明确规定工具事件时如何输出无操作；v2 解析器按当前事件唯一引文校验并拒绝。这是提示词的时点规则缺口，拒绝保持正确：旧引文不能冒充当前事件来源。
- 工具事件不具备用户授权。可安全提交的表示只有空 `task_context` 加一个 `no_change`，引文精确来自当前工具事件，且只证明该无操作条目的出处，不赋予内容任何要求或授权效力。
- c002 同一步为真实 provider `ConnectError`。它是已保存的传输失败，不应由提示词或重试修复。
- 原轮初始请求包含截止点前全部历史正文，工具已可用但没有实际信息增益。不能把这组“完整历史已可见”的轨迹说成自主回查已验证。

## 新版实现

- 新交互契约 [`s3_interaction_v3.py`](../scripts/s3_interaction_v3.py) 保留 v2 的严格形状、引文、可信来源和范围规则，协议写在 [`s3-incremental-v3.md`](../specs/s3-incremental-v3.md)。初始/后续请求只显示当前事件；之前事件仅提供 `event_id/seq/role` 索引，模型自行请求并通过时点/数量校验后，原文才进入续答请求。完整原始公开包的 evidence hash 和工具权限保持绑定不变。解析仍对本地完整可信事件校验，绝不修补响应。
- 新提示词 [`incremental_interaction_v4.md`](../prompts/incremental_interaction_v4.md) 明确：用户事件只引用当前用户原文；非用户事件必须空任务背景且只能提 `no_change`，其引文只是出处；缺失旧文精确上下文时可按需请求回查。
- 新真实入口 [`s3_coverage_v3.py`](../scripts/s3_coverage_v3.py) 仅运行 c001/c002/c003 三条增量补充轨迹，不重跑已完成的六条基线，也不跨轮拼接做方法排名。每条最多五次提取、两次回查续答、一次终点；每轨最多 3.00 元、全轮最多 24 次与 9.00 元。普通轨迹错误停止该轨并继续下一案例；预算或完整性故障停止全轮。单并发、90 秒/次、无重试、首次失败不补跑。评测器 [`s3_coverage_report_v3.py`](../scripts/s3_coverage_report_v3.py) 先重建轨迹，再单独读取作者草稿参考。

## 离线验证

- 新增 [`test_s3_interaction_v3.py`](../tests/test_s3_interaction_v3.py)：4 项通过，覆盖旧事件正文隐藏、事件索引与公共 evidence hash、工具事件无权威 no-change、拒绝工具事件任务背景、回查后仅在续答返回原文。
- 新计划预检 [`s3-incremental-v3-preflight-frozen-20261026`](../reports/local/s3-incremental-v3-preflight-frozen-20261026/) prepare 与独立 preflight-verify 成功。plan SHA-256：`ead6921e1716cf79bec8c2e8ee9177cbe47cbbdb465d0cb9c50f25f5e8515233`，状态 `draft_unapproved`，真实请求 0。
- [`待运行报告`](../reports/local/s3-incremental-v3-pending-report-frozen-20261026/) 已生成三条 `not_run` 空行、无模型指标，不读参考标签。
- 保存的 21 次脚本控制轨迹 [`离线演练`](../reports/local/s3-incremental-v3-offline-lookup-frozen-20261026/) 独立回放及 [`报告`](../reports/local/s3-incremental-v3-offline-lookup-frozen-20261026-report/) 重建成功：三案例各完成五个事件、脚本构造一次回查、在终点注入合成 provider 错误后按规则停轨。`actual_model_calls=0`；lookup 分类 `offline_transport_only`，不能证明模型自主回查或真实终点。
- `git diff --check` 通过。原始 v3 真实失败记录不变。

## 真实候选成本与待授权

2026-10-26 10:50 UTC 复核 [DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)：`deepseek-flash`（页面列示版本 DeepSeek-V4.1-Flash-1017）峰时缓存未命中输入 $0.30/百万 token，输出 $1.20/百万 token。按本项目保守换算 10 元/美元，单次最大预留为 `((100000+8192)×3 + 4096×12)/1000000 = 0.373728` 元；24 次满额为 **8.969472 元**。因此拟议本地上限 **9.00 元人民币**。本地预留不是提供商账单硬上限，实付未知。

此 plan 是独立的一次性新 scope；用户需明确批准该 plan/hash 的 9 元预算以及将计划列明的公开合成历史、候选代码/契约证据、提示词和按需回查的历史原文发送至 DeepSeek。批准前不读密钥、不创建 approved 收据、不发请求。成功完成并出现自主选择、真实返回、续答接受和有效终点，才可把本诊断标记为 `lookup_branch_exercised`；未触发或任一环节失败均保留事实、不补跑。

## 结论

本步完成了失败归因、版本化修复、无网络回查/停止/回放准备和新预算预检。它**没有完成真实增量补测**。S3 开发验收仍差本计划获授权后的真实结果；若模型不触发回查，开发验收仍按未验证记录。独立审核者仍是外部待办，完整 S3 研究验收与 S4 正式比较均未完成。
