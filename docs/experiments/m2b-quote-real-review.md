# M2b 新版引文契约真实验证

日期：2026-10-20。运行 ID：`m2b-3425de7b92bc`；`execution_kind=real_model`，模型 `deepseek-flash`，输出契约 `source_quote_v1`。这是三种授权历史 × 两种完整上下文方法的**一次开发运行**，不是正式方法比较或泛化实验。旧版 `source_span_v0` 的六任务失败记录和评分保持原样，见 [首次真实接通](m2b-real-review.md)。

## 运行与结果

- 运行前重新核对 [DeepSeek 官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)：`deepseek-flash` 高峰时段缓存未命中输入 2 元/百万 token、输出 8 元/百万 token，与本地保守费用规则一致。无密钥预检为 6 个请求，请求集合 SHA-256 `3ba28e8a6939530101a7a6ad8957d38b095c8b2573c73c1fe7fc9baf26595377`；同一历史的两方法使用相同公开证据。单并发、每任务一次、最多六次请求、每次 90 秒超时、最多 4096 输出 token；本地预留估算 0.526636 元，低于本次 20 元上限。
- 实际发起并收到响应 **6/6** 次；`finish_reason=stop`，均返回 token 用量。运行工件集合 SHA-256 `66af6a0d7120724f0f759d8d98fc15223f73a258c86041e829852816ae1d106d`。运行记录位于 Git 忽略的 `reports/local/m2b-quote-live-20261020/`；独立评分位于 `reports/local/m2b-quote-live-score-20261020/`。两目录不作为已发布仓库材料。
- **5/6 输出通过正式结构与来源校验**：`full_checklist` 3/3，`full_direct` 2/3。五个有效输出的 `impact`、`authorization`、`decision` 均与项目编制的草稿参考标签一致。直接基线 c001 因漏掉必填的 `checklist` 字段而为 `invalid_output`，三个判断轴在正式计划任务分母中均不得分；这是输出格式失败，不是正确拒绝或引文位置错误。两方法分别为三轴全对 3/3 与 2/3；样本只有一个合成场景的三个历史，不能据此宣称清单方法更优。
- 对唯一失败响应只做**内存诊断**：补上缺失的 `checklist: null` 后，`source_quote_v1` 能唯一定位其中 10 条引文。此诊断没有改动保存的响应、验证器契约或正式评分，不能把它计为第六个有效输出。新运行未出现旧版的字符偏移错误；但一次开发运行不足以证明模型总会遵守新契约。
- 提供商报告输入 28,901 token、输出 7,182 token，其中输入缓存命中 1,024、未命中 27,877。按高峰时段**全部输入按缓存未命中**估算为 0.115258 元（直接基线 0.052712、清单基线 0.062546）；提供商实际账单字段未知。失败任务的用量和费用包含在总数内。本地预留 0.526636 元不是实际账单或提供商侧硬上限。

## 复核与边界

运行命令与评分命令分离，评分器只在运行完成后读取私有参考标签。可在本地分别查看 `run.json`、六个 `job_*/attempt_01.json`、`summary.json` 和 `summary.md`；`read_run` 会逐件验证记录哈希。请求只使用公开白名单输入，未传入私有标签、参考修复或项目设计文档；用户密钥只从环境读取，记录仅保存环境变量名。旧运行仍为两方法各 0/3 有效输出，不按新契约回写。

本次从 Windows 用户级环境变量注入密钥后执行 `python -m writscope.m2b run --output reports/local/m2b-quote-live-20261020 --max-cny 20`，随后独立执行 `python -m writscope.m2b score --run reports/local/m2b-quote-live-20261020 --labels evaluation_private/case_001/labels.json --output reports/local/m2b-quote-live-score-20261020`。这些输出目录已存在；重复运行必须另取目录并单独授权费用。再次校验两次真实运行的工件哈希均未变化，新运行六份请求的 `output_contract` 均为 `source_quote_v1`；在新运行保存的工件中未发现密钥原文。

这次完成了新版引文契约的真实有效性闸门：明确得到 **5 个有效输出、1 个缺失字段的负例**。它不证明授权语义抽取正确、方法优劣、成本优势或增量状态效果。下一小步仍是 [M3a 最小增量状态内核](../archive/planning/research-direction-2026-10-20.md)，先离线实现；是否调整直接基线的可空字段契约或提示词，须另起版本和运行，不能事后改分。
