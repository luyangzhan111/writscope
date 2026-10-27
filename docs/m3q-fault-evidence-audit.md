# M3q：离线故障与证据审计

日期：2026-10-24。执行类型：**离线脚本提供商审计**。实际外部模型调用 0 次；没有新的付费授权、真实模型成绩或方法比较结论。

## 审计范围与记录

[版本化审计脚本](../scripts/m3q_fault_evidence_audit.py)从三条历史的公开输入生成脚本响应，运行并独立回放 M3p 同轨入口。正式记录为 [审计清单](../reports/local/m3q-fault-evidence-audit-20261024-final/run.json)、[故障矩阵](../reports/local/m3q-fault-evidence-audit-20261024-final/faults.json)、[输入与报告对齐](../reports/local/m3q-fault-evidence-audit-20261024-final/alignment.json)、[旧运行回放](../reports/local/m3q-fault-evidence-audit-20261024-final/history.json)和[篡改检查](../reports/local/m3q-fault-evidence-audit-20261024-final/tamper.json)。此前的初稿和修订稿仍在本机忽略目录中；以此处的 `final` 清单为本阶段结果。脚本不读取凭据或私有标签，不构造网络 provider。模拟价 `offline-test-price` 和 10 元模拟上限只用于测试预留规则。

九种故障覆盖：本地注入超时、provider 错误、缺失用量、坏 JSON、未来事件回查、未知事件回查、跨版本范围不符、终点输出无效，以及全程预留不足的请求前拒绝。前八种均在首次故障停止，保留已占模拟预留；返回过文本的情形保存原始响应，未返回文本的情形如实记录为空。失败轨迹均无有效终点结果，已发出的模拟尝试账单状态仍为 `unknown`，未按零计。预算不足用 1 元模拟上限对照 1.993216 元的全程最坏预留底线，在 provider 尝试前拒绝。另将请求内容篡改并重写文件哈希，独立回放仍拒绝该记录。

## 与完整上下文基线的共同证据

对 c001、c002、c003，分别构造 `full_direct` 与 `full_checklist` 请求，再核对增量请求的基础公开证据哈希、工件及登记表、工具策略；每个事件只包含决策时点前缀，终点请求包含同一完整基础证据。请求键集合固定，扫描的模型可见公开输入未含私有评分标记。三条脚本轨迹各含五次提取、两次回查续答、一次终点响应；另保留 c002 非空状态轨迹，以验证状态来源实际进入终点请求。

[对齐表](../reports/local/m3q-fault-evidence-audit-20261024-final/alignment.json)按 `case_id`、方法、状态、观察到的影响/授权/决策三轴、证据与工具哈希、尝试数、模拟用量及运行哈希列出字段。`expected`、`correct` 留空；模拟 token 单列，实际账单为 `unknown`，美元费用完整值为空。该表说明输入与报告结构可对照，**不能**说明三种方法的准确率或成本优劣。两条基线在此只构造请求，没有同轮模型响应；额外的状态与回查调用使总预算也尚未匹配。M3p 轨迹尚未保存真实 provider 耗时或 M2 式运行 ID，真实用量、账单和正式评分也缺失。

旧 M3c、M3d、M3e 保存记录均无网络回放通过，工件集哈希分别为 `440b7f3f7f53dd39647a126306f101ee9f9c52532a99296525e47824cdba4ad13f`、`171519845c311a8bb9ea92481c083457fb91ad48e5801b73fd1af0aa760806c7`、`e92805c4f7e4e4f21bd6ef77ac2a6c05fada0a3c85c938be4451ae7254258680`。没有改写旧输出或评分。

## 验收与下一步

使用项目 `.venv` 运行 `python -m scripts.m3q_fault_evidence_audit run --output reports/local/m3q-fault-evidence-audit-20261024-final` 后，再以相同目录执行 `verify`；两次均返回 `passed`、九类故障和零实际模型调用。定向测试见 `tests/test_m3q_fault_evidence_audit.py`，包括重写根清单哈希后的对齐表篡改拒绝。审计依赖本机已有的 M3c–M3e 忽略目录记录；迁移到另一工作区时须连同脱敏保存工件提供，不能把不存在的旧记录算成回放通过。

M3r 下一步形成**可审查的真实运行前预检**：补齐真实 provider 入口与报告缺口，冻结目标、调用顺序、当时官方价格、最坏预留、总额、错误与重试规则，并验证未获新授权时在读取凭据前停止。M3c–M3e 的旧授权不覆盖它。M3q 不触发 M3s 付费运行。
