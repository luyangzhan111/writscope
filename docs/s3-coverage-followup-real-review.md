# S3 v3 开发验收补充轮真实记录

日期：2026-10-26。用户批准将冻结计划公开材料发送至 DeepSeek，并批准本轮 12 元上限。单轮计划 SHA-256：`1fa3e1307ce099ca95b84284f2d90eebb9a3a0ed71705e4bd326a2b7c7050615`；价格 10:20:15 UTC 复核，峰时未缓存输入 0.30 美元/百万 token、输出 1.20 美元/百万 token。运行器记录实际 13 次请求，九项任务均尝试，无重试。

## 任务结果

| 案例 | 方法 | 状态 | 观察三轴 | 请求数 | 预留/元 | 回查 |
| --- | --- | --- | --- | ---: | ---: | --- |
| c001 | full_direct | completed | breaking / not_covered / revise | 1 | 0.131190 | not_applicable |
| c001 | full_checklist | completed | breaking / not_covered / revise | 1 | 0.131898 | not_applicable |
| c001 | incremental | stopped_on_failure | 无终点 | 2 | 0.318423 | not_exercised |
| c002 | full_checklist | completed | breaking / covered / allow | 1 | 0.132120 | not_applicable |
| c002 | incremental | stopped_on_failure | 无终点 | 2 | 0.318390 | not_exercised |
| c002 | full_direct | completed | breaking / covered / allow | 1 | 0.131412 | not_applicable |
| c003 | incremental | stopped_on_failure | 无终点 | 3 | 0.481887 | not_exercised |
| c003 | full_direct | completed | breaking / not_covered / revise | 1 | 0.131313 | not_applicable |
| c003 | full_checklist | completed | breaking / not_covered / revise | 1 | 0.132021 | not_applicable |

六个基线输出各 3/3 与作者草稿参考一致。三个增量任务均无有效终点；不能将无终点当成语义三轴误判，也不能将失败解释成正确。

## 失败、缺失与回查

- c001 增量：e001 被接受；e002 的第二次提取输出被解析器拒绝，转换日志错误为 `state source quote missing or non-unique`。根任务和调用摘要 `error` 为 null；细节保存在 `transition_steps`。
- c002 增量：e001 被接受；e002 第二次请求发生 `DeepSeek transport error: ConnectError`。没有重试；provider 调用错误原文保存在 calls/jobs，转换步骤诊断层额外出现 `mock script exhausted`，此字段来自内部重建脚本，不覆盖 provider 错误。
- c003 增量：e001/e002 被接受；e003 第三次提取输出因 `state source quote missing or non-unique` 被拒绝。根任务和调用摘要的 `error` 同样为 null，详细错误保存在转换步骤。

三条增量轨迹各自均在首次故障处停止，之后的事件未请求。回查请求 0、回查续答 0、终点复核 0。模型在当前可见材料中未主动选择回查；由于每个增量轨迹均在提取阶段结束，**本轮没有取得自主回查证据**，不能用脚本回查或提示诱导替代，也不自动补跑。

## 费用与调用

| 账目 | 记录 |
| --- | ---: |
| 请求 / 计划上限 | 13 / 30 |
| 本地预留 | 1.908654 元 |
| 峰时保守估算 | 已知部分 0.311295 元；另 1 次用量未知，完整值 unknown |
| 提供商实付 | unknown；13 次响应均无账单金额字段 |
| 输入 token | 已知 70,625；另 1 次未知 |
| 输出 token | 已知 8,285；另 1 次未知 |
| reasoning token | 13 次均 unknown |
| 阶段请求数 | review 6；extract 7；lookup continuation 0；final review 0 |
| provider 累计耗时 | 88,056.194 ms |

总预留低于 12 元本地上限；预留并非提供商账单硬上限。未计入的人工与共享准备成本仍为 unknown。

## 回放及报告限制

- [原运行目录](../reports/local/s3-coverage-followup-live-20261026/)保留 13 个调用、九个任务、授权与其原始失败。运行清单绑定计划哈希，并含原 `authorization.json`、`calls.json`、`jobs.json`、`plan.json` 哈希。
- 原冻结运行器的 verify 初次遇到 verifier 代码缺陷：它对已完成任务的 `failure=null` 直接执行 `.get()`，抛出 AttributeError。没有修改原运行器或任何原工件；通过[只读兼容回放入口](../scripts/s3_coverage_replay_compat_v1.py)仅调整 jobs 的内存读取视图后，原 verifier 与报告器重建成功。原文件里的 null 保持原样，兼容证据记录新旧摘要。
- [九行真实报告](../reports/local/s3-coverage-followup-report-20261026/report.md)保留全部任务、分母、失败及成本；[回放证据](../reports/local/s3-coverage-followup-replay-evidence-20261026/verification.json)绑定真实运行清单、冻结运行器源码快照和兼容适配器。报告在原轨迹回放后读取作者草稿参考。
- 所有有效基线命中只适用于三条已调试合成历史与 R=1。增量方法在三条轨迹均无有效结果，且自主回查未触发；调用成本结构不同，没有独立审核或未见家族，不能宣称方法排名、普遍收益或独立金标准。

## 开发验收结论

本轮满足九项任务均实际尝试及报告保留全部失败的覆盖要求。用户先前表示当前无法找到独立审核者，因此本项目可继续按作者审核的开发验收口径推进；独立研究验证仍标未完成。由于真实自主回查没有发生，用户要求的开发验收终点**尚未全部达到**。下一步若仍要求回查证据，必须另拟并授权新的诊断范围；本轮授权已用，不能续跑。S3 完整研究验收和 S4 正式比较均未完成。
