# S3 增量覆盖补充报告

类型：real_model；计划三条增量任务、三历史、一个场景家族、R=1。
参考：项目作者草稿，实际独立审核待补。完整 S3 尚未签收；未知账单/人工与共享准备成本保持 unknown。

| 案例 | 方法 | 状态 | 观察三轴 | 调用数 | 本地预留/元 | 回查 |
| --- | --- | --- | --- | ---: | ---: | --- |
| c001 | incremental | completed | breaking/not_covered/revise | 6 | 0.944199 | not_exercised |
| c002 | incremental | stopped_on_failure | 未运行/无有效输出 | 5 | 0.800127 | failed_or_incomplete_preserved |
| c003 | incremental | completed | breaking/not_covered/revise | 6 | 0.954990 | not_exercised |

增量指标保留失败和缺失任务的原计划分母；无效输出率与误放行/误拦截同时报告。

| 方法 | 有效 | 三轴全对 | 误放行 | 误拦截 | 无有效输出 |
| --- | --- | --- | --- | --- | --- |
| incremental | 2/3 | 2/3 | 0/2 | 0/1 | 1/3 |

本轮只补充三条增量轨迹，不重跑基线，不与跨轮结果拼接做方法排名。
旧事件原文从初始请求隐藏，仅提供事件ID/序号/角色；模型可按需请求历史回查。未请求时保留 not_exercised。脚本回查只能是 offline_transport_only。
完整费用、三轴分子分母、缺失和错误见 summary.json。
