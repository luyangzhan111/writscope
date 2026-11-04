# S4 P2：三方法公共 A 轨离线脚本演练

2026-10-28。P2 完成四例 × 三方法的离线脚本接线、状态范围适配、记录及重放。12 条计划轨迹均完成，33 次模拟 provider 尝试，1 次历史回查；真实模型调用 0 次。**脚本响应是人工编排的测试材料，不是模型输出或方法成绩；不报告模拟准确率。** 四例作者参考未经过独立审核，S4 正式比较未运行。后续 30 例代码材料核对见 [P3 记录](s4-p3-review.md)。

## 运行与隔离

[P2 运行器](../scripts/s4_p2.py)从指定公开包目录、案例清单、方法、模拟脚本和输出目录读取参数。`full_direct` 和 `full_checklist` 都拿完整历史与同一公开影响证据；`incremental` 每步从空状态开始，只接收当前时点以前的历史和此前维护状态，最终复核同一原始证据。每例各方法的基础证据哈希一致。三种方法都可使用同一套有界只读历史回查：每条轨迹最多三次、每次至多两条历史、不得越过当前时点；每阶段最多一次回查续答。任意 shell/Python 执行仍关闭。

运行器仅接受 `ScriptedMock`，不读取凭据或接入网络 provider。方法请求和可见材料来自单个公开包；模拟响应脚本、作者参考、兼容补丁、私有验证记录及其他轨迹不进入请求或方法工具环境。脚本内包含人工编排的响应，因此也必须留在方法可见范围外。输入包的文件集合、路径、哈希和公开证据绑定在运行前核验；本地路径白名单与目录隔离不是操作系统沙箱。

## 新版范围判断

[S4 作用范围适配器](../scripts/s4_p2_scope.py)复用既有原子批次和范围内核，对每个授权分别核查可信用户引文、主体、操作、约束引用、局部例外、生命周期和候选条件。它把提取出的精确值范围绑定到已成功提交的授权 ID 和当前事件引文，再与实际影响的旧值/新值逐项匹配：

- 有效授权、条件及精确值均匹配时，才可判覆盖；旧值、新值或主体不匹配时不覆盖。
- 具体值范围缺失或不可验证时保留 `ambiguous`；撤销或替代后的旧授权不再生效，宽约束仍保留。
- 同一影响存在多条授权时逐条检查，不能因第一条不匹配而漏掉后面的有效授权；所有影响均需覆盖才可汇总为覆盖。

值字符串出现在引文中只是来源和结构检查，不能证明模型对自然语言的语义提取正确；未知条件、无可表达的值变化及存在歧义的情况不能自动放行。历史状态内核和旧记录均未修改。新增适配器、状态及来源绑定只覆盖本次测试的结构能力，不等于已经穷举全部生命周期或复杂语义。

## 失败、账本、评测与回放

单轨迹的模拟 provider 错误、空/无效输出、非法状态批次或回查拒绝会保留请求、原始响应、错误和状态记录，停止该轨迹，不自动生成 `revise`。输入污染或哈希问题停止整个运行；模拟预算耗尽时保留已尝试记录，未开始任务标为 `not_run`。每次尝试前后保存检查点；它可用于审计中断，但目前不提供自动续跑。没有自动重试。

账本对每次模拟尝试预留 1 个**虚拟尝试单位**，记录阶段、尝试、回查和本地耗时；最终预留 33/60 单位。这不是人民币预算或 provider 硬账单上限。真实 token、实际账单和模拟用量缺失均记 `unknown`／`null`，不是零；没有模型请求的事实单独记录为 0 次。耗时仅用于本地开发诊断，不用于方法成本比较。

[独立评测入口](../scripts/s4_p2_report.py)在全部轨迹完成后才读取作者参考，验证引用及三轴比较路径，并刻意丢弃脚本比较结果；报告保留 12 条计划任务、完成/失败和无有效输出等状态，`accuracy=null`。三轴比较原语可区分无结果和有效结果；正式真实运行的验证、汇总指标和阈值尚待 S4 实验冻结，不能把当前模拟报告当作正式评分。

最终 [脚本轨迹](../evaluation_private/s4_p2/scripted-run.json)、[评测报告](../evaluation_private/s4_p2/evaluator-report.json)和[哈希清单](../evaluation_private/s4_p2/manifest.json)均为评测器侧材料，不可提供给方法。重放重新执行相同脚本、重新建立状态与判断，并核对轨迹、输入、脚本、运行源码及锁文件绑定；本地耗时允许变化。保存记录的重放通过，S3 v4–v8 旧预检复验通过。

`tests/test_s4_p2.py` 的 13 项定向测试通过，覆盖证据一致、独立回查、截止点、空输出、模拟 provider 错误、预算停止、检查点、私有文件拒绝、状态来源、值范围、撤销、替代、条件、同主体多授权及回放篡改。此前 P0/P1 的 14 项相关测试也通过。自动测试不是独立语义审核或方法效果证据。

## 重建命令

先按 [P1 验证](s4-p1-review.md)重建 P1；输出目录均须不存在。下面假设 P1 的输出是 `.work/s4-p1-demo`：

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_p2 run --bundle-root .work\s4-p1-demo\bundles --scripts fixtures\s4_p2\mock_scripts_v1.json --output .work\s4-p2-demo --case s4_027 --case s4_006 --case s4_010 --case s4_004 --method full_direct --method full_checklist --method incremental
.\.venv\Scripts\python.exe -m scripts.s4_p2 verify --bundle-root .work\s4-p1-demo\bundles --scripts fixtures\s4_p2\mock_scripts_v1.json --run .work\s4-p2-demo\run.json
.\.venv\Scripts\python.exe -m scripts.s4_p2_report --bundle-root .work\s4-p1-demo\bundles --scripts fixtures\s4_p2\mock_scripts_v1.json --run .work\s4-p2-demo\run.json --references evaluation_private\s4_p1\references.json --output .work\s4-p2-demo-report.json
```

P3 已将代码材料扩展到 30 例并保留语义审核争议，见 [P3 记录](s4-p3-review.md)。正式模型、R、价格、比较阈值、外发范围及预算仍须另行冻结并获得授权；B 轨的独立执行权限边界也仍未建立。
