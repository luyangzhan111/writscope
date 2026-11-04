# S4 P4：全批离线接线与故障回放

2026-10-28。P4 已完成 30 例 × 三方法的离线模拟接线、独立报告、五类故障重放和旧记录兼容检查。**这些是人工编排的脚本，不是模型表现或研究比较。** P3 的作者参考与配对语义仍待审核，数据和真实实验未冻结；新增真实模型请求 0 次，没有批准费用或材料外发。

## 全批接线

以 P3 公开包和已验证影响事实生成统一的模拟运输脚本；生成器不读取作者参考、配对映射或兼容修复。脚本有意采用简单固定判断，仅用于验证管线，不代表正确答案。`full_direct`、`full_checklist`、`incremental` 各有 30 条计划轨迹，完整清单强基线保留。每例各方法的基础证据哈希与只读回查权限一致；增量提取只看到当前时点之前事件并从空状态开始。每个方法只得到自己的单个公开包，任意 shell/Python 执行关闭；文件白名单不构成操作系统安全沙箱。

正常离线演练结果：**90/90 轨迹完成、219 次模拟尝试、3 次脚本回查、真实模型请求 0 次**。每次尝试、状态转换、回查、最终输出和本地耗时均保存在可重放轨迹；逐轨检查点供中断审计，暂不提供自动续跑。模拟用量缺失保持 unknown：219 次输入 token、输出 token 和金额均无模拟值；真实 provider token 与账单也记 unknown，不能当作零。虚拟预算上限是 260 次尝试单位，不是人民币授权或 provider 硬账单限额。

## 失败与账本

下表均为单独构造的离线控制，保留 90 个计划任务分母；每种控制保存独立脚本和完整轨迹，并从保存记录重新执行回放。

| 离线控制 | 完成 | 失败 | 未运行 | 模拟尝试 | 保存后回放 |
| --- | ---: | ---: | ---: | ---: | --- |
| 模拟 provider 错误 | 89 | 1 | 0 | 218 | 通过 |
| 无效 `null` 输出 | 89 | 1 | 0 | 219 | 通过 |
| 请求未来历史 | 89 | 1 | 0 | 214 | 通过 |
| 工具事件冒充权限 | 89 | 1 | 0 | 215 | 通过 |
| 预算耗尽 | 2 | 1 | 87 | 7 | 通过 |

无效输出保留原始 `null`，未来回查返回拒绝和空事件，工具来源的状态变更被拒绝；失败不自动生成 `revise`。provider 错误控制还模拟一笔已知的 10 个输入 token 和 0.01 美元，其余用量未知，账本只记录已知部分并保持总额 unknown；这只是合成记账数据，不是实际账单。

[独立报告](../evaluation_private/s4_p4/evaluator-report.json)在轨迹完成并重放后才读取评测器侧参考，核验三轴比较通路，但丢弃模拟比较结果，`accuracy=null`。预算控制的[独立报告](../evaluation_private/s4_p4/budget-evaluator-report.json)保留全部 90 行，包含 87 个 `not_run`，不把失败或缺失当零成本成功。报告不做真实方法排名或质量/成本结论。

## P3 语义争议和待冻结项

已将版本/时序、操作措辞、加急措辞、两例歧义及全部参考的独立审核缺口整理为六项[待决登记](../evaluation_private/s4_p4/disputes.json)：每项保留可能解释、受影响案例与后续处理，不静默修改 P3 原事件或标签。为用户和实际独立审核者准备了包含 30 例公开事件、契约、补丁、影响事实及空白问题的[盲审包](../evaluation_private/s4_p4/blind-review.md)，不附作者参考答案。目前没有用户逐例确认或实际独立审核响应；该包和登记均为评测器侧材料，不得进入方法环境。

[实验计划草案](../evaluation_private/s4_p4/experiment-draft.json)状态为 `draft_unapproved`，源码、数据和待决登记带哈希，但模型、版本、重复次数、阈值、预算、价格、最大调用数、外发对象和材料范围尚未确定。它不是冻结计划或付款/外发批准。正式数据冻结和真实比较前，须解决或明确记录争议，完成适用的审核，并根据具体新计划另行取得预算及外发授权；B 轨还须建立独立执行权限边界。

## 证据与复现

正常与故障的压缩轨迹、脚本、运行绑定和摘要保存在 [P4 评测器侧记录](../evaluation_private/s4_p4/runs/)；[五类故障汇总](../evaluation_private/s4_p4/fault-scenarios.json)和[证据哈希](../evaluation_private/s4_p4/validation-meta.json)记录来源。保存后的正常和 provider 错误轨迹再次重放通过，五类控制各自回放通过。旧 P2 四例轨迹以及公开版保存的 S3 v8 成功、v7 失败和九任务覆盖均离线重放通过；旧证据字节未变。历史记录里的模型调用次数属于当时已保存轨迹，不是本次新增调用。

P4 的 6 项定向测试通过，覆盖完整分母、缺失输出、已知/未知用量、只用公开输入生成脚本、盲审答案隔离、未批准草案和记录篡改拒绝。自动检查不替代语义审核或真实实验。

已有 P3 公开包时，可使用新的输出路径进行离线重建：

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_p4_scripts --bundle-root .work\s4-p3-final-v2\bundles --output .work\s4-p4-demo-scripts.json
.\.venv\Scripts\python.exe -m scripts.s4_p4 run --bundle-root .work\s4-p3-final-v2\bundles --scripts .work\s4-p4-demo-scripts.json --output .work\s4-p4-demo
.\.venv\Scripts\python.exe -m scripts.s4_p4 verify --bundle-root .work\s4-p3-final-v2\bundles --scripts .work\s4-p4-demo-scripts.json --run .work\s4-p4-demo\run.json.gz
.\.venv\Scripts\python.exe -m scripts.s4_p4_scenarios --bundle-root .work\s4-p3-final-v2\bundles --scripts .work\s4-p4-demo-scripts.json --output .work\s4-p4-demo-faults
.\.venv\Scripts\python.exe -m scripts.s4_p4_report --bundle-root .work\s4-p3-final-v2\bundles --scripts .work\s4-p4-demo-scripts.json --run .work\s4-p4-demo\run.json.gz --references evaluation_private\s4_p3\references.json --p0 evaluation_private\s4_p0\case_matrix_v1.json --output .work\s4-p4-demo-report.json
```

下一步是组织语义审核与确定正式实验设计；在审查和授权完成之前，可继续不读取密钥、不发送材料的离线工作。
