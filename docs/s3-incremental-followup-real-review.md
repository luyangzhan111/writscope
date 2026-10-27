# S3 增量补充 v3 真实执行记录

日期：2026-10-26。用户针对计划哈希 `ead6921e1716cf79bec8c2e8ee9177cbe47cbbdb465d0cb9c50f25f5e8515233` 批准 9.00 元本地上限，并明确授权向 DeepSeek 发送计划列明的公开合成历史、候选代码/契约、提示词及按需返回的历史原文。执行前重新核对官方价格并核验计划、入口、价格、源码哈希。**一次性授权已使用；没有补跑。**

## 冻结和执行

- [计划及首请求预检](../reports/local/s3-incremental-v3-preflight-frozen-20261026/)仍绑定该 SHA-256；三轨 c001/c002/c003、每轨最多 8 次、全轮最多 24 次，单并发、每次 90 秒、无重试。
- [授权收据](../reports/local/s3-incremental-v3-authorization-20261026.json)仅对应本计划、单轮和该材料范围；一次性启动记录已占用。
- DeepSeek 官方[价格页](https://api-docs.deepseek.com/quick_start/pricing/)在 2026-10-26 10:56 UTC 复核：`deepseek-flash` 由页面列为 DeepSeek-V4.1-Flash-1017；峰时未缓存输入 $0.30/百万 token、输出 $1.20/百万 token。批准本地上限 9.00 元，计划满额最坏预留 8.969472 元；预留并非提供商硬账单上限。

真实运行于 10:56:37–10:58:06 UTC 完成，实际 **17 次请求**，模型回报均为 `deepseek-flash`；无回查续答。15 次 extract、2 次终点复核、0 次 lookup。原始 [运行记录](../reports/local/s3-incremental-v3-live-20261026/)和调用文本未修改。

## 轨迹与终点

| 案例 | 结果 | 接受事件 | 终点三轴／缺失 | 请求 | 预留/元 | 回查 |
| --- | --- | ---: | --- | ---: | ---: | --- |
| c001 | completed | 5/5 | breaking / not_covered / revise | 6 | 0.944199 | not_exercised |
| c002 | stopped_on_failure | 4/5 | 无有效终点 | 5 | 0.800127 | failed_or_incomplete_preserved |
| c003 | completed | 5/5 | breaking / not_covered / revise | 6 | 0.954990 | not_exercised |

c002 的 e005 原子批次包含一项 `grant` 和一项对 `c001` 的 `revoke`；后者目标缺失或不活跃，内核拒绝整个批次，接受事件仍为 4，之后立即停止该轨。拒绝理由保存在 `jobs.json` 的转换记录 `authorization target is missing or inactive`。job/call 根摘要的 `error` 为 null，未用兼容层猜填或改写这项历史。

两条有效终点均与项目作者草稿参考一致。仅按三行预定分母报告：有效终点 2/3，三轴全中 2/3，误放行 0/2，误拦截 0/1，无有效输出 1/3。参考不是独立金标准；c002 缺失不算错误或正确。本轮没有重跑两个基线，也不与旧轮拼接排名。

尽管早期事件正文已隐藏，模型在三案例中均未自行请求历史回查；真实自主回查仍是 `not_exercised`，不以离线脚本回查替代。提示词/可见性改动后的真实入口接线和两例终点获得实际运行证据，但这不构成通用效果提升。

## 费用和用量

| 账目 | 结果 |
| --- | ---: |
| 实际请求／授权上限 | 17 / 24 |
| 本地预留 | 2.699316 元 |
| 峰时保守估算 | 0.427062 元 |
| 提供商实付 | unknown，17 次均无账单字段 |
| 输入／输出 token | 119,218 / 5,784 |
| 缓存命中／未命中输入 | 14,848 / 104,370 |
| reasoning token | unknown，17 次均缺失 |
| provider 累计耗时 | 85,297.807 ms |

估算依据冻结价计算；人工与跨轮准备成本仍 unknown。

## 回放及报告

- 冻结真实运行器在首次 `verify` 时遇到同类 nullable failure 缺陷：它对已完成 job 中序列化的 `failure=null` 调用 `.get()`。未修改冻结源码、运行清单、jobs、calls、响应或调用记录。
- 通过只读兼容器 [`s3_coverage_replay_compat_v3.py`](../scripts/s3_coverage_replay_compat_v3.py)把内存读取视图中的 null 映射为 verifier 默认值，随后轨迹回放与独立评分成功。原始文件保留 null；[回放证据](../reports/local/s3-incremental-v3-replay-evidence-20261026/verification.json)绑定原运行清单、冻结源码、兼容器和报告哈希。
- [真实报告](../reports/local/s3-incremental-v3-live-report-20261026/report.md)先验证轨迹、后读取作者草稿参考，另列三行原计划分母。兼容处理不修改原输出。

## 收尾状态

本轮补齐了三条增量轨迹的真实运行覆盖，并证明新请求可携带按需回查策略；但模型没有使用回查，且 c002 仍在状态批次失败。因此，**S3 开发验收仍未完全通过**，不得将本轮记录为自主回查已验证或全部三轨有效。三案例都属于一个已调试场景家族，作者草稿仍待实际独立审核；完整 S3 研究验收和 S4 正式比较未完成。


后续离线归因补充（2026-10-26）：e005之前的状态中，c001确实存在且是active constraint，authorization集合为空。原错误字符串指的是授权集合查找失败，不能解释为整个状态缺少该ID。旧提示词把revoke写为任意同类目标，而内核仅支持撤销授权；新版本只明确该能力边界，原响应仍整批拒绝。错误摘要接入还存在被同名Cursor定义覆盖的工程缺陷；其修复和新回查诊断见[后续v4复核](s3-development-v4-preflight.md)，原真实记录及评分未改。
