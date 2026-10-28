# GitHub 首次公开发布

日期：2026-10-28。用户明确要求在 `luyangzhan111` 账号下新建公开仓库 `writscope`；授权覆盖本次建仓、开发版和既有提交历史上传，以及自动测试核验和必要发布说明同步。

## 已执行

- 已创建公开仓库 [luyangzhan111/writscope](https://github.com/luyangzhan111/writscope)，默认分支 `main`。
- 使用本机已有 GitHub 登录完成建仓与 Git 上传；令牌仅在内存中使用，没有显示、保存或提交，也未创建新密钥。
- 原始候选 [1a7d412](https://github.com/luyangzhan111/writscope/commit/1a7d41233944a6c2774d8c5ed72151004675feb1) 及既有提交历史已上传；初始代码树为 `ec9cb20a8fe81a372717d502868e707c87ab5701`。
- 本地 `origin` 指向该仓库，`codex/first-public-release` 跟踪 `origin/main`；没有覆盖或强推旧分支。
- GitHub 插件已确认仓库为 public 且本账号具有写入权限。

## 自动测试

初始代码已完成 [No-key tests / run 1](https://github.com/luyangzhan111/writscope/actions/runs/40621481583)，整体 conclusion=success。

| GitHub runner | 完整测试 | 演示 | 历史回放 |
| --- | --- | --- | --- |
| Ubuntu | 369 项通过，76.21 秒 | 通过 | 通过 |
| Windows | 369 项通过，105.73 秒 | 通过 | 通过 |

准确被测提交为 `1a7d41233944a6c2774d8c5ed72151004675feb1`。后续提交仅同步发布文档，使用 `[skip ci]` 避免重复相同代码的整套测试；不把该说明提交冒充另一次完成的 CI。方法源码、脚本、测试、提示词、规格、锁文件和历史证据与被测提交逐字节一致。机器摘要见 [verification.json](../reports/github-publication-20261028/verification.json)。

该工作流从锁文件安装、恢复精选证据、执行无密钥完整测试、脚本演示及保存记录回放。没有配置 DeepSeek 密钥，不运行真实模型请求。

## 证据与研究边界

359 份历史证据的原始字节和归档哈希保持不变；原始候选的 `reports/public-v0.1/source-manifest.json` 与 `validation.json` 保留 2026-10-27 验收时点，不随本次发布说明追写。后续只更新发布状态文档；若远程测试发现问题，将明确记录修订范围和对应 CI。

GitHub 公开发布不等于方法有效性验收：S3 仍只完成作者审核开发出口，独立审核待补，S4 未运行。没有新增真实模型请求或付费授权，没有发表方法优势、成本收益或泛化结论。
