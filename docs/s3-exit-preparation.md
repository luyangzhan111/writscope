# S3 独立审核材料与真实回查离线预检

日期：2026-10-25。用户授权准备两份材料；本步新增真实模型请求 **0 次**，没有新的付费授权。两套输出均已保存后独立复验，具体契约见 [准备契约 v1](../specs/s3-exit-preparation-v1.md)。

## 一、独立审核材料已准备，尚未审核

入口为 [独立审核包构建器](../scripts/s3_independent_review_pack.py)。打开[审核说明](../reports/local/s3-independent-review-20261025/README.md)，按步骤填写[审核者响应](../reports/local/s3-independent-review-20261025/reviewer_response.json)。冻结的[来源清单](../reports/local/s3-independent-review-20261025/source_manifest.json)与空白模板供回放核对。

材料只含原公开事件、候选、接口契约和共同公开影响证据，不附作者人工状态、参考判断或先前诊断结论。先审核 e001–e004 的四个共同前缀，再打开三份 e005 分支；共七个审核单元，覆盖三条开发历史的十五个前缀位置。每步独立记录背景、约束、授权、例外、生命周期、歧义和原文依据；分支末尾再分别判断 impact、authorization、decision。

审核者身份、既往参与、是否看过作者答案、独立性声明和实际结论均保持空值。响应文件可填写，但来源完整性验证不会把自报完成或身份自动接纳为独立审核；须另有可信的人类来源核对及分歧处理。阶段目录只是工作流，不提供盲审访问控制。若审核者已有答案暴露，须明确记录非盲审，不能事后改称盲审。

本地包为 evaluator_only，不进入模型文件白名单。原作者包和标签保持原版本，没有被独立审核结果替换。

## 二、回查预检草案与六类接线记录已准备

入口为 [回查离线预检构建器](../scripts/s3_lookup_offline_preflight.py)。最终材料见[预检说明](../reports/local/s3-lookup-preflight-20261025-final/README.md)、[未授权计划](../reports/local/s3-lookup-preflight-20261025-final/plan.json)、[首条待请求](../reports/local/s3-lookup-preflight-20261025-final/first_request.json)和[六类传输记录](../reports/local/s3-lookup-preflight-20261025-final/transport_cases.json)。

本步选择现有 c003 作为离线候选，保持交互/终点提示词和完整可见原文，不隐藏基线原文，不强制模型必须回查。候选模型仍为现有 `deepseek-flash` 配置，但当前可用性和价格未联网核对。此选择不承诺回查会发生：旧 M3s 已有未触发记录，未来同样可能得到 not_exercised；不能仅为凑回查次数自动重复付费。

| 离线场景 | 脚本回查请求 / 本地成功回查 | 传输请求数（含终点待请求） | 结果 |
| --- | ---: | ---: | --- |
| 不回查 | 0 / 0 | 6 | 五事件接受，终点仅待响应 |
| 一次回查 | 1 / 1 | 7 | 返回 e001 原文并形成续答，终点仅待响应 |
| 两次回查 | 2 / 2 | 8 | 两次续答与终点待请求，上限内 |
| e004 请求未来 e005 | 1 / 0 | 4 | 解析拒绝，停在已接受三事件，无终点 |
| 请求未知事件 | 1 / 0 | 5 | 解析拒绝，已接受四事件，无终点 |
| 第三次请求回查 | 3 / 2 | 7 | 额度拒绝，不再续答或生成终点 |

这些请求和回查由脚本生成，**实际 provider 尝试为 0**。状态提案全部为 no_change，仅验证传输衔接，不能拿来证明语义提取正确。六类场景最大请求字节数为 21950，低于拟议每次 100000 字节上限；没有将脚本次数换算成真实 token 或费用。

## 三、真实执行尚未放行

计划为 `draft_unapproved` / `paid_run_ready=false`，最多五次初始提取、两次回查续答、一次终点，共八次；单并发、90 秒、无重试、首次失败停止。所有实际提取、续答、终点和失败尝试须计费，失败不释放已用预留。

以下仍为 null：当前价格及核对时间、人民币上限、最坏货币预留、新授权、真实执行入口绑定。本预检没有 paid/real 命令，不能把它交给旧 M3s 入口复用旧授权；下一付费申请前还需绑定新入口、重新核价、计算最坏预留并完成对应故障检查。

真实模型自行选择且完成回查请求、原文返回、续答和终点，才可记 `lookup_branch_exercised`；没有触发则记 `not_exercised`。即使真实模型响应了强制回查提示，也只能记 `prompted_transport_control_only`。脚本、强制控制和自主选择分开，任何未触发或失败都不自动补跑。结果分类来自未来运行器的可信日志，不信任模型自报身份或费用。

## 验证与来源

[本步定向测试](../tests/test_s3_exit_preparation.py) **11 项通过**：公开材料/参考隔离和禁止 provider 构造、审核模板保持空白、可填副本不自动代表审核通过、未来原文篡改即使重写清单哈希仍拒绝、六类传输的前缀/失败停止/字节上限、伪造授权标志拒绝、未触发/失败/脚本/强制控制分类，以及不能吞掉无关解析缺陷。

两个最终目录均 `prepare` 后另行 `verify` 成功：审核包为 `prepared_not_reviewed`，预检为 `offline_prepared_unapproved`，新增模型请求均为 0。审核包 run.json SHA-256 为 `8e299e0223bc1428c824ab69c10ee3ff908ef24ed3103df44934fba6d1ce69a1`；最终回查预检 run.json 为 `aa9ecad8d309cf24f5e38e8f0a686e26edc5421081bdec7eaadcee7af795bfb0`，计划规范哈希为 `73ea32b88b149e35a965457eaa837ad3ed58e9a5a0299498d6142cd5b194decf`。人工审核与本地准备耗时未测量，不记为方法零成本。

本步完成了材料和离线预检，**独立审核尚待另一位实际审核者填写，真实自主回查仍未运行**。后续可并行收取独立审核意见，以及实现接受新计划的版本化真实入口/费用闸门；准备完毕后再形成精确付费申请。完整 S3 仍未验收，同轮三方法结果也未产生。

无网络复验：

```powershell
.\.venv\Scripts\python.exe -m scripts.s3_independent_review_pack verify --output reports/local/s3-independent-review-20261025
.\.venv\Scripts\python.exe -m scripts.s3_lookup_offline_preflight verify --output reports/local/s3-lookup-preflight-20261025-final
```

后续进度（2026-10-25）：[小型真实验收冻结](s3-small-real-acceptance-freeze.md)已完成新版本真实入口绑定、当日核价和 3.00 元预算提案，29 项定向测试与三份脚本回放通过，等待本次用户明确批准。本文所述空值是原准备草案的当时状态，原草案和六类记录保持不变；新增真实请求仍为 0 次。

后续执行更新（2026-10-25）：[S3 小型真实验收记录](s3-small-real-acceptance-review.md)已获独立3.00元批准并完成6真实请求、五事件/终点、保存回放及草稿三轴核对。未触发回查，not_exercised，不补跑；本次授权已用。独立审核与同轮三方法仍未完成，原空白审核包不变。
