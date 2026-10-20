# M2b 离线验收：DeepSeek 六任务接通试跑准备

日期：2026-10-20。状态：**代码、无密钥预检与离线测试已完成；真实接口调用未运行。** 用户授权 DeepSeek API 总费用上限 20 元，当前无法设置凭据，并要求先完成代码与离线验收。M2b 的实际接通门槛是可用凭据、运行前价格核对、六项真实响应与保存后独立评分；本记录不能替代该门槛。

## 本轮落地

- 固定一个后端：`https://api.deepseek.com/chat/completions`，模型 ID `deepseek-flash`，JSON 输出模式、关闭 thinking、无工具；密钥只从 `DEEPSEEK_API_KEY` 读取。两种完整上下文方法沿用 M2a 的公开输入与原提示词，分别处理 c001/c002/c003，共六项。
- 调用前构造并校验六个完整请求，不截断历史；同一历史的两方法公开证据一致。预检保存每项请求、提示词、输入、源码树和锁文件哈希。标签、参考修复、隐藏测试不进入方法请求。运行与评分仍分开：只有 `score` 读取参考标签。
- 本地预算政策固定 20 元总上限、最多六次请求、每项最多一次、单并发、90 秒超时与最大输出 4096 token。每次调用前按请求 UTF-8 字节数 + 8192 token 余量和最大输出预留费用，超限拒绝调用；超时不退还预留。缺失用量或接通错误停止后续调用。提供商账单金额保留未知，不能把估算记作实付。
- 真正运行时逐次保存脱敏请求、原始模型文本、响应状态、提供商用量、模型版本字段、耗时和错误。结构有效但判断错误仍由独立评分器按影响、授权、决策分别统计，不由检查器硬编码参考答案。

费率来源为 [DeepSeek 官方人民币价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)；2026-10-20 02:30 UTC 核对时 `deepseek-flash` 峰时输入缓存未命中 2 元/百万 token、输出 8 元/百万 token。本地将全部输入按缓存未命中峰时计价，未抵扣缓存或低谷折扣。**这是带余量的客户端停止规则，不是提供商账单的硬性上限。** 真实运行前应复核价格，若可用则同时设置提供商侧项目消费限制。当前没有产生任何 DeepSeek 费用。

## 实际验收证据

在本机 Windows / Python 3.12.4 的独立 `.venv` 中，完整离线测试 **52 passed**。测试覆盖请求相同证据、私有字段未泄漏、DeepSeek 请求/用量解析、HTTP 错误正文不回显、预算预留停止、20 元授权上限不可抬高、六任务注入响应后独立评分、缺失用量和接通错误停机、无密钥运行拒绝且不创建运行目录。DeepSeek HTTP 测试使用内存 `httpx.MockTransport`，六任务测试使用注入 provider；两者均不是实际 API 结果。`uv lock --check --offline` 和 `git diff --check` 通过。初次默认权限下完整 pytest 因 Windows `WinError 5` 无法访问自身临时目录；换新工作区临时目录并获准执行后 52 项通过，这是设施权限问题，不作为代码失败。

实际执行无密钥命令：

```powershell
.\.venv\Scripts\python.exe -m writscope.m2b preflight --output reports/m2b-2026-10-20
```

保存的 [preflight.json](../reports/m2b-2026-10-20/preflight.json) 显示六个请求、三个案例的每对证据哈希一致；请求字节数分别为 18956、19192、19030、19266、18997、19233。按上述本地规则，六次全部预留费用估计为 **0.524260 元**，低于 20 元授权上限。这不是实际 token 用量或账单；**模型请求数为 0，真实结果与正确率未测**。该预检目录仅含计划请求的哈希和预算计算，不含模型响应。

## 凭据可用后的操作

在将来实际运行的**同一个 PowerShell 窗口**中，在仓库根目录完成以下步骤。不要把密钥发送到聊天、写入 `.env` 或粘贴进命令行历史。先在 DeepSeek 控制台取得 API Key，并核对模型和价格；能设置项目消费硬限额时设为不超过 20 元。

```powershell
$secret = Read-Host "DeepSeek API Key" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try { $env:DEEPSEEK_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
Remove-Variable secret, ptr
.\.venv\Scripts\python.exe -m writscope.m2b preflight --output reports/local/m2b-preflight-new
.\.venv\Scripts\python.exe -m writscope.m2b run --output reports/local/m2b-real-new
Remove-Item Env:DEEPSEEK_API_KEY
```

两个输出目录必须事先不存在；每次换新名字。实际运行结束后先检查 `run.json`、逐项错误/用量与提供商账单，再独立评分：

```powershell
.\.venv\Scripts\python.exe -m writscope.m2b score --run reports/local/m2b-real-new --labels evaluation_private/case_001/labels.json --output reports/local/m2b-score-new
```

六项试跑只检验真实接口和当前输出行为，不构成正式方法排名；同一场景的三历史不是三个独立任务家族。参考标签仍为项目编制草稿。进入后续基线实测或 M3 前，还需实际核对 DeepSeek 响应结构、可用版本和费用记录；M4 正式比较另需冻结实例、提示词、重复数、质量/成本门槛和完整状态维护成本。
