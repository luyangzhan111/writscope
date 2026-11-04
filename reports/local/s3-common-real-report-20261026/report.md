# S3 三方法共同报告

类型：real_model；计划九任务、三历史、一个场景家族、R=1。
参考：项目作者草稿，实际独立审核待补。完整 S3 尚未签收；未知账单/人工与共享准备成本保持 unknown。

| 案例 | 方法 | 状态 | 观察三轴 | 调用数 | 本地预留/元 | 回查 |
| --- | --- | --- | --- | ---: | ---: | --- |
| c001 | full_direct | completed | breaking/not_covered/revise | 1 | 0.13119 | not_applicable |
| c001 | full_checklist | completed | breaking/not_covered/revise | 1 | 0.131898 | not_applicable |
| c001 | incremental | completed | breaking/not_covered/revise | 6 | 0.843594 | not_exercised |
| c002 | full_checklist | completed | breaking/covered/allow | 1 | 0.13212 | not_applicable |
| c002 | incremental | stopped_on_failure | 未运行/无有效输出 | 5 | 0.698751 | failed_or_incomplete_preserved |
| c002 | full_direct | not_run | 未运行/无有效输出 | 0 | 0 | not_run |
| c003 | incremental | not_run | 未运行/无有效输出 | 0 | 0 | not_run |
| c003 | full_direct | not_run | 未运行/无有效输出 | 0 | 0 | not_run |
| c003 | full_checklist | not_run | 未运行/无有效输出 | 0 | 0 | not_run |

指标保留失败和缺失任务的原计划分母；无效输出率与误放行/误拦截同时报告。

| 方法 | 有效 | 三轴全对 | 误放行 | 误拦截 | 无有效输出 |
| --- | --- | --- | --- | --- | --- |
| full_direct | 1/3 | 1/3 | 0/2 | 0/1 | 2/3 |
| full_checklist | 2/3 | 2/3 | 0/2 | 0/1 | 1/3 |
| incremental | 1/3 | 1/3 | 0/2 | 0/1 | 2/3 |

三方法使用同一基础模型和共同原始证据，调用结构不同，属于等配置开发诊断；未做预算匹配。
模型未请求回查时保留 not_exercised，脚本回查只能是 offline_transport_only；历史 M2b/M3 结果不补入本轮。
完整费用、三轴分子分母、缺失和错误见 summary.json。
