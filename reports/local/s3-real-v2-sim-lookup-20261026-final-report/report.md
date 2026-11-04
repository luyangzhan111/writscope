# S3 v2 三方法共同报告

类型：offline_simulation；计划九任务、三历史、一个场景家族、R=1。
参考：项目作者草稿，实际独立审核待补。完整 S3 尚未签收；未知账单/人工与共享准备成本保持 unknown。

| 案例 | 方法 | 状态 | 观察三轴 | 调用数 | 本地预留/元 | 回查 |
| --- | --- | --- | --- | ---: | ---: | --- |
| c001 | full_direct | completed | breaking/ambiguous/ask | 1 | 0.13119 | not_applicable |
| c001 | full_checklist | completed | breaking/ambiguous/ask | 1 | 0.131898 | not_applicable |
| c001 | incremental | completed | breaking/ambiguous/ask | 8 | 1.239255 | offline_transport_only |
| c002 | full_checklist | completed | breaking/ambiguous/ask | 1 | 0.13212 | not_applicable |
| c002 | incremental | completed | breaking/ambiguous/ask | 8 | 1.268655 | offline_transport_only |
| c002 | full_direct | completed | breaking/ambiguous/ask | 1 | 0.131412 | not_applicable |
| c003 | incremental | completed | breaking/ambiguous/ask | 8 | 1.239624 | offline_transport_only |
| c003 | full_direct | completed | breaking/ambiguous/ask | 1 | 0.131313 | not_applicable |
| c003 | full_checklist | completed | breaking/ambiguous/ask | 1 | 0.132021 | not_applicable |

未计算模型准确率：未执行真实同轮运行，或本报告仅是脚本接线演练。

三方法使用同一基础模型和共同原始证据，调用结构不同，属于等配置开发诊断；未做预算匹配。
模型未请求回查时保留 not_exercised，脚本回查只能是 offline_transport_only；历史 M2b/M3 结果不补入本轮。
完整费用、三轴分子分母、缺失和错误见 summary.json。
