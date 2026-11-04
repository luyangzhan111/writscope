# v5 真实试跑复核：三条契约失败，离线回放通过

2026-11-04。负责人本机于北京时间 00:16 启动已单独批准的候选 `cb8e107401102f00cba2b67623d9286b492ea062a631b0db76535b6d2c8665bd`。本次仅检查保存记录和离线重建，不读取密钥、不发新模型请求、不补跑、不外发项目材料、不产生新实验费用、不更新 GitHub。

**实际保存 3 次 HTTP 请求，三方法各一次；0 完成、3 失败、0 整轨未运行，有效主终点 0/3。** 三次均 HTTP 200、finish_reason=stop，失败原因是输出契约检查，不是连接、超时、截断、输出大小或预算耗尽。原始 root.stop_reason=null 保留；普通契约错误分别停止单轨，三项主分母完整保留。

R4 按冻结版本验证 253 份源码/规格/提示词绑定、实际请求/实收响应、时序、批准/claim、账本及失败位置，独立重建结果为 `verified_settled`，随后才读取固定评测器侧参考。软件回放通过表示保存的成功/失败过程可重算，不把失败改成有效回答，也不是独立人工研究审核。

## 三方法结果与费用

| 方法 | 请求 | 原运行主结果与具体原因 | 冻结峰时 token 估算（CNY） |
| --- | ---: | --- | ---: |
| full_direct | 1 | failed，无有效主输出；字段存在项引文缺 API 范围，另有值语义派生项缺范围锚点引用 | 0.02250144 |
| full_checklist | 1 | failed，无有效主输出；exact_keys=true 的 response_shape 覆盖遗漏 add_alias | 0.02144856 |
| incremental | 1 | failed，e001 第二项值语义锚点缺 API 范围；整批未提交，接受事件 0/5 | 0.01829816 |
| 合计 | **3** | **0 完成／3 失败；无有效终点** | **0.06224816** |

直接和清单捕获文本均写 breaking / not_covered / revise，但整份响应被预定契约拒绝，原预测仍为 null，不补记有效三轴或改分。增量没有 assessment 或终点。评测器保留各方法 planned=1、failed=1、no_valid_output=1；accuracy/model_metrics 仍为 null，不能把这一轮称作“语义准确率 0%”，也不能把无效文本里的三轴拿来记成功。

## 重点一：值语义引文被部分采用，完整契约尚未满足

### 直接复核

原始首个拒绝为 `API_constraint_lacks_explicit_structured_source_support`，定位到第一项 `c_status_exists`。它只引用 e001 的：

> 响应对象只能包含 status

该片段真实、唯一且来自用户，但不自含冻结契约要求的 API 路径及范围。API 信息虽然在整段历史和别的派生项中存在，当前规则不会跨项自动补入该项的来源。不是伪造原文，也不是 JSON 无法解析；这是范围引用表示与冻结契约不一致。

逐项只读调用同一冻结守卫，还定位到随后 `c_status_semantics` 的独立问题：

- `expected.source_text` 为原文“值为字符串 active，表示账户启用”。
- `expected.explanation` 为英文说明，已与原文字面含义分开；不再把英文转述直接当作规范性原文。
- qualifiers 的 scope_source 是完整 API 句，meaning_source 是其包含的局部含义片段，两者都指向 e001。对原声明单独做只读 grounding 检查，唯一引文、包含区间和字面来源通过；这不是原运行已经接受该响应，也不验证英文翻译。
- 该派生项自己的 source_refs 只有局部含义片段，缺少其声明的完整 scope_source。冻结规则要求每个值语义派生项自己同时引用两份来源，单项守卫因此拒绝 `meaning_item_must_cite_its_own_scope_and_meaning_sources`。

直接的响应形状项通过逐项检查；三项声明的 2/1/4 类操作覆盖也与各自谓词一致。第一项已使完整输出拒绝，后续只读检查用来定位被首错遮住的问题，没有修复回答或生成修复后的主结果。

### 增量首事件

原响应是合法 JSON 的三项 mutation batch：API 响应形状、值语义、客户端背景。第一、第三项分别通过对应操作的只读检查；第二项 `c_profile_v1_status_meaning` 的外层 source_quote、scope_source 和 meaning_source 都选成局部句：

> 值为字符串 active，表示账户启用。

原文字面、当前用户 e001、同事件包含和外层/锚点相等本身成立，但该句没有 `/api/v1/profile`、status 主体及明确 API 范围，AddConstraint 的冻结守卫报 `API_constraint_source_scope_unresolved`。API 锚点不能从同批第一项自动借用。

拒绝发生在响应契约阶段，尚未调用状态转换；三项整批未提交。保存的 transitions=[]、assessment=null、final=null，初始请求的 through_seq=0、约束/授权/例外/背景均为空。这里不是“内核已提交第一项，再拒绝第二项”，也不是 v4 那种拒绝后事件游标消费到 e003 的情形。

## 重点二：no_change 当前事件绑定尚未实际触发

已核对实际发送的增量 schema：NoChange 只有 operation、target_ids、constraint、authorization、value_scope 五个字段，禁止额外字段，不含 source_quote。请求提示词及 schema 哈希匹配最终修订版本；不是沿用了 v4 旧版。

本轮增量只到 e001，实际三项操作为 add_constraint / add_constraint / record_context，**没有 no_change 响应，程序绑定次数为 0**。e002/e003 工具事件及后续事件均未到达，因此登记 `not_exercised`，不能说“修复已在真实模型中验证”，也不能说“再次发生 e002/e003 引文错绑”。

日志首条“list[NoChange] 最多 1 项、实际 3 项”来自校验器尝试 Union 中另一个候选分支，是分型错误噪声。mutation batch 上限为 8，三项这个长度本身合法；本轮真正失败是第二项 AddConstraint 的范围守卫。没有把 NoChange 分支噪声当成独立失败或计数。

## 重点三：增量终点摘要没有到达

e001 即失败，后续四事件、assessment、incremental_final 和终点摘要全部未运行。没有维护后的终态可拿来评分，也不离线补出模型 final。状态与终点摘要一致性记 `not_reached`，而非通过或冲突。

本轮两份捕获终点虽然分别属于直接/清单方法，也均无效；它们不能代替增量终点。lookup=not_exercised，没有回查、续答、重试或自动补跑。

## 背景、覆盖及计费核对

两份捕获 final 各把客户端映射与任务压力放入两条 task_context，没有派生 authorization；增量未提交的第三项也单列客户端背景。未在这些捕获内容中观察到旧版把客户端许可直接提成服务端授权的问题；这只是无效输出/未提交批次的局部观察，不能签收完整语义提取。

清单的 `c2` 声明 response_shape、expected={status:active}、exact_keys=true。其 source_refs 包含完整 API 句，字段类型和 HTTP 状态声明通过单项来源支持检查；遗漏是 change_types 只有 remove_field / replace_field / change_value_semantics，缺 add_alias。它是“完整约束范围”枚举缺项，不是本补丁新增了别名，也不因本补丁只替换字段就减少约束覆盖。原守卫正确拒绝，不自动补枚举。

返回用量：输入 **25,314**、输出 **2,801**；缓存命中 **5,504**、未命中 **19,810**。按本轮冻结峰时费率：

`(5504 × 0.04 + 19810 × 2 + 2801 × 8) / 1,000,000 = 0.06224816 元`

约 **0.06225 元**是实收 token 的峰时估算，不是实际扣款。发生时间、禁用工具或零重试均不替代服务商账单；三份 reasoning 与工具计数缺失保持 unknown，组合账单及实付仍 unknown，不当 0。失败的三请求完整计入。

本地条件预留 **6.389760 元**，不是费用，失败不释放；30 元授权上限也不是预计消费或服务商账单硬保证。整轮墙钟约 **36.78 秒**，传输及清理约 26.468 秒，余下约 10.310 秒由原共同成本表记录为本地准备/验证/账本开销；没有本地状态更新阶段。本次离线审核耗时不计入原方法运行成本，历史共享证据制作成本仍 unknown。

## 保留、授权及后续

原始七文件在 `reports/local/s4-owner-pilot-real-v5/`；本次前后逐文件哈希相等。**922 份既有文件**，包含 v1–v4 原运行/评分、v5 冻结候选/提示/源码/原离线验收、批准原文及已用 claim，保持原字节。原 root.actual_model_calls=null 等历史元数据保留，实际三次 HTTP 观察和当前状态另立 closeout，不回填或改分。

新评测、逐项只读来源/覆盖诊断、未提交前缀、费用、收尾和保留核验在 `reports/s4-owner-pilot-real-v5-review-v1/`；固定运行摘要为 `closeout.json`，语义检查为 `semantic-audit.json`。批准、origin.launch、确认词及 claim 相互核对一致，**本次授权已使用，remaining_authorized_starts=0**。剩余 10 个请求槽和本地预算余量不构成补跑或补终点权限。

下一步可先离线审查每条约束的范围引用、双位置引用一致性、完整操作覆盖及错误分型摘要；本轮不改冻结契约，不准备或启动下一次付费运行。有效终点和真实完整增量链仍缺；同一例已反复调试，不能主张方法趋势、泛化或费用效率改善。

参考仍 provisional，逐例未签收，独立审核 `not_performed_skipped_by_user`。本次为项目内 AI 非盲规格核对，不是独立研究验证。助手新增模型请求 0、密钥读取 0、项目材料外发 0、新实验费用 0、GitHub 更新 0。
