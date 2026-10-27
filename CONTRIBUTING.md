# 贡献说明

当前仓库是研究开发版，S3 已满足作者审核开发验收，独立研究审核与 S4 尚未完成。先读 [README](README.md)、[数据契约](specs/data-contracts.md)、[评测协议](docs/evaluation-protocol.md) 和 [协作规则](AGENTS.md)。不要把脚本输出或参考标签当作模型效果。

## 本地复现

使用 Python 3.12 与 uv，在仓库根目录执行：

```powershell
uv --cache-dir .work/uv-cache sync --locked --python 3.12
.\.venv\Scripts\python.exe -m scripts.public_release restore
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .work/pytest-local-new
.\.venv\Scripts\python.exe -m writscope.m2 run
.\.venv\Scripts\python.exe -m writscope.m2b preflight
```

Linux/macOS 将解释器路径换成 `.venv/bin/python`；pytest 的 `--basetemp` 每次用新的工作区内路径。M1 可用 `.venv` 解释器运行 `-m writscope.m1`。M2a 第二步评分命令见 [README](README.md)，必须在运行完成之后执行。上述 M2b 命令只做无密钥预检，不调用模型；真实接通步骤与边界见 [M2b 离线验收](docs/m2b-offline-review.md)。CI 配置是无密钥测试，远程运行状态以实际工作流为准。

## 添加案例或方法

案例需记录固定代码、候选、公开契约、事件原文、决策时点、来源与作用范围；先跑真实代码影响验证，再导出公开输入。私有标签、修复参考及隐藏测试不得放入方法请求。成对变体只改变决定授权的信息，记录共同文件哈希。方法间使用同一原始证据、基础模型配置与工具权限；新的状态维护/回查/重试都需计入总成本。

提交更改时更新相关契约、测试和运行说明；保留失败记录，标明离线 mock 与真实模型。不要提交密钥、虚拟环境或未经检查的本地日志。采用 [MIT](LICENSE)；历史证据中原机器路径为保留哈希而明确保留，新增教程请使用仓库相对路径。

`reports/public-v0.1/evidence.zip` 是精选原始证据，不要直接编辑；清单记录每个文件的原始 SHA-256。发现历史故障时另加诊断或明确版本，不回写原响应、授权或评分。新的付费实验和材料外发需要对应新计划的授权；公开开发版和历史收据均不提供该授权。
