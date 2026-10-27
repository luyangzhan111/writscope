# S3 开发验收 v7 离线入口复核

日期：2026-10-26。针对 c004 的单轨开发诊断准备；**真实 provider 请求 0 次，计划未获授权**。本记录只证明新版本入口与离线故障路径可回放，不证明模型遵循新提示，也不关闭 S3。

## 新版本范围

- 独立提示：[`incremental_interaction_v8.md`](../prompts/incremental_interaction_v8.md)。澄清本地映射/实现偏好属于 task context；服务端 grant 需明确用户授权；保留宽约束 active 与将约束列入 grant 的 `constraint_refs` 是两件事；依赖隐藏历史的条件在回查并核对前保持 unknown。
- 新入口、报告器、模拟器及契约：[`s3_acceptance_v7.py`](../scripts/s3_acceptance_v7.py)、[`s3_acceptance_report_v7.py`](../scripts/s3_acceptance_report_v7.py)、[`s3_acceptance_simulation_v7.py`](../scripts/s3_acceptance_simulation_v7.py)、[`s3-acceptance-v7.md`](../specs/s3-acceptance-v7.md)。复用严格 `interaction_quote_v4` 解析、精确主体范围校验、回查上限、预算账本和保存后回放；不修改 v6 源码、计划或任何已保存真实结果。
- 唯一任务为 c004 incremental R=1；最多8次（5次提取、2次模型选择的回查续答、1次终点），单并发、每次90秒、无重试、任一轨迹错误停止。本计划固定本地候选上限3.00元、满额保守预留2.989824元；这是本次离线预检的保守预留值，不是提供商硬账单上限。
- 计划目录：[v7离线预检](../reports/local/s3-development-v7-preflight-final3-20261026/)，SHA-256 `bb104db31a623025accfcabb9739b80e09752ff74f1eb4a053facd6ea54dc57f`。状态 `draft_unapproved`、`actual_model_calls=0`、新授权为空。官方峰时价于2026-10-26 15:33:19 UTC重新核对并绑定本计划；真实执行前仍应确认价格未变化。

## 离线验证

10项定向测试通过；`preflight-verify` 再次确认冻结计划、首请求与源码哈希一致。pytest 仅出现无法写入本地 `.pytest_cache` 的权限警告，未影响结果。

六种模拟记录均保存到对应 `reports/local/s3-development-v7-*-final3-20261026/` 目录，并由同一版本入口回放和重建报告：

| 场景 | 模拟请求数 | 结果 |
| --- | ---: | --- |
| `v8_lookup` | 7 | 脚本回查传输控制完成，分类 `offline_transport_only` |
| `no_lookup` | 6 | 完成；`not_exercised` |
| 原 v4 拒绝响应 | 1 | 首错停机，范围拒绝保留 |
| v5 已保存的错误续答 | 6 | 严格 schema 拒绝，原响应不变 |
| 首次 provider 故障 | 1 | 首错停止，无重试 |
| 非法 JSON | 1 | 首错停止，无后续事件 |

所有记录 `actual_model_calls=0`、性能指标为 null、开发验收为 `not_satisfied`。脚本构造的回查不算模型自主回查。

## 当前判断与下一步

新入口和失败回放可供审查；尚未对 v8 做真实模型观察。S3 开发验收仍缺真实模型自主请求历史、收到原文、续答被接受并完成有效终点的完整链。v5仅到原文返回，续答被拒；v3覆盖轮、v6均未触发回查。

计划目前不是付费执行凭据。下一步若继续，只能先基于这份确切入口重新核价、更新预检及其哈希，再单独取得预算和计划列明数据外发授权；首次失败或未触发时保留记录，不补跑。即使新真实轨迹完成，独立审核仍是完整研究验收的外部依赖：审核包目前空白，作者自审不能代替实际独立审核。S4正式比较未运行，也不属于本次开发诊断。
