# S4 离线工程公开候选

本目录是本地发布准备工件；本轮未更新 GitHub。当前工程候选仍为
`0ef9be84a94f272109535b483813b5eb91f4ddb7fc158f70a8d9c89e340c17d7`，不是新的付费试跑批准。

- `evidence.zip`：内容寻址归档，48,006,924 字节；1,174 个唯一 blob 恢复为 1,389 个文件。
- `manifest.json`：原 1,046 文件路径/哈希与发布源码覆盖层，逐对象及整包哈希；旧响应、账本、参考与评分字节不改。
- `publish-files.json`：未来提交的显式文件清单，另列必须携带的清单自身；没有执行 git add、commit 或 push。
- `validation.json` 和 `checks/`：本次实际验收收据，在源码封包之外单独记录，不追写原工程验收。

恢复必须使用新目录，命令在仓库根目录执行：

```powershell
uv sync --locked --python 3.12.4
uv run --locked python -m scripts.s4_public_release restore --destination .work/s4-public-review
uv run --locked python -m scripts.s4_public_release verify --root .work/s4-public-review
uv run --locked python -m scripts.s4_public_release demo --root .work/s4-public-review
```

依赖就绪后这些操作可离线执行；verify 只回放保存记录，demo 只发送到固定模拟传输。
参考与配对等评测器侧材料即使随包公开，也不得成为方法请求。原机器路径与已使用批准收据用于回放，不能授权再次启动。

归档内文档属于封包准备时点；后来生成的验收收据在外层本目录提供，并列入发布文件清单。
模式扫描检查源码、历史、嵌套 ZIP、JSON/JSONL 编码响应和 SQLite；不认识的其他格式只作原始模式扫描，结果不构成不存在敏感信息的保证。

真实 v5 三方法均失败；当前演示通过不改变原评分。独立审核 `not_performed_skipped_by_user`、参考 provisional、逐例未签收、当前版本 270 轨及正式比较未运行。
当前候选的新远程 Windows/Ubuntu CI 尚未运行。使用说明见[发布说明](../../docs/s4/public-release.md)。
