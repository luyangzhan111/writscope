# R5 离线交付与恢复操作

本页说明恢复方式；实际通过状态以验收记录、交付清单及包旁的恢复收据为准。交付仅用于离线开发核验，不授权真实发送、费用、外发或 GitHub 更新。

## 交付内容与入口

`reports/s4-r5-offline-v1/delivery.zip` 是明确选取的源码、依赖锁文件、精选历史证据及本轮场景工件；`manifest.json` 绑定归档和逐文件哈希。它包含评测器侧参考，**整个交付包不能作为方法输入包**。运行器仍只读取公开包白名单。

当前职责分工：

| 部分 | 维护入口 |
| --- | --- |
| 任务执行、单账本、合成授权与一次启动 | `scripts/s4_r3.py` |
| 响应驱动独立复验与评测器 | `scripts/s4_r4_replay.py`、`scripts/s4_r4_evaluator.py` |
| 全批计划与故障注入 | `scripts/s4_r5_acceptance.py` |
| 字节未变时省略重复解析的发送前检查 | `scripts/s4_r5_integrity_guard.py` |
| 交付归档、恢复与重算 | `scripts/s4_r5_delivery.py` |
| Windows 本地事件循环的恢复兼容补充 | `scripts/s4_r5_recovery.py` |
| 特定隔离故障非零退出的只读核验 | `scripts/s4_r5_process_audit.py` |
| 旧成功/失败只读兼容回放和公开演示 | `scripts/public_release.py` |

旧脚本与保存证据保留，不迁移评分或覆盖历史。所有故障测试均为固定合成控制；`synthetic=True` 的 claim 不是真实用户批准。真实入口始终关闭。

本轮隔离故障在下一轨加载公开包时以退出码 1 停止，未生成整轮汇总；原启动控制器因预期退出码 0 而停止。补充的只读核验器仅接受这一个有确切白名单错误、故障注入标记、单次已完成模拟收据及完整计划的现场；不会接受其他非零退出，也不补写汇总、重启任务或读参考。对应单项定向测试同时验证无关异常仍拒绝。后续未启动场景按原冻结候选逐项启动，不能自动续跑故障轨迹。

复查该现场时为收据和派生评测分别使用新的输出文件名，两者均独占创建：

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_r5_process_audit --suite reports/local/s4-r5-final-20261102 --output isolation-exit-audit-new.json --evaluation-output process-audit-evaluation-new.json
```

包中已经含有该现场的核验输出；常规恢复命令会重新验证隔离故障拒绝和参考不读取，不需重复运行上述补充核验。原始进程记录、计划及账本始终保留。

## 恢复步骤

需要 Python 3.12 与 `uv.lock` 对应依赖。包中不包含 `.venv`、`.env*`、Git 内部或凭据。此次恢复使用已有本地依赖；没有联网安装依赖，也不声称跨操作系统或全新依赖环境已验收。

在原项目目录中，选择一个尚不存在的恢复目录：

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_r5_delivery restore --package reports/s4-r5-offline-v1 --destination .work/r5-restored-example
```

归档路径、大小、重复项、链接和全部字节哈希会先检查，任何异常在写入前拒绝；已存在的目标目录也拒绝。无需 Git 或被忽略目录中的其他材料。

Windows 的原恢复编排将本机事件循环 socket 也拦截了，首次恢复因此在全部 16 份报告重算后失败。原工件保留；恢复检查改用 [版本化兼容补充](../specs/s4-r5-recovery-addendum-v1.md)，只在任务执行前初始化本机事件循环，演示/回放协程仍禁止外部连接。

先从包旁补充材料核对并新增恢复脚本，不覆盖已有文件：

```powershell
$r5Addendum = Get-Content -LiteralPath reports/s4-r5-offline-v1/recovery-addendum.json -Raw | ConvertFrom-Json
$r5Source = 'reports/s4-r5-offline-v1/addendum/scripts/s4_r5_recovery.py'
$r5Expected = $r5Addendum.files_sha256.'scripts/s4_r5_recovery.py'
if ((Get-FileHash -LiteralPath $r5Source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $r5Expected) { throw '补充脚本哈希不一致' }
$r5Target = '.work/r5-restored-example/scripts/s4_r5_recovery.py'
if (Test-Path -LiteralPath $r5Target) { throw '目标文件已存在，不能覆盖' }
Copy-Item -LiteralPath $r5Source -Destination $r5Target
```

随后明确从恢复目录导入源码。以下保留原项目的依赖解释器，但不使用原项目的源码。在允许本机通信的环境执行；不需联网安装或配置密钥：

```powershell
$r5Python = (Resolve-Path .venv/Scripts/python.exe).Path
Push-Location .work/r5-restored-example
& $r5Python -I -c "import sys,runpy; sys.path[:0]=['.','src']; runpy.run_module('scripts.s4_r5_recovery',run_name='__main__')" --suite reports/local/s4-r5-final-20261102 --output recovery-check-new.json
Pop-Location
```

检查会验证源码导入位置、逐场景重算报告、运行无密钥演示、只读回放旧 S3 v8 成功/v7 失败记录，并确认真实入口没有触碰传入的凭据或发送回调。输出文件采用独占创建，不能重复覆盖；再次检查须使用另一新恢复目录。此次收尾显式采用 `--reuse-recomputed` 复用首次失败前已经完成的 16 份重算报告，逐份比较并绑定其哈希；没有重跑模型或将原失败改写成成功。包旁恢复收据明确记录这一差别。

最后核对包内原文件未被上述操作改写：

```powershell
.\.venv\Scripts\python.exe -m scripts.s4_r5_delivery verify-restored --package reports/s4-r5-offline-v1 --destination .work/r5-restored-example
```

恢复时不重跑 270 条轨迹或读取真实密钥；它只验证保存响应、状态和账本。恢复后生成的公开演示/回放报告单独保存，不写回历史记录。旧 claim 中的绝对路径作为历史原文保留，旧回放的路径兼容只在内存中生效。

## 解释结果

完整合成轨迹可接评测参考；中断未决只验证已落盘前缀；哈希、隔离或账本损坏必须被拒绝。故障场景达到预期拒绝/停止行为也属于工程检查通过，不能改称任务成功或模型判断正确。所有场景保留 270 条计划分母；被拒绝的记录不产生指标。

token、真实账单以及未测量的本地准备、序列化、完整性复核和共享证据开销仍 unknown；操作耗时汇总不是整个实验的完整用时。参考保持 provisional，独立审核为 `not_performed_skipped_by_user`。R5 验收只能支持进入 R6 离线候选冻结准备。
