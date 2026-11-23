# Immich 将旧 ping 接口标记为弃用后，原字段还在吗？

2026-11-23，WritScope 项目内首次使用外部项目的真实变更做回顾。Immich 的 [PR #10677](https://github.com/immich-app/immich/pull/10677) 于 2024-06-28 合并，主题是迁移 `/server-info` 接口。本次使用 PR 两端提交中保存的完整 OpenAPI JSON，只读分析公开材料。Immich 维护者未参与本次试用。

## 审查问题与结果

审查前先限定一个旧客户端可观察的保证：它调用 `GET /server-info/ping` 时，HTTP 200 JSON 响应是否仍承诺包含字符串 `res`？人工查看两份契约，两边的 `ServerPingResponse` 都将 `res` 声明为 `required`、`type: string` 和 `readOnly: true`。PR 结束时旧操作新增了 `deprecated: true`。

| 实跑 | 结果 | 依据 |
| --- | --- | --- |
| 首次使用原有审查器 | `unknown / not_needed / investigate` | 两侧均在解析只读标量时停止，报告未能判断字段。此处授权尚未评估。 |
| 加入布尔 `readOnly` 的响应语义后 | `compatible / not_needed / allow` | 原路径仍承诺相同的必需字符串 `res`。用同一对输入重算后，HTML、JSON 和清单均逐字节一致。 |

这条 `allow` 只回答上面的字段问题。在这两份契约中，旧路径都存在，新路径 `/server/ping` 都未列出。对新路径单独运行得到 `investigate`。本次没有启动 Immich，也没有验证运行时路由或整个 PR 的兼容性。

PR 留下了三条正文为空的批准评审。公开材料足以确认有人批准这份 PR，无法从这些评审中提取删除 `res` 的明确许可，因此本次未提供授权台账。旧字段仍被保留，得出当前字段结论无需借用这三条批准。[PR 讨论与评审](https://github.com/immich-app/immich/pull/10677)

## 在本机复现

安装 WritScope 后，从其源码目录执行以下 PowerShell 命令。下载仅涉及两个固定提交的公开 JSON 文件。命令同时核对 SHA-256。

```powershell
$caseDir = Join-Path $PWD '.work/immich-pr-10677'
New-Item -ItemType Directory -Force -Path $caseDir | Out-Null
$rawBase = 'https://raw.githubusercontent.com/immich-app/immich'
$spec = 'open-api/immich-openapi-specs.json'
$base = 'e361640e3904bc8c606883c278369fd158a959f0'
$head = 'e51a7d6a3d08e366fbe16361ec963ef958115fc7'
Invoke-WebRequest "$rawBase/$base/$spec" -OutFile "$caseDir/before.json"
Invoke-WebRequest "$rawBase/$head/$spec" -OutFile "$caseDir/after.json"
if ((Get-FileHash "$caseDir/before.json").Hash -ne '4696AD7D3CE5ED70E1BA17FF7FB9C9D7142D6D2CFC29880E17AFC7E031DDF4E0') { throw 'before.json 校验失败' }
if ((Get-FileHash "$caseDir/after.json").Hash -ne 'E87F6872D9B3F3B24616EBA7B7617F4EF4C285895862498AF0296FD116318605') { throw 'after.json 校验失败' }
uv run --offline --locked writscope review --before "$caseDir/before.json" --after "$caseDir/after.json" --method GET --path '/server-info/ping'
```

打开输出的 HTML，查看两份契约指向的 `#/components/schemas/ServerPingResponse`。在输出目录中运行 `uv run --offline --locked python -m writscope.review` 可复算保存的输入。将上面审查命令的路径换成 `/server/ping`，可复现缺少路径时的结果。

## 对产品的影响

这次试用促成了一项可验证的改进：布尔 `readOnly` 可以出现在受支持的响应标量上；旧响应失去必需字段时，工具仍报告破坏。对于这笔迁移，审查者还需要另看操作的弃用状态。运行结果支持改进字段复核，用户耗时和采用意愿仍待实际使用者验证。
