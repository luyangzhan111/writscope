# S3 开发验收 v8 终点引用离线预检

日期：2026-10-26。针对 v7 真实回查轨迹的终点引用拒绝，已另立终点提示 v2 和 c004 v8 隔离入口。**真实 provider 请求 0 次；本计划 `draft_unapproved`，授权为空。**

## 修订及范围

- [`incremental_final_review_v2.md`](../prompts/incremental_final_review_v2.md) 明确区分来源字段：逐影响授权来源只引用户事件；工具报告等非权威事实放顶层 `source_refs`；派生状态项沿用其可信用户来源。这样不放宽校验器，也不把工具事实当成许可。
- 新入口为 [`s3_acceptance_v8.py`](../scripts/s3_acceptance_v8.py)，配套独立报告器、模拟器和契约；提取仍用 interaction v8 / `interaction_quote_v4`，原 v7 真实提示、输出和报告未改。
- 单一 c004 incremental 轨迹、R=1；最多 8 次（5 提取、2 次模型选择回查续答、1 终点），单并发、90 秒/次、无重试、首错停。3.00 元上限、2.989824 元最坏预留；外部账单可能不同。
- 新计划：[离线预检](../reports/local/s3-development-v8-preflight-final-20261026/)，文件 SHA-256 `806ce80878cb94f314c76863c22aa567b656ac30412e9547d5a7acba7c6228c7`；内部规范化计划哈希 `256e9de60ab96243f7dad30cbbaf68f0841d49b1189da011aeabbb76878196aa`。官方价格观察绑定为 2026-10-26 15:35:16 UTC；实跑时若延迟或价格改变须更新计划和哈希，启动前复核。

## 离线验证

10 项定向测试通过，计划和源码哈希复核成功。六类模拟均以该新计划保存并回放：成功模拟回查控制 7 次、no-lookup 6 次、旧 v4 拒绝 1 次、v5 错续答 6 次、首次 provider 故障 1 次、非法 JSON 1 次。每条均为 `actual_model_calls=0`；模拟回查只标 `offline_transport_only`，所有报告 `s3_development_exit=not_satisfied`。pytest 有本地 `.pytest_cache` 权限警告，不影响通过。

## 验收状态

v7 的真实运行实际取得自主回查、原文返回及接受续答；但终点因引用位置不符合现有权限来源契约而被拒绝，导致有效终点仍为 0/1。该旧轨迹授权已消费，不能重跑。v8 只修订提示字段说明，仍需新的独立 3.00 元预算和计划列明数据外发授权，不能复用 v7 授权。独立审核者仍是完整研究验收的外部依赖；即使 v8 产生有效终点，也只关闭作者审核的开发诊断缺口，不能替代独立审核或 S4 方法比较。
