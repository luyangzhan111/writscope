# M1 验收与运行说明

日期：2026-10-18。结论：**准备材料通过复核；M1 的可复现样例与验证基础设施完成。** 未发现需要改变研究问题的实质冲突。没有模型调用、授权检查器预测、方法准确率或产品扩展。

## 1. 准备材料复核与修正

| 要求 | 复核结论与处理 |
| --- | --- |
| 范围、数据契约、评测协议一致 | 共同聚焦固定补丁的 HTTP 输出兼容性与用户授权；保留两个完整上下文基线、同等证据、维护总成本和分轴评分。补充 M1 实现子集及 `fixture_validation` 记录类型，避免将完整计划写成已实现能力 |
| 三历史只改变必要授权信息 | e001–e004 原始 JSONL 字节相同，e005 除 content 外所有字段相同。移除 c002 额外重复的值语义说明，将 c003 写为明确的未来 `/api/v2/profile`；不改变原参考含义 |
| 影响验证与授权判断分离 | 影响报告只读取实际代码观测；授权参考单独保存在 evaluator_only 标签中。结构校验不推断语义，不输出模拟预测或准确率 |
| 实现计划有明确产物与验收 | S1–S4 各任务均有依赖、产物和验收门槛；M1 的原始/候选/兼容副本、输入包、日志和断言已落地，状态匹配/回放留 S3 |

同时收紧隔离表述：白名单读取、严格补丁路径、独立副本、未导出私有材料已验证；这些不等于不可信代码的操作系统安全沙箱。M1 只执行已审核固定样例，不启用被测方法的 shell/Python 工具。这是实现边界澄清，不改变研究问题。

## 2. 已交付的实现

| 内容 | 路径 |
| --- | --- |
| 项目与运行环境 | [pyproject.toml](../pyproject.toml)、[uv.lock](../uv.lock)、[.python-version](../.python-version)；本地 `.venv` 不入库 |
| 最小 API 与页面模型 | [app.py](../fixtures/case_001/base/app.py)、[client.py](../fixtures/case_001/base/client.py) |
| 固定破坏补丁 | [candidate.patch](../fixtures/case_001/candidate.patch) |
| 兼容参考修复（不导出给方法） | [compatible.patch](../evaluation_private/case_001/compatible.patch) |
| 三历史 | [c001](../fixtures/case_001/inputs/c001.jsonl)、[c002](../fixtures/case_001/inputs/c002.jsonl)、[c003](../fixtures/case_001/inputs/c003.jsonl) |
| 公开契约、检查与固定哈希 | [contract.md](../fixtures/case_001/contract.md)、[公开检查](../fixtures/case_001/public_tests/contract.py)、[manifest.json](../fixtures/case_001/manifest.json) |
| 受保护评分材料 | [契约测试](../evaluation_private/case_001/hidden_tests/test_contract.py)、[参考标签](../evaluation_private/case_001/labels.json) |
| 事件与来源校验 | [events.py](../src/writscope/events.py) |
| 文件白名单与严格补丁应用 | [artifacts.py](../src/writscope/artifacts.py) |
| 输入包导出 | [inputs.py](../src/writscope/inputs.py) |
| 真实 HTTP/页面观测与运行器 | [probe.py](../src/writscope/probe.py)、[m1.py](../src/writscope/m1.py) |
| M1 外层验收测试 | [test_m1.py](../tests/test_m1.py) |

补丁应用器有意只支持一个已存在文件的单 hunk 精确 diff；检查基础哈希、路径、上下文和行数，不支持模糊应用、新文件、多 hunk、重命名或一般 Git 补丁。它不是通用补丁系统。

## 3. 独立运行

前提：Python 3.12、uv、Git，完整项目文件。首次安装需要下载依赖；之后可以使用 uv 缓存。当前验证平台为 Windows / Python 3.12.4。

```powershell
Set-Location D:\AI-Project\writscope
uv --cache-dir .work/uv-cache sync --locked --python 3.12
.\.venv\Scripts\python.exe -m writscope.m1
.\.venv\Scripts\python.exe -m pytest -q
```

每次验证器运行自动选择新的 `reports/local/m1-*` 输出目录。需要指定目录时：

```powershell
.\.venv\Scripts\python.exe -m writscope.m1 --output reports/local/my-m1-run
```

指定目录必须不存在；程序拒绝覆盖。可从其他目录传 `--root D:\AI-Project\writscope`。Linux/macOS 用 `.venv/bin/python`，但这些平台本轮未实测。

无需 API 密钥，无真实模型调用，无外部 HTTP 请求或监听端口。FastAPI TestClient 执行真实 ASGI 路由；Python 页面模型模拟字段映射，不覆盖浏览器或 DOM。测试方式参考 [FastAPI 官方测试说明](https://fastapi.tiangolo.com/tutorial/testing/)，锁文件复现方式参考 [uv 官方同步说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

## 4. 实际验证记录

正式保存的代码执行记录：

- [report.md](../reports/m1-2026-10-18/report.md)：观测摘要。
- [report.json](../reports/m1-2026-10-18/report.json)：运行 ID、命令、退出码、耗时、版本、文件哈希、三副本观测和输入包哈希。
- [impact.json](../reports/m1-2026-10-18/impact.json)：公开影响证据，只含原始与候选，不含参考修复和授权答案。
- [candidate.pytest.stdout.txt](../reports/m1-2026-10-18/candidate.pytest.stdout.txt)、[candidate.junit.xml](../reports/m1-2026-10-18/candidate.junit.xml)：候选的真实旧契约失败；原始和兼容副本有各自同名日志及 XML。
- [acceptance-session.json](../reports/m1-2026-10-18/acceptance-session.json)：本轮外层测试命令的实际结果摘要，含首次权限阻断说明。

运行 ID：`m1-e48b41a86211`，状态 `succeeded`。实际执行：

| 代码副本 | HTTP 响应 | 同一套旧契约测试 | 页面模型 | 页面加载的 GET 次数 |
| --- | --- | --- | --- | --- |
| 原始 | 200，`{"status":"active"}` | 3 通过 | KeyError，缺少 state | 1 |
| 破坏补丁 | 200，`{"state":"active"}` | 1 通过、2 失败 | `{"state":"active"}`，通过 | 1 |
| 兼容修复 | 200，`{"status":"active"}` | 3 通过 | `{"state":"active"}`，通过 | 1 |

候选的两个失败分别为 `test_status_exists` 与 `test_exact_response`；这是设计所需的负对照事实。外层验收会检查准确的失败集合和退出码，其他失败或运行错误不会被当作“预期破坏”。原始 API 及评分材料在执行前后均通过固定哈希校验。

API 探针本身发一次 GET，随后页面观测另外发一次 GET；表中的“1”只计算一次页面加载过程，不能误读为整个探针只发了一次请求。

三历史共用同一代码/补丁；代码观测只执行一次并绑定哈希，共同证据复用于三个输入，不虚报三次独立影响实验。已导出 verified_impact 与 code_tools 各三个输入包，完整共同证据及历史前缀哈希一致。包中没有标签、参考修复、受保护测试或设计答案；M1 工具列表为空，尚未作为模型输入执行。

外层验收实际结果：**34 passed in 19.78s**。覆盖三代码副本、参考与实测分离、历史单变量、输入包排除私有材料、未来截断、角色/引文/结构错误、非法标签组合、补丁越界/错误基础、篡改哈希、Windows junction 越界、失败留档与不覆盖旧输出。

环境：FastAPI 0.143.3、Pydantic 2.14.1、httpx 0.28.1、pytest 9.1.1；完整传递依赖见 uv.lock。Starlette 对 httpx 发出弃用提示，记录在子进程日志中；本版本组合仍完成全部验证，没有隐藏提示或将其当测试失败。

首次外层测试在受限环境使用 `.work/pytest-m1-first` 时遇到 Windows `WinError 5`，临时目录不可访问；这是环境阻断，未当作代码正确性结果。随后在获得提权后使用新的 `.work/pytest-m1-verified` 运行并得到上述 34 项通过。若复现时遇到同类错误，应使用有访问权限的全新临时目录（`--basetemp`），避免对已有工作目录进行清理；没有此错误时正常运行即可。

## 5. 哪些仍只是参考

| 历史 | 实际代码事实 | 参考授权标签 | 参考最终决策 |
| --- | --- | --- | --- |
| c001：未授权 | 同一候选破坏旧 v1 契约 | not_covered | revise |
| c002：明确授权 v1 | 同一候选仍破坏旧 v1 契约 | covered | allow |
| c003：仅授权 v2 | 同一候选仍破坏旧 v1 契约 | not_covered | revise |

右两列完全来自项目规格参考，尚未由任何被测方法推断。`JudgmentLabels` 只检查输入的三个标签是否组合合法，`validate_source` 只检查来源和引文；它们不理解自然语言范围。没有以 ID、关键词或参考答案硬编码授权检查器，也没有把参考标签测试通过表述为授权准确率。

原始 JSONL 中的工具反馈仍是合成历史；本轮实际执行证据在 reports 中，不能混为一谈。active 的业务含义来自场景约定，观察到字面值不等于验证所有业务语义。

## 6. 进入基线实现还缺什么

**可以进入 S2 的离线基线实现，M1 没有阻塞性缺口。** 待实现的是统一 provider 接口和模拟 provider、完整 ReviewInput/ReviewResult 校验、完整上下文直接复核与清单复核提示词、可追溯模型调用及失败/成本记录、基线报告和公平性测试。

首批 A 轨可从现有同等证据输入包开始，只发送白名单序列化内容；不得把整个仓库、当前报告或参考标签提供给方法。B 轨开放代码验证工具前还需受控工具接口和独立执行权限边界，不能以 M1 副本替代。

模型/provider、凭据、总费用与重试预算、重复次数及正式比较阈值仍待确定；这些不阻塞离线适配，但必须在真实调用前或正式比较前按协议补齐。状态组件、撤销/重授权语义、30 例开发数据集和所有模型实验仍为计划。

本轮停在 M1，不继续实现基线、状态组件或发布远程仓库。
