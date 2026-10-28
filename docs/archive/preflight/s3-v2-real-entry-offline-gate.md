# S3 v2真实入口：离线故障与授权闸门验收

日期：2026-10-26。用户要求准备v2真实入口及离线故障/授权闸门。**本步已实现并完成离线验证，新增真实请求0次，没有读取实际模型密钥，也没有创建新的approved凭据或申请付费执行。** 原v1入口、14请求真实记录、失败和评分保持不变。

## 已实现入口

[s3_common_real_v2](../../../scripts/s3_common_real_v2.py)另立版本，使用已验收的v2脚本核心，在每次真实响应之后重建轨迹，决定是否继续提取、回查续答或终点。v2请求反序列化、提示词、配置哈希与新契约接通；两基线提示词/完整证据和终点契约不变。没有临时替换或修改v1模块。

入口提供prepare、preflight-verify、real、verify；[单独报告](../../../scripts/s3_common_real_v2_report.py)先回放再评分。[模拟入口](../../../scripts/s3_common_real_v2_simulation.py)只生成适配器形状的脚本响应，模拟元数据为synthetic_adapter，不冒充真实provider调用。规则详见[闸门契约](../../../specs/s3-common-real-v2-gate.md)。

新授权范围 `S3_v2_three_method_three_case_development_round_v1` 绑定三历史×三方法、单轮、最多30请求、无重试、单并发、90秒/次、全组首次运行故障停止。授权/计划/代码/契约/价格及新鲜核价不匹配时，先拒绝再读取凭据；已有输出或已占用启动记录也先于凭据拒绝。

真实调用前逐次预留；之后所有返回、失败和未运行项保存。代码额外区分合法响应之后的下一请求预留失败，避免把合法的上一次provider响应重标成失败。无效用量保留原值，汇总记unknown。

## 未授权预检草案

[最终计划](../../../reports/local/s3-real-v2-preflight-20261026-final/plan.json)、[九份首请求](../../../reports/local/s3-real-v2-preflight-20261026-final/first_requests.json)和[预检清单](../../../reports/local/s3-real-v2-preflight-20261026-final/run.json)已生成后独立复验，状态仍是draft_unapproved/new_authorization=null：

- 计划规范SHA-256：`2188a28843df9f89c1b9ea23f8d973b5ee50b2e6ba1c9e93cf715213fcf2e349`。
- 运行器SHA-256：`0dd03ebd1f4d1218c2d836c3183142678202f0cd49846424914e0f2d4451f538`。
- 九任务采用原固定轮转，每基线最多1次、每增量最多8次；原始公开证据及权限一致，逐轨空状态，没有作者标签、人工状态或其他方法输出进入请求。

2026-10-26 06:15:14 UTC 重新核对[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)：Flash峰时缓存未命中输入$0.3/百万、输出$1.2/百万，保守换算系数10元/美元（不是实时汇率），对应本地预留输入3元/百万、输出12元/百万。

30次×`((100000+8192)×3 + 4096×12)/1000000` = **11.211840元**满额最坏预留；代码草案总额为**12.00元**，增量每任务3元、基线每任务0.40元。**这些是新v2的未批准候选值，不是沿用旧12元授权，也不是本步费用实测。** 提供商实付未知，本地预留不保证提供商硬封顶。实际执行前还须核对当前价格与批准范围。

## 六份保存后的离线演练

每份都执行simulate → verify → 独立报告；报告步骤再次先回放，未读取参考标签。以下次数全为脚本尝试，实际模型请求均为0：

| 演练 | 脚本尝试 | 结果 | 保存记录 |
| --- | ---: | --- | --- |
| 回查完整链 | 30 | 九任务完成，v2提取/续答及终点接通 | [运行](../../../reports/local/s3-real-v2-sim-lookup-20261026-final/run.json) / [报告](../../../reports/local/s3-real-v2-sim-lookup-20261026-final-report/report.md) |
| 无回查完整链 | 24 | 九任务完成，仍只记离线传输 | [运行](../../../reports/local/s3-real-v2-sim-no-lookup-20261026-final/run.json) |
| 非法grant | 18 | c002形状失败，全组停止，后四任务未运行 | [运行](../../../reports/local/s3-real-v2-sim-bad-grant-20261026-final/run.json) |
| 首次provider错误 | 1 | 首任务停止，其余八项未运行 | [运行](../../../reports/local/s3-real-v2-sim-first-error-20261026-final/run.json) |
| 超时异常 | 1 | 保留预留，首任务停止 | [运行](../../../reports/local/s3-real-v2-sim-timeout-20261026-final/run.json) |
| 非法用量 | 1 | 原负数保留，汇总unknown，全组停止 | [运行](../../../reports/local/s3-real-v2-sim-invalid-usage-20261026-final/run.json) |

脚本响应由固定公开控制构造，不是模型输出、作者正确状态或原真实响应修复。当前provider的JSON模式不保证遵守schema；模型合法率、真实自主回查和方法质量均未在本步测量。

## 验证覆盖

[新增真实边界测试](../../../tests/test_s3_common_real_v2.py)30项与原v2离线/形状/交互回归39项，共 **69项通过**。验证了：

- 成功30次与无回查24次、c002合法脚本状态、四阶段费用字段及全组首次故障停止。
- provider错误、超时异常、未预期异常、截断、缺用量、非法/越界用量、非法grant、未来回查、跨版本范围拒绝及终点错误。
- 缺/旧/非用户来源/错误金额/次数/计划/代码/契约/价格哈希的授权拒绝，过期或明显未来的核价拒绝。
- 已存在输出/启动记录先于密钥读取拒绝，排他启动不能用于其他目录，预留不足或字节超界在发送前拦截。
- 九份首请求或保存请求即使同步重写文件hash仍被拒绝；模拟不能改标真实后读标签评分。
- 合法授权后的凭据读取顺序用假密钥读取器和立即中断的构造替身检查，没有读取实际环境密钥、没有创建真实运行记录或发请求。测试中的approved字典不是人类批准。

真实模式另保存authorization.json与哈希，回放按启动时间校验核价；模拟不得携带授权文件或引用。程序检查scope/哈希/字段不能独自证明某份JSON来自人类，可信操作者仍须先核对实际用户消息后才生成真实批准凭据。

## 下一步

当前完成的是**真实入口实现及离线闸门验收**。本步不执行real，不复用旧授权，也不声称完整S3已结束。下一步可审查本草案后冻结新运行预算并取得明确批准；实际运行前重新核价，再单轮执行、失败停止、回放和生成真实共同报告。

只读复验：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_common_real_v2 preflight-verify --preflight reports/local/s3-real-v2-preflight-20261026-final
.\.venv\Scripts\python.exe -m scripts.s3_common_real_v2 verify --preflight reports/local/s3-real-v2-preflight-20261026-final --output reports/local/s3-real-v2-sim-lookup-20261026-final
```
