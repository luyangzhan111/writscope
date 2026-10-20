# 贡献说明

当前仓库是本地研究原型。先读 [项目交接书](docs/project-brief.md)、[数据契约](specs/data-contracts.md)、[评测协议](docs/evaluation-protocol.md) 和 [协作规则](AGENTS.md)。不要把三例脚本输出或参考标签当作模型效果。

## 本地复现

使用 Python 3.12 与 uv，在仓库根目录执行：

```powershell
uv --cache-dir .work/uv-cache sync --locked --python 3.12
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp .work/pytest-local-new
.\.venv\Scripts\python.exe -m writscope.m2 run
```

Linux/macOS 将解释器路径换成 `.venv/bin/python`；pytest 的 `--basetemp` 每次用新的工作区内路径。M1 可用 `.venv` 解释器运行 `-m writscope.m1`。M2a 第二步评分命令见 [README](README.md)，必须在运行完成之后执行。无需模型凭据。CI 配置是无密钥测试，远程运行状态以实际工作流为准。

## 添加案例或方法

案例需记录固定代码、候选、公开契约、事件原文、决策时点、来源与作用范围；先跑真实代码影响验证，再导出公开输入。私有标签、修复参考及隐藏测试不得放入方法请求。成对变体只改变决定授权的信息，记录共同文件哈希。方法间使用同一原始证据、基础模型配置与工具权限；新的状态维护/回查/重试都需计入总成本。

提交更改时更新相关契约、测试和运行说明；保留失败记录，标明离线 mock 与真实模型。不要提交密钥、虚拟环境、机器专属路径或未经脱敏的本地日志。开源许可证及远程发布尚未确定。
