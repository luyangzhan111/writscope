# failure_summary_v1 离线摘要透传契约

适用：已保存、可按原冻结入口回放的 v2 记录；不定义新的真实执行范围或授权。

实现：scripts/s3_failure_summary_v1.py。先运行原 Cursor.consume；仅允许在增量调用 state_rejected 且其 error 为 null 时，以唯一匹配 core_call_id 与事件序号的 batch_rejected/context_rejected 转换错误补齐返回值与 cursor.failure.error。batch 必须明确拒绝且 error/rejection_reason 一致。调用状态、阶段、pending、响应、状态、任务背景、计费和后续停止语义不变；已有错误不覆盖，无法唯一确认的缺失继续保留，不从模型正文推断。

scripts/s3_local_mapping_diagnosis.py 在原 verify 通过后另存 offline_saved_response_diagnostic：raw_failure 保留原值，failure 为新派生摘要，calls 保存 raw_error/error 及原响应文本哈希，jobs 保留九行含未运行项，reproduction 引用原拒绝批次。metrics=null，new_model_calls=0，不宣称新增真实结果、纠正模型响应或通过完整 S3。run 只新建独立目录；verify 从原记录再次计算并逐字段核对，不能通过改派生诊断绕过。

只读输入是原预检与运行工件及原运行器白名单公开数据，不接收模型提供的路径、标签或人工正确状态。新适配层不自动替换旧模块，既有预检与源码哈希仍有效；新真实入口接入为后续工作。
