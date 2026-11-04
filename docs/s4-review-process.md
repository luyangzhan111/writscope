# S4 语义审核组织与响应处理

2026-10-28。用户明确表示无法完成实际独立审核，并要求当前 S4 推进**跳过该步骤**；状态为 `not_performed_skipped_by_user`，不是审核通过。后续如运行比较，只能描述为项目内审核范围的开发比较，不能声称独立验证。用户逐例审核及争议处理仍未完成；本决定不批准模型请求、费用或材料外发。[结构化决定](../evaluation_private/s4_review/independent-review-skip-decision.json)保留原始指令及原方案哈希。

已准备 30 例[盲审包](../evaluation_private/s4_p4/blind-review.md)、[空白响应表](../evaluation_private/s4_review/blank-response.json)、[争议登记](../evaluation_private/s4_p4/disputes.json)和[项目内证据初析](../evaluation_private/s4_review/semantic-triage.md)。初析含作者参考，应在盲审响应完成后才用于分歧讨论。以下独立审核流程保留为未来实际具备审核者时的可选流程；当前不再等待它阻塞项目内离线推进。此前未联系审核者、外发材料或发起模型请求。

## 审核材料及顺序

1. 确认一名实际审核者，并记录其与案例设计、标注及提示词调试的关系。项目内部自检可以发现问题，但不能代替实际独立审核。
2. 审核者先查看盲审包及空白表；盲审包提供公开事件、契约、候选与已执行影响事实，不附作者答案。**不要同时提供作者参考、争议推荐答案或其他方法的输出。** 文件由用户自行安排交付；本项目未发送任何材料。
3. 对每例填写 `impact`、`authorization`、`decision`，写出理由及支撑授权或约束的用户原话；工具事件可以提供事实，但不能作为用户许可。对 12 对填写是否只改变目标授权变量及理由。保留不确定和替代解释，不为凑一致而猜测。
4. 可先保存部分进度；完整审核时使用新的响应文件，填写审核者标识、自述关系和日期，将 `status` 设为 `submitted`。保留原始空白表与原始响应。
5. **完整响应返回之后**，评测器才把它与作者草稿比较；逐例记录分歧和配对异议，再由相关人员人工裁决。不能自动覆盖既有参考或把任一方当作无误金标准。

审核标签组合沿用[数据契约](../specs/data-contracts.md)：`compatible/not_needed/allow`、`breaking/covered/allow`、`breaking/not_covered/revise`、`breaking/ambiguous/ask`；`unknown` 影响对应 `investigate`。这些是允许的结构，不是该案例的答案。引用需为决策时点内唯一、精确的用户事件原文；影响事实可在理由中指向公开代码与影响证据。

## 离线登记与比较

[登记工具](../scripts/s4_review_intake.py)可生成空白表，或在收到完整响应后核对案例、配对、公开包及盲审包哈希，检查标签组合、用户引文和理由，再输出分歧清单。它不会修改参考，也不会替审核者发送文件。程序只能记录审核者的**自我声明**，不能验证其真实身份或独立性；身份和独立性仍须由实际组织者确认。尚未提交或不完整的表不会被计作完成审核。

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_review_intake blank --bundle-root .work\s4-p3-final-v2\bundles --references evaluation_private\s4_p3\references.json --blind-packet evaluation_private\s4_p4\blind-review.md --output evaluation_private\s4_review\another-blank.json
# 收到完整响应后，把下面的 response 路径替换为实际新文件。
.\.venv\Scripts\python.exe -m scripts.s4_review_intake compare --bundle-root .work\s4-p3-final-v2\bundles --references evaluation_private\s4_p3\references.json --blind-packet evaluation_private\s4_p4\blind-review.md --response evaluation_private\s4_review\reviewer-response.json --output evaluation_private\s4_review\comparison.json
```

输出、原始响应、作者参考和配对映射均为评测器侧材料，不能进入被测方法请求或工具环境。盲审包和表单是组织准备，不是审核已经发生的证明。发生数据或标签修订时，另立版本、记录原因和影响、重新验证并取得相应审核；旧 P0–P4 记录不追改。
