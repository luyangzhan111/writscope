# S4 离线工程公开候选说明

2026-11-04。此页保留第 3 项本地发布准备口径，原实测记录保存在 [validation.json](../../reports/public-s4-v0.2/validation.json)。后续用户单独授权的 GitHub 上传、已通过的 Windows／Ubuntu 24.04 CI、失败记录及 Linux 固定适配见 [实际发布记录](github-publication-20261104.md)。源码封包和原工程候选独立版本化，不追写历史验收数；没有新增模型请求或模型费用，此页不构成真实执行批准。

## 发布内容

当前实验工程候选为 `0ef9be84a94f272109535b483813b5eb91f4ddb7fc158f70a8d9c89e340c17d7`，不是付费试跑候选。已保存的离线证据为 263 份绑定文件、51 项定向测试、9 组控制共 67 次模拟尝试、1,038 份恢复清单工件及 11 份报告精确重算，942 份既有文件不变，详见[原验收](../s4-contract-convergence-offline-20261104.md)。这些数字不计作本次发布测试的新通过数。

新归档计划位于 `reports/public-s4-v0.2/`。它按原 [1,046 文件清单](../../reports/s4-contract-convergence-v2/manifest.json)闭包保存原始材料，以 `blobs/<sha256>` 内容寻址去重；同一字节只存一份，原路径与原哈希仍分别保留，恢复时写回所有原路径。原清单、源文件、真实/固定响应、状态、账本与评分不改写。新源码封包另含当前文档、新发布器/扫描器及测试和旧版跟踪测试，使独立根具备明确检查集所需依赖。

原归档中可能保留历史机器路径与已消耗批准收据。它们用于回放，不是独立宿主证明，也不授权新的调用。发布包装不读取 `.env` 或实际密钥，不把本机全部 `reports/local/`、`.work/`、虚拟环境或未选择的开发产物整体打包。

`evaluation_private` 中的参考、配对和审核材料可作为可审计工程材料随选定归档保存，但始终属于评测器侧。恢复后目录存在不意味着方法获得读取权限；方法请求只使用公开输入白名单与自身生成状态，不发送参考、配对、隐藏测试、修复或固定答案。

## 离线使用

冻结环境要求 Python **3.12.4** 和原 uv.lock 中的依赖版本；校验器会拒绝环境漂移，不能用另一补丁版本冒充原环境。安装方法见[根 README](../../README.md)。在仓库根目录，用一个不存在的目标目录运行：

```powershell
uv run --locked python -m scripts.s4_public_release restore --destination .work/s4-public-review
uv run --locked python -m scripts.s4_public_release verify --root .work/s4-public-review
uv run --locked python -m scripts.s4_public_release demo --root .work/s4-public-review
```

- `restore` 核对归档与每个 blob，将原冻结文件恢复到独立根；不覆盖当前工作树中的新版 README 或导航。
- `verify` 从恢复根加载绑定代码并检查保存轨迹、来源、状态、终点及账本；再按原协议使用评测器参考。来源故障和中断未决仍只验证前缀。
- `demo` 使用固定响应走三方法完整链；它不运行模型，不产生模型准确率或新的账单。

实际输出字段、归档大小和验证结果在本轮独立验收记录中列出。历史价格快照只供固定控制和旧记录核账，不是当前报价或预算授权。

## 公开候选的检查集

CI 明确选择旧版已跟踪的 **48 个测试文件（47 个测试模块及 conftest）**、当前契约的 **51 项定向测试**及新增发布/扫描安全测试。前者对应首发验证模块，369 是旧首发记录而非本次新结果。新契约测试在独立恢复根中执行，避免从 checkout 加载代码却从另一个根找数据，也避免同进程模块缓存混用版本。

该选集不是全仓库历史 S4 测试重跑。旧 S3 精选依赖通过旧 `public_release restore` 恢复；新冻结依赖从新归档恢复。只有干净候选和独立根中的实际运行能证明没有遗漏依赖。源码、公开包、提示词及 schema 继续精确绑定，不能通过跳过失败用例来缩减已经声明的检查集。

发布扫描需覆盖明确的源文件清单、Git 历史、归档实际成员与可解析的嵌套证据；不得只扫描 zip 压缩字节。JSON 的固定响应编码和 SQLite 保存的请求/响应也在检查范围内。匹配内容不应回显为凭据；报告给出位置与类型。模式扫描不是无泄露的数学证明，原始机器路径与 consumed receipt 也不能直接等同密钥。

归档检查还应拒绝路径穿越、符号链接/链接替换、重复或冲突成员、非法路径、哈希或长度不符、清单与实际 blob 不一致，以及不受限解压或递归。内容相同的 blob 可以复用扫描结果，但全部原逻辑路径和复原哈希仍须核对。

## 本轮发布验收记录

| 项目 | 对应机器记录 |
| --- | --- |
| 新公开归档生成、压缩后字节数、唯一 blob 数 | manifest.json 的 files/object_count/archive_bytes |
| 原 1,046 文件逐路径哈希与旧字节保留 | validation.json 的 preservation；不改旧清单 |
| 当前文档、新工具/测试的源码包与依赖闭包 | validation.json 的 recovered_root 和源码哈希核验 |
| 源文件、历史与递归归档凭据扫描 | validation.json 的 scan；命中与覆盖不足分别保留 |
| 旧测试文件的本轮实际结果 | validation.json 的 tests 分组；369 只标首发历史基线 |
| 当前契约在独立公开恢复根运行 | validation.json 的 tests；不拿原本地 51 项代替 |
| 新发布/扫描安全测试 | validation.json 的 tests；以实际 JUnit 计数为准 |
| 独立根回放、无密钥演示及恢复后保留检查 | validation.json 的 replay/demo/preservation |
| 新远程 Windows/Ubuntu CI | 本地准备时未运行；后续已通过，见[实际发布记录](github-publication-20261104.md)，原 validation.json 不追写 |

这些记录在本轮实际检查后单独写入。源码封包保留准备时点，与后来生成的验收收据分开绑定；报告保留失败、环境问题、未覆盖项与真实运行根，不将计划写成通过。新报告作为额外发布文件列入 inventory addendum，不回填原冻结清单。

## 研究与历史边界

真实 v5 三方法均输出契约失败，原评分保持失败。真实 no_change 未触发，增量终点未到达；固定演示通过不改变这三个事实。当前版本 30×三方法×R=3 的 270 轨没有运行，旧 R5 的 270 轨只对应旧版本。

参考仍 provisional，逐例未签收；独立审核为 `not_performed_skipped_by_user`。S3 是作者审核开发验收，不是独立研究验证；等配置主块保留，预算匹配后置。正式数据/实验冻结、真实比较、费用和材料外发另按具体新计划处理，本次只整理离线工程公开候选。

旧首发入口 `python -m scripts.public_release restore/demo/replay`、[旧证据说明](../../reports/public-v0.1/README.md)、[旧文档导航](../README.md)与[首发记录](../archive/releases/github-first-publication-20261028.md)原样保留。当前导航见 [S4 README](README.md)。
