# 客户端背景与服务端许可隔离：离线工程契约 v1

2026-11-03。依据v2保存记录：e001客户端本地映射被存为无条件server add_alias，虽未覆盖当前replace_field影响，仍是状态语义缺陷。旧源码、事件、响应、状态、终点评分及已使用批准不改。本版本只离线实现/验证，不读key、不发请求、不付费、不更新GitHub，也不是新的可付费执行冻结。

## 表示与编译

- 新增record_context操作：source_quote、verification_refs、空target_ids，constraint/authorization/value_scope均null。此通道不能授予、覆盖、替代或撤销API许可。
- 复用既有TaskContextLedger，逐事件保存可信用户唯一精确引文与公开artifact引用，ID/位置由程序派生。非用户事件仍仅no_change，完整历史/工具事实不受影响。
- server grant与授权supersede须显式effect_domain=server_api；该自报字段不是证据。原操作互斥、范围、版本、条件、生命周期、精确值规则全部保留。
- DomainEngine组合原QualifiedEngine与TaskContextLedger。先验证候选，再在副本编译，只有API批次接受时提交context；拒绝时不部分提交context。纯context事件由声明的编译规则生成仅推进游标的no_change，不是对错误grant的修复。原始模型字节始终保留。
- prior_state额外显示non_authoritative_task_context，终点状态中task_context独立存放；其哈希与API状态共同绑定，但不混入authorization/exception/value_scope。所有输入来自同轨模型响应和公开历史，不附作者正确状态。
- 转换检查点保存context更新、编译投影及范围检查结果。独立R4以同一声明编译器重新从实际响应重建，比较原检查点；不执行模拟脚本或模型。编译/背景维护耗时包含在已有transition本地计时内。

## 有界范围检查，而非通用语义证明

引文必须是当前可信用户事件的唯一精确片段。对服务端许可要求明确server/API范围、许可及相应操作的中英表面形式；grant还要求引文出现其字段标识。客户端/前端/页面/适配层与本地/映射/适配/转换等效应共现时，不能支持server许可；混合领域的整段引文应选择更窄的明确片段，不由程序自动切句或删改。

否定、疑问、条件或不确定形式及未识别表达保留未决，拒绝该候选，不当作许可，也不自动改为not_covered/revise。record_context也不能拿显式或可能的server许可句替代API授权来绕过检查。真实server授权子句在同一事件中仍可正常通过，不能因同事件另有客户端选项就全禁grant。

这是一个透明、保守的中英受限表面检查，**不是自然语言权限判定器**。它不证明意图、条件完整性、所有隐式授权或引用语义支持；不处理所有语言/反语/嵌套作用域，可能拒绝合法但不在支持形式内的表达。即使检查接受，general_semantic_proof仍为false，原核继续校验角色、引用、主体、操作、生命周期和条件。不能声称已一般性解决语义提取。

## 三方法共同规则

三方法终点均新增顶层task_context数组，保存带event_id的用户引文及公开verification_refs；payload仍用原三轴/逐影响/引用和六项清单结构。客户端实施选项应进入context，不能进入API-shaped derived authorization。派生authorization及正向covered依据使用共同范围检查；负向not_covered可引用客户端文本作为事实，不能被错误封禁。

增量schema同时容纳context与合法API操作；no_change仍独占批次，最多8项。无权限的context不触发任何例外。显式API许可、无许可、client-only与未知形式必须分别标注，不用人工参考投影修正模型输出。新增通道不增加工具权限/回查次数，终点仍保留完整历史及自身状态，清单强基线不削弱。

## 版本、失败和执行边界

入口scripts/s4_effect_domain_offline.py只暴露prepare/offline/verify；start_live一律在任何回调前拒绝。固定传输、原单账本/进程期限/费用unknown/失败停止/R4复用，不复制完整运行器。离线计划固定绑定旧v2 SHA 903d83e8…的223份依赖及8份新文件；版本字段标记offline_effect_domain_diagnostic，real_transport_allowed=false，价格仅为历史离线控制快照，不申请新费用。

新格式或范围不明停单轨并保留成本与原响应；来源/隔离/账本/额度故障停全轮。旧v1/v2通过原只读版本入口回放，不能以新检查追改原结果。反事实重检旧响应必须单列：拒绝旧client grant只证明新护栏，不是新模型结果。

验收覆盖原v2负例、伪称server_domain、合法server grant、否定/混合/未决、多语言支持边界、context无权、引用/未来/角色/条件/生命周期、原子拒绝、共同基线、同证据与三方法固定演练、中断、旧回放和恢复。没有新真实实验、正式30例/R=3比较或语义金标准签收。参考仍provisional，独立审核仍not_performed_skipped_by_user。
