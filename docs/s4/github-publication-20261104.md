# S4 GitHub 更新与双平台 CI

2026-11-04。按用户“按已验收清单更新 GitHub，并核验 Windows／Ubuntu CI，保持模型真实发送关闭”执行。已更新 [luyangzhan111/writscope](https://github.com/luyangzhan111/writscope) 的 main；没有新增真实模型请求、读取模型密钥或启动付费试跑。

## 已发布与实际验证

首个提交 `7f32e6544e6c7ca0cff3c8490dfceaf7ba36e8d0` 的文件树精确对应已批准的 653 个路径，包括清单自身；18 份被 Git 忽略的精选历史 Markdown 仅按这份白名单加入。原工作区分支与未选择改动保留，发布在独立 clone 完成，无强推。

最终受测代码提交为 [`07f5108e354727b8c6f766337b67c7c6eb4ec328`](https://github.com/luyangzhan111/writscope/commit/07f5108e354727b8c6f766337b67c7c6eb4ec328)。新增三个 Linux 固定传输适配文件，另更新未冻结发布器和外层清单绑定；这时公开清单共 656 个路径。原工程候选 `0ef9be84…`、263 文件绑定、48,006,924 字节源归档、原 1,046 文件及真实结果/评分不改。后续状态文档和本页收据单独同步，不计作新的实验结果。

[最终 Actions run 41792070076](https://github.com/luyangzhan111/writscope/actions/runs/41792070076) conclusion=success，精确 head SHA 为上述 `07f5108e…`。

| 平台 | 实际结果 | 回放与演示 |
| --- | --- | --- |
| Ubuntu 24.04 | **494 passed，134.72 秒**；包含 3 项 Linux 实际进程、总超时回收、父进程死亡控制 | 9 份当前报告及 2 份历史成功/失败报告一致；三方法完成，8 次固定模拟尝试 |
| Windows | **公开检查集步骤成功**；子进程日志没有输出 pytest 计数，不填造直接记录值 | 测试、11 份回放、固定演示步骤全部成功 |

这仍是明确选择的公开检查集，不是全仓库全部历史 S4 测试。新 Linux 控制在 Windows 的跳过不冒充 Linux 通过。原本地准备的 482 项记录保留当时时点；当前跨平台结果以上表、原始 Actions 日志和 [publication.json](../../reports/github-s4-publication-20261104/publication.json) 为准。

## 两轮失败及修订

| 代码 / run | 失败事实 | 后续处理 |
| --- | --- | --- |
| `7f32e65` / [41787829890](https://github.com/luyangzhan111/writscope/actions/runs/41787829890) | ubuntu-latest 已滚动至 Ubuntu 26.04，不提供冻结要求的 Python 3.12.4；Windows 被矩阵 fail-fast 取消 | `24134eb` 固定 Ubuntu 24.04，并关闭矩阵 fail-fast；保留原 Python 与锁文件 |
| `24134eb` / [41789091103](https://github.com/luyangzhan111/writscope/actions/runs/41789091103) | Windows 完整成功；Ubuntu 477 passed、5 failed，固定传输在发送前被旧 Windows-only WorkerJob 拒绝 | 另立固定模式 Linux 适配及原生 receipt，不修改旧进程监管或原失败结果 |

所有失败日志与结果在 `reports/github-s4-publication-20261104/` 另存。没有跳过原 5 项失败联动来取得成功，没有重放任何旧批准的真实运行。

## Linux 发布适配的范围

[新适配规格](../../specs/s4-public-linux-fixed-v1.md)只支持固定响应，拒绝 live 或非空 credential。Linux bootstrap 校验固定配置与父 PID，设置 PDEATHSIG，独立会话/进程组清理并由旧 receiver 回收。原生 receipt 保留 `s4_public_linux_fixed_receipt_v1` 和 `linux_pdeathsig_process_group_fixed_v1`；仅在 verifier 内存中披露两字段词汇投影，不能把 Linux 保存记录写成 Windows Job 证明。

外层 `runtime_overlay` 精确绑定发布入口、适配、测试、规格四文件。恢复先核对旧归档原字节，再仅替换未在原 1,046 文件中的发布入口、增加三个版本化文件；原模型状态/来源/范围/生命周期内核与真实传输未改。旧 Windows 保存 receipt 继续原规则只读验证。Linux 真实传输没有因此开放。

## 当前边界

- 本轮项目材料仅按明确发布范围发送到 GitHub；没有发送到模型服务。
- 模型真实发送关闭、模型密钥读取 0、新模型请求 0、新模型实验费用 0。
- 原参考 provisional，逐例未签收；独立审核仍 `not_performed_skipped_by_user`。
- 当前契约 270 轨与正式真实比较未运行，不能由固定响应 CI 推出方法效果、费用优势或泛化。
- `reports/public-s4-v0.2/validation.json` 与扫描结果中的 `github_updated=false` 保留本地准备时点/只读命令含义，实际上传与新 CI 在本页和 publication.json 另立记录。

后续若只提交状态说明与收据，使用 `[skip ci]`，并明确实际受测的是 `07f5108e…`；不把文档提交冒充另一次新 CI。
