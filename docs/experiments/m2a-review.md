# M2a 验收：两种完整上下文基线的无密钥流程

日期：2026-10-20。范围：现有 c001/c002/c003 三种授权历史、实验 A 的 `verified_impact` 公开证据、最终决策点。**这是离线脚本化流程验证，不是模型实验或方法性能比较。** M1 原始验证报告保持不变。

## 已完成与可复现路径

| 计划门 | 已落地产物与验收事实 |
| --- | --- |
| P0 M1 基点 | 本地 `main` 的 M1 基点提交 `44dd773`；无远程。M1 精选报告和输入包未覆盖。 |
| P1 输入/输出契约 | `contracts.py` 读取固定 M1 公开包，校验版本、白名单路径、文件/快照/补丁哈希、决策时点、影响证据与引用；输出拒绝重复 JSON 键、非法三轴组合、伪造引文、无定义来源和越界对象。语义正确性不由结构校验证明。 |
| P2 调用边界 | 只有 `scripted-mock-v1`；显式脚本步骤、超时、最多尝试数和总尝试预算。逐次保存请求、原始响应、状态、合成用量与耗时；失败和重试不丢弃。无真实模型后端、联网请求或密钥。 |
| P3 两种基线 | `full_direct` 与 `full_checklist` 各有版本化指令，使用同一公开输入构造和配置。每例两请求的 `evidence_sha256` 一致，包含实际代码、补丁、契约、全部截止前事件和验证影响内容；清单输出必须填写重建及范围检查。 |
| P4 独立评分 | `run` 不读标签；`score` 只在运行完成后读取项目参考标签，验证保存工件哈希，逐任务统计影响、授权、决策、三轴全对、误放行、误拦截、诊断配对和失败分母。相同记录可重算同一指标；更换标签只改变评分，不改变请求。 |
| P5 复现交付 | README、CONTRIBUTING、无密钥 CI 配置、实际运行和评分记录齐备。本机 Python 3.12.4 / Windows 完整测试 41 项通过；Linux 与远程 CI **尚未运行**。 |

在仓库根目录先按 [README](../../README.md) 用 `uv sync --locked --python 3.12` 建立独立环境，然后执行：

```powershell
.\.venv\Scripts\python.exe -m writscope.m2 run --output reports/local/m2a-my-run
.\.venv\Scripts\python.exe -m writscope.m2 score --run reports/local/m2a-my-run --labels evaluation_private/case_001/labels.json --output reports/local/m2a-my-score
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .work/pytest-my-new-run
```

输出目录必须事先不存在；每次换新名称，不覆盖旧记录。Linux/macOS 用 `.venv/bin/python`。本机 Windows 上默认系统临时 pytest 目录曾因 ACL 返回 `WinError 5`，改用工作区内的新 `--basetemp` 并在获准的执行环境下测试；这是测试设施权限问题，不是测试断言失败。

## 实际观察

精选 [运行记录](../../reports/m2a-2026-10-20/run/run.json) ID `m2a-1c636d8c30dc`；[评分报告](../../reports/m2a-2026-10-20/score/summary.md) 与 [机器可读评分](../../reports/m2a-2026-10-20/score/summary.json) 可复核。运行工件集合 SHA-256 为 `826831209989d54955f9a0045bf56900c1ea3af41f7e0c869a6ab4a009cfe059`，代码树与锁文件哈希保存在 manifest。两方法在每个案例中拿到相同证据哈希，提示词哈希不同。

默认脚本不读取请求、case ID 或参考标签，三次都输出 `breaking / not_covered / revise`。六个任务均通过结构校验；对两方法分别得到影响 3/3、授权 2/3、决策 2/3，c002 被错判为未授权，误拦截 1/1。这个数字**只证明故意错误的静态响应被正确计分**，不能代表任一基线的模型准确率。三历史同属一个场景，诊断配对重叠。

实际模型调用、token 和账单均为 0。六次脚本化尝试的合成 token 已知小计为输入 440、输出 230，各有 2 次未知；合成费用 6 次均未知，完整总额为 null，不能解释为 0。专项测试另外覆盖 provider 错误后重试、超时后重试、无效 JSON、预算耗尽、输入哈希错误、输出来源错误与失败任务计入分母。变更保存结果文件会被评分器哈希校验拒绝。`uv --cache-dir .work/uv-cache lock --check --offline` 通过；完整本地测试 `41 passed`，其中 M1 34 项、M2a 7 项。

## 仍然只是参考或计划

`evaluation_private/case_001/labels.json` 为项目编制的草稿参考，未做独立标注；兼容修复和隐藏测试是 M1 参照材料，不进入 M2a 方法请求。结构校验可证明引用存在、角色可信、引文位置正确，不能证明模型解释的授权范围正确。默认脚本本身不执行语义复核。

M2b 真实接通仍需用户决定 provider/模型及版本、凭据、实际费用上限、并发/超时/重试与停止规则，并明确授权调用。M3 增量授权状态、B 轨工具执行安全边界、30 例开发评测、独立验证家族与对外发布均未实现或运行。已有 CI 文件只是配置，尚无远程工作流成功记录。
