# V2 三方法离线同轮报告

本报告全部为脚本演练，无真实模型调用、评分或费用。
参考标签未读取；独立审核与真实回查证据状态不变。

| 案例 | 方法 | 状态 | 脚本尝试 | 事件接受 | 终点有效 | 回查 |
| --- | --- | --- | ---: | ---: | --- | --- |
| c001 | full_direct | completed | 1 | — | True | not_applicable |
| c001 | full_checklist | completed | 1 | — | True | not_applicable |
| c001 | incremental | completed | 6 | 5 | True | offline_transport_only |
| c002 | full_checklist | completed | 1 | — | True | not_applicable |
| c002 | incremental | completed | 6 | 5 | True | offline_transport_only |
| c002 | full_direct | completed | 1 | — | True | not_applicable |
| c003 | incremental | completed | 6 | 5 | True | offline_transport_only |
| c003 | full_direct | completed | 1 | — | True | not_applicable |
| c003 | full_checklist | completed | 1 | — | True | not_applicable |

synthetic_usage 与模拟预留仅用于核对接线账表，不能作为模型用量、准确率或成本优势。
详细状态/回查/原子转换及脚本响应保留于各 job 子目录；未知实际价格和新预算均为空。
