# 组件级运行指南

以下命令均在仓库根目录执行；首次使用请先完成根目录 [README](../README.md) 的环境安装。完整无密钥演示和历史回放仍以该 README 为入口。下列 M1–M3b 为各阶段组件入口，脚本或人工诊断不能作为模型成绩，旧预算和预检记录不提供新付费权限。

## 运行 M1

在仓库根目录，用 Python 3.12 与 uv 创建/同步独立环境：

```powershell
uv --cache-dir .work/uv-cache sync --locked --python 3.12
.\.venv\Scripts\python.exe -m writscope.m1
.\.venv\Scripts\python.exe -m pytest -q
```

Linux/macOS 使用 `.venv/bin/python` 替代 Windows 解释器路径；初版在 Windows / Python 3.12.4 验证；当前首发代码已通过 Windows 与 Ubuntu 的 GitHub CI，macOS 尚未验证。无需运行 HTTP 服务端口或提供模型密钥。验证器使用 TestClient，在独立副本中执行真实应用代码；每次生成新的 `reports/local/m1-*` 目录，保存 JSON、Markdown、进程日志、JUnit XML 和公开输入包。显式 `--output` 只能指向不存在的新目录，避免覆盖旧记录。

实测报告见 [M1 执行记录](../reports/m1-2026-10-18/report.md)，运行细节与权限问题说明见 [M1 验收](experiments/m1-review.md)。依赖由 [uv.lock](../uv.lock) 锁定；固定样例和输入哈希由 [manifest.json](../fixtures/case_001/manifest.json) 约束。

## 运行 M2a（无需密钥）

```powershell
.\.venv\Scripts\python.exe -m writscope.m2 run --output reports/local/m2a-demo-run
.\.venv\Scripts\python.exe -m writscope.m2 score --run reports/local/m2a-demo-run --labels evaluation_private/case_001/labels.json --output reports/local/m2a-demo-score
```

两步分开运行：第一步只读公开输入和固定脚本，第二步才读参考标签。示例的两个输出目录必须事先不存在；再次运行请换新名称。省略 `--output` 会自动创建新的 `reports/local/m2a-*` 目录。默认脚本对三例给出同一判断，故意错判 c002，只用于验收管线。精选实际记录在 [M2a 报告](../reports/m2a-2026-10-20/score/summary.md)，方法提示词在 [prompts](../prompts)，脚本在 [examples/m2a](../examples/m2a)。贡献方式见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## M2b 离线预检（无需密钥、不会联网）

```powershell
.\.venv\Scripts\python.exe -m writscope.m2b preflight --output reports/local/m2b-preflight-demo
```

输出目录须事先不存在；如需重复运行请换名称。预检核对两方法 × 三历史的全部公开输入、相同证据、请求哈希、源码/锁文件哈希与本地预算预留。精选预检见 [preflight.json](../reports/m2b-2026-10-20/preflight.json)。首次真实运行已完成，但 **6/6 输出未通过引文位置校验**；单次试跑固定最多六次请求，预算上限 20 元，费用预留只是本地估算，不是提供商账单硬上限。

首次 M2b 实际接通与单独评分已执行；原始引文位置失败见 [真实接通记录](experiments/m2b-real-review.md)。新版 `source_quote_v1` 让模型提交唯一原文、由运行器派生位置；[新一次真实开发运行](experiments/m2b-quote-real-review.md)得到 5/6 有效输出，单独保存，不改写首次负结果。后续 S3 已完成作者审核开发验收，见 [v8真实记录](experiments/s3-development-v8-real-review.md)；独立审核和首轮 30 实例 S4 评测尚未完成。GitHub 开发版已公开并采用 MIT；尚未发布 PyPI 包。

## 运行 M3a（无模型调用）

```powershell
.\.venv\Scripts\python.exe -m writscope.m3a run --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --proposals evaluation_private/case_001/m3a_manual_proposals/c002.jsonl --output reports/local/m3a-demo-c002
.\.venv\Scripts\python.exe -m writscope.m3a verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c002 --run reports/local/m3a-demo-c002
```

示例输出目录必须事先不存在。私有目录中的提案是**人工诊断输入**，仅用于验证状态内核；不会发送给两种完整上下文方法，也不能当作增量方法预测。实际三历史运行与边界见 [M3a 验收](experiments/m3a-review.md)。

## 运行 M3b（脚本响应，不调用真实模型）

```powershell
.\.venv\Scripts\python.exe -m writscope.m3b run --bundle reports/m1-2026-10-18/inputs/verified_impact/c001 --script examples/m3b/c001_script.json --output reports/local/m3b-demo-c001
.\.venv\Scripts\python.exe -m writscope.m3b verify --bundle reports/m1-2026-10-18/inputs/verified_impact/c001 --run reports/local/m3b-demo-c001
```

输出目录必须事先不存在。脚本按固定顺序返回响应，不读取请求或参考标签；本例只验证逐事件接线与回放，不能当作模型结果。详见 [M3b 验收](experiments/m3b-review.md)。
