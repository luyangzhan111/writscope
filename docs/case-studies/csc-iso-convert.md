# Country State City 的 ISO 转换接口还承诺返回 `value` 吗？

依赖旧版 OpenAPI 编写的客户端可能会读取 `response["value"]`。Country State City 在 2026-09-21 合并了官方 API Playground 的一笔修正。合并前后，请求都用 `value` 传入国家代码，但新版响应只承诺用 `input` 回显该值。上游作者解释，线上服务原本就返回 `input`，这次修的是文档。WritScope 于 2026-11-23 回顾了两份完整的 OpenAPI 3.0.3 契约，没有访问线上转换接口。[上游 PR #9](https://github.com/dr5hn/csc-swagger-playground/pull/9) · [Playground 对契约文件的说明](https://github.com/dr5hn/csc-swagger-playground/blob/928a4ed7852975790dcb94df179fb9c879fa92ac/README.md)

## 契约发生了什么变化

审查范围为 `GET /iso/country/convert` 的 HTTP 200 `application/json` 响应。两版响应都引用 `#/components/schemas/IsoConvert`。

| 核对项 | 变更前 | 变更后 |
| --- | --- | --- |
| 响应必需字段 | `from`、`to`、`value`、`result` | `from`、`to`、`input`、`result` |
| 响应中 `value` 的声明 | 必需字符串，位于 `#/components/schemas/IsoConvert/required/2` | `properties` 和 `required` 均无此字段 |
| 响应中 `from`、`to` 各自的取值 | `iso2`、`iso3`、`numeric` | 同一组值 |
| 请求的必需查询参数（人工核对） | `from`、`to`、`value` | `from`、`to`、`value` |

## 实际运行结果

| 使用的审查器与接口 | 影响 / 授权覆盖 / 决定 | 证据 |
| --- | --- | --- |
| WritScope `303b7dd`，ISO 转换接口 | `unknown / not_needed / investigate` | 两侧均在解析 `from` 的字符串枚举时停止，未进入字段影响或授权判断。 |
| 支持字符串枚举后，同一接口 | `breaking / not_covered / revise` | 一项影响。旧响应失去对 `value` 的必需保证，两侧枚举相同。 |
| 支持字符串枚举后，同一对文件中的 `GET /timezone/{ciso}` | `compatible / not_needed / allow` | 操作及其引用的 `TimezoneInfo` 均一致，五个原必需字段仍有保证。 |

本轮未传入维护者授权台账。`not_covered` 描述本次运行可核验的许可材料。上游 PR 明确记录了作者的改名意图和合并结果。对依赖旧版文档的客户端，复核动作是查找读取响应 `value` 的代码并确认迁移；请求参数仍应使用 `value`。[实际运行的 JSON 报告](csc-iso-convert.review.json)

## 在本机复现

按[复核指南](../offline-review.md)安装当前版本后，在仓库根目录执行以下 PowerShell 命令。首次获取两个固定提交的公开文件后，复核和重算均可离线执行。

```powershell
$root = Join-Path $PWD '.work/csc-iso-pr9'
New-Item -ItemType Directory -Force -Path $root | Out-Null
$raw = 'https://raw.githubusercontent.com/dr5hn/csc-swagger-playground'
$base = 'e0c854930639a50122e040f6240fff83ad57b07a'
$head = '928a4ed7852975790dcb94df179fb9c879fa92ac'
Invoke-WebRequest "$raw/$base/config.json" -OutFile "$root/before.json"
Invoke-WebRequest "$raw/$head/config.json" -OutFile "$root/after.json"
if ((Get-FileHash "$root/before.json").Hash -ne '27CD8381D9F1D9ADC3D7BB876E446BABCC496010234CC88B979D9256860712A2') { throw '旧契约哈希不符' }
if ((Get-FileHash "$root/after.json").Hash -ne '5751F74BE8FC1EC2EB3B632F28EE279064ADD6DF35382DCAEB635C5764514817') { throw '新契约哈希不符' }

$inputs = @('--before', "$root/before.json", '--after', "$root/after.json", '--method', 'GET')
uv run --offline --locked writscope review @inputs --path '/iso/country/convert' --output "$root/convert"
uv run --offline --locked writscope review @inputs --path '/timezone/{ciso}' --output "$root/control"
```

输出目录需为新目录。本次分别从两份报告保存的输入重算，两个接口各自的 HTML、JSON 和清单均逐字节一致。要核对本机主要结果，可将 `convert/review.json` 与随案例发布的报告比对。

```powershell
if ((Get-FileHash "$root/convert/review.json").Hash -ne (Get-FileHash 'docs/case-studies/csc-iso-convert.review.json').Hash) { throw '与案例报告不一致' }
```
