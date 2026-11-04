# API约束与任务背景分层、统一覆盖契约 v1

2026-11-03。只离线实现与验证，不读取密钥、不请求模型、不外发项目材料、不产生实验费用或更新GitHub。v3原始记录、已使用批准、冻结源码和评分原字节保留；不以新契约追改旧结果。

## 问题与类型边界

v3清单c2把客户端映射和页面要求重复写成API-shaped constraint，并可能把可以加强为must；三方法的约束change_types也分别列3／4／2项，旧开放qualifiers不能证明等价。新版本不靠清空c2、删响应字段或追补旧列表来制造通过。

任务背景继续保存唯一精确用户引文，不引入可自由改写的任务义务字段。API约束必须有独立、最小的API义务引文；客户端映射引文／叙述、空change_types或server_api自报均不能把任务背景变成API约束。三方法终点均增加api_constraints：每个constraint类型派生项恰好对应一个item_id、effect_domain=server_api和结构化predicate；没有约束则显式空数组。对应项／ID／操作有缺失、重复或多余即拒绝；声明不是自然语言真值证明。

## 闭集谓词与四操作覆盖

| 谓词 | 结构 | 完整覆盖 |
| --- | --- | --- |
| field_exists | expected必须为true，qualifiers={} | remove_field、replace_field |
| value_semantics | expected为来源原文中的含义描述，qualifiers={} | change_value_semantics |
| response_shape | expected非空对象，值为精确字面值；qualifiers仅exact_keys、http_status、field_types | remove_field、replace_field、change_value_semantics；exact_keys=true时再含add_alias |

response_shape的http_status为100–599整数或null；field_types的键须在expected中，类型与字面值一致，类型枚举为string/integer/number/boolean/object/array/null。原only_keys、status_type、任意自定义键不自动转换；未识别表达停止提案。expected含全部声明subject字段。来源需包含路径、字段、字面值和限定的受支持表面形式；许可／条件／疑问不是无条件义务，混合领域应选择更窄引文。英文和中文受限形式之外可能误拒，不宣称通用NL推理。

所有方法使用同一个coverage函数。终点change_types与导出集合完全相等，顺序不作语义要求，但重复或漏项／多项拒绝，不静默补齐。增量接受后的状态／后续请求／终点请求显式保存该覆盖映射及联合哈希。增量最终摘要还须包含全部active约束，结构化谓词、subject、lifecycle、interpretation与实际维护状态一致；可附真实inactive历史，不能凭最终摘要新造约束或重新激活。

## 范围评估与旧内核复用

API操作仍交给冻结的原子状态内核；来源、主体、版本、撤销／替代、局部例外、条件与精确值授权规则不放宽。新DomainEngine只在验证新格式后透传同一批次原文，不修补错误。无约束背景批次继续仅推进权限游标。

评估时将冻结state.assess_impacts的约束选择器短暂绑定为同一个coverage函数，以修正旧按predicate.kind选择导致的response_shape值语义覆盖差异；grant/exception/condition和精确值判断继续复用。单并发版本锁内使用，退出／异常立即恢复旧选择器；不修改冻结源码或历史状态。独立R4使用同一声明策略，从实际请求／响应和转换重建，不使用作者正确状态。仅覆盖这四类操作，HTTP状态变化等不在本次操作域，不暗示完整API兼容判定。

## 执行、成本和隔离

入口scripts/s4_constraint_offline.py仅prepare/offline/verify；start_live无条件拒绝，没有凭据回调或付费批准入口。固定传输、原执行器／单账本／工作进程期限／共同成本／独立R4复用，不复制完整运行器。绑定v3候选903c99f4…的233份依赖和新8文件，共241份；价格只沿用历史快照用于离线控制，real_transport_allowed=false。没有新的实际批准或请求权限。

普通格式、覆盖和最终状态摘要冲突停单轨；新来源校验失败停全轮；成本unknown不写0，全部失败、回查／续答和本地耗时沿原账本记录。参考、配对、原模型回答、语义审核和合成答案仅评测器可读，不进入方法输入。旧v1/v2/v3按各自原版只读回放，新格式不能追改旧接受／拒绝或分数。

验收应覆盖两个实际待审项的反事实结构控制、真实API约束正支、四操作及开放／封闭形状、未知qualifier拒绝、权限／条件／角色／截止点、完整状态摘要、生命周期与原子拒绝、同证据／强清单、三方法固定演练及R4、旧真实回放与字节保留。合成控制不是模型改善；原始旧响应在新格式拒绝不是旧评分失败。

未证明：自然语言语义完整性、所有合法表达／组合、所有条件含义、所有API变更类型或未见家族。参考仍provisional，逐例未签收，独立审核not_performed_skipped_by_user；不要求重新找审核者，也不创建新真实执行候选。
