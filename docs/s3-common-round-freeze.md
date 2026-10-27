# S3 三方法共同执行与报告冻结

日期：2026-10-25。用户要求补齐 S3 缺口、执行并汇总共同报告。本步已实现统一入口、完成离线演练和报告生成，**新增真实请求 0 次；九任务真实执行等待独立 12.00 元预算批准**。此前 c003 单次 3 元授权已用，不覆盖本轮三方法运行。

## 独立审核暂缺时如何推进

用户明确表示目前缺少实际审核者。独立审核列为外部依赖，继续执行技术核验和开发阶段同轮报告；参考始终标记 `project_authored_draft_external_review_pending`。不填写或代签[已有空白审核表](../reports/local/s3-independent-review-20261025/reviewer_response.json)，不将自审、脚本或 AI 输出升级为实际独立审核。

后续可请未参与答案编制的同学或同事，按[七个审核单元](../reports/local/s3-independent-review-20261025/README.md)复核并填写来源与分歧。已有答案暴露须注明；单人作者参考仍可用于开发诊断，但完整 S3 的独立审核项暂不签收。当前没有自动降低完整验收门槛。

## 同轮范围与顺序

三条已有开发历史 c001/c002/c003、三种方法各一次，R=1，共九任务。三历史共享一个场景家族；不把九任务称为九个独立样本。使用现有 A 轨公开核验影响包，不改原提示词或输入，不拼接旧 M2b/M3 单例成绩。

| 案例 | 固定方法顺序 | 最大请求数 |
| --- | --- | ---: |
| c001 | full_direct → full_checklist → incremental | 1 + 1 + 8 |
| c002 | full_checklist → incremental → full_direct | 1 + 8 + 1 |
| c003 | incremental → full_direct → full_checklist | 8 + 1 + 1 |

固定轮转使三方法位置均衡，不按输出换序。每次 incremental 从空状态开始，不复用其他案例、其他方法或旧运行的状态。两基线都保留完整五事件、原候选、代码与公开证据；增量逐步获得完整可见前缀，最终拥有同等原始证据和权限，人工答案及其他方法输出不进入请求。

共同配置为 DeepSeek `deepseek-flash`、temperature=0、thinking disabled、JSON 输出，每次至多 4096 输出 token；单并发、每次 90 秒、无重试，请求/响应文本各至多 100000 UTF-8 字节。提供商别名不能钉死权重，实际模型与缓存用量按返回值记录，不能声称提供商缓存已关闭。

每个增量任务最多五次提取、两次回查续答和一次终点，共八次；模型自行决定回查，完整原文及原提示词不变，不保证发生回查。未发生则记 `not_exercised`，不额外重复或强制触发。

**全组首次运行故障即停**：provider、超时、用量缺失/越界、输出契约、回查、状态或预算错误停止全部后续任务，后续行保留 `not_run`；不自动续跑。结构合法的语义错误在独立评分阶段记录，不借标签决定执行顺序或停止。所有已发生调用、失败及预留保留。

本轮属于等配置的开发同轮诊断，调用结构和总费用不同；不是预算匹配、30 实例正式 S4 或未见家族验证。本轮批准也不自动允许后续补跑、其他场景或人工状态入模。

## 执行与共同报告

- [统一入口](../scripts/s3_common_round.py)提供 prepare、preflight-verify、real、verify；计划绑定公开输入、四份提示词、完整状态组件、运行器、共同报告代码与环境锁哈希。
- [报告入口](../scripts/s3_common_report.py)单独运行，先离线回放本轮，再允许评测器读取固定私有参考。执行入口不读取标签或审核表。
- 报告列九个计划任务的状态、三轴、分阶段 token/预留/估算/unknown 账单、回查分类和本轮运行来源。失败/未运行保留原计划分母，另列无有效输出、误放行、误拦截及含缺失的合法变更未放行率。
- 基线复核 review、提取 extract、回查续答 lookup_continuation、终点 final_review 四阶段均拆账；阶段计数及预留之和与总账一致。本地重复重建计时逐次累计，解析/保存等额外开销仍在运行器墙钟中，人工与共享准备成本保持 unknown。
- 离线脚本报告与待运行报告不计算模型准确率；不将脚本 token 或金额称为真实消耗。历史成绩不补入本轮分母。

实际独立审核、真实自主回查和真实同轮结果三个缺口分别保留。共同报告生成器完成不等于真实同轮报告已经完成，也不能仅凭某个方法未触发回查宣称该方法失败。

## 金额及授权

2026-10-25 13:42:43 UTC 核对[官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)：按 Flash 峰时缓存未命中输入 $0.3/百万 token、输出 $1.2/百万 token，以及保守系数 10 元/美元预留。该系数不是实时汇率，即使非峰时仍按上界计算。

单次最大预留为 `((100000+8192)×3 + 4096×12)/1000000 = 0.373728` 元。全组最多 30 次，满额最坏预留 **11.211840 元**；申请独立 **12.00 元人民币本地总上限**。每个增量任务上限 3.00 元，每个基线任务上限 0.40 元，没有从失败任务释放预留或向其他任务额外增加请求数。

本地预留不是提供商硬账单限额，实付可能与估算不同。新授权范围为 `S3_three_method_three_case_development_round_v1`，须匹配计划、入口、三个案例、三方法、单轮、30 次及 12 元；真实启动前在读取密钥之前核验批准和 24 小时内核价记录，以计划哈希排他占用一次性启动记录。不把本轮授权延续到补跑。

## 已执行检查与冻结工件

最终[计划](../reports/local/s3-common-preflight-20261025-final-v2/plan.json)和[预检](../reports/local/s3-common-preflight-20261025-final-v2/run.json)已保存并独立复验：

- 计划规范 SHA-256：`58ce7d029b92125d4c9cfbd11e0e4aff8858e0b1e9d9fa66218f03c6a7693e83`。
- 运行器 SHA-256：`9928d7b2002b24d4c32bf3752621e4bc37a8a2bfdccc2592476eb7525d89816f`。
- 新入口[13 项测试](../tests/test_s3_common_round.py)及既有隔离准备 11 项，共 **24 项通过**；覆盖九任务完整演练、四阶段对账、全组停止、未来回查拒绝、缺用量、超时、provider 错误、截断、用量越界、缺/旧/过期授权、篡改请求回放拒绝、缺失分母和排他启动。
- [完整演练](../reports/local/s3-common-simulation-20261025-final-v2/run.json)：九任务、30 次脚本尝试，保存回放成功；其[共同报告](../reports/local/s3-common-simulation-20261025-final-v2-report/report.md)明确为 offline_simulation，无模型准确率。
- [首次失败演练](../reports/local/s3-common-first-failure-20261025-final-v2/run.json)：第一任务一次脚本尝试失败，其余八项保持未运行，保存回放成功。
- [真实共同报告待运行表](../reports/local/s3-common-pending-report-20261025-final-v2/report.md)已生成，九行均为 not_run，无成绩或费用占位值；不能当作实测结果。

初次模拟因响应适配器漏填必填来源字段失败，已修正；完整演练又发现旧增量成本汇总器未单列基线阶段，随后另立四阶段共同汇总并重跑。中间版本与失败输出保留，本次授权仅绑定 `final-v2` 目录和上述哈希。

无网络准备与演练可重复使用新的输出目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_common_round preflight-verify --preflight reports/local/s3-common-preflight-20261025-final-v2
.\.venv\Scripts\python.exe -m scripts.s3_common_round verify --preflight reports/local/s3-common-preflight-20261025-final-v2 --output reports/local/s3-common-simulation-20261025-final-v2
```

真实预算未批准前，不创建 approved 凭据、读取密钥或运行 real。批准后才执行本轮、回放、评分和生成实际共同报告；完整 S3 的独立审核与自主回查仍按实际证据分别判定。
