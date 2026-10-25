# 长理智慧能源管理系统 / GridWise Campus

面向高校的通用能源分析与优化项目。现有离线原型支持合成数据的纯电费调度、无储能基线、CSV 质量检查及本机交互式可视化；无学校数据接口、实时控制或认证声明。 / The offline prototype includes cost-only dispatch, a no-storage baseline, CSV quality checks and a local interactive desktop view. It has no campus connection or control claim.

## 已实现 / Implemented

- JSON 数据及单位、时间间隔校验 / JSON input validation, units and time spacing.
- 多时段光伏、购电、储能混合整数优化 / Multi-period PV, grid and battery mixed-integer optimization.
- 同输入无储能基线、独立结果校验 / Same-input no-storage baseline and independent result validation.
- JSON、CSV 和 SVG 导出 / JSON, CSV and SVG export.
- 只读 CSV 导入、缺失值与时间完整性报告、图表数据准备 / Read-only CSV quality reporting and chart-data preparation.
- 本机双语交互原型：文件选择、质量拦截、优化/基线对比与五张图 / Local bilingual desktop prototype with file selection, quality gating, comparison and five charts.

## 安装与运行 / Install and run

Python 3.12+。建议使用独立虚拟环境；以下命令不会连接学校系统。 / Python 3.12+ and a dedicated virtual environment are recommended. These commands do not connect to campus systems.

```powershell
py -3.12 -m venv [LOCAL_ENV_ROOT]\gridwise-campus-py312
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pip install -e '.[test]'
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.cli examples\synthetic-day.json outputs\sample
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.data examples\synthetic-day.csv --step-hours 1
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.desktop
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe tools\audit_exports.py examples\synthetic-day.json outputs\sample\optimized.csv outputs\sample\baseline.csv outputs\sample\summary.json
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pytest -q
```

打开 `outputs/sample/grid-purchase.svg` 查看购电功率比较。输出目录已被 Git 忽略。 / Open that SVG to view grid purchase. The output directory is Git-ignored.

CLI 默认拒绝覆盖输出目录中已有的四类导出文件，并以退出码 2 报告冲突。确认需要替换时显式追加 `--overwrite`。新导出先在输出目录内暂存；一般写入失败时回滚本次写入。四文件对并发读者不是原子快照，进程崩溃或不协作的外部写入仍需人工核对；请为重要运行使用独立新目录。 / The CLI refuses existing export targets by default. Add `--overwrite` only for deliberate replacement. Exports are staged before publication and ordinary write failures are rolled back; the four files are not an atomic snapshot for concurrent readers or process crashes. Use a fresh directory for important runs.

v0.3.6 的故障注入确认逐文件发布期间可出现新旧混合结果；失败回滚及暂存清理异常现在会显式报告，恢复副本可能保留供人工核对。推荐每次使用唯一新目录，并在 CLI 成功退出且[独立审计](docs/export-consistency-design.md)通过后读取结果。Tk 间歇初始化/焦点异常可用 `python tools/diagnose_tk_runtime.py --mode toplevel --count 30 --repeat 5 --output tk-evidence.json` 进行有界独立进程诊断；短时通过不等于根因已确定。 / Fault injection confirmed mixed versions during sequential publication. Rollback and staging-cleanup failures are now explicit; recovery copies can remain for manual review. Prefer a unique new directory and consume it only after a successful CLI exit and independent audit. The bounded Tk diagnostic captures runtime paths and focus traversal, but successful short probes do not establish a root cause.

v0.3.7 可选的[已校验运行目录](docs/run-artifact-contract.md)入口：`python -m gridwise.run_artifacts publish examples/synthetic-day.json outputs/runs`，随后对输出的 `run-...` 目录执行 `python -m gridwise.run_artifacts verify RUN_DIR`。发布前执行独立审计；读取端检查完成标记、清单及全部文件哈希。旧 CLI 用法保持不变。 / The optional verified-run interface publishes a unique run after independent audit and reads it only after marker, manifest and file checks. The legacy CLI remains compatible.

桌面原型默认载入合成 CSV；也可主动选择本地 CSV 并填写小时步长。先点“检查数据”，通过后再点“运行对比”。使用合成场景的设备参数；每次交互式求解最多 96 时段，运行文件仅存于系统临时目录。检查与调度在后台执行，关闭窗口或更换输入会取消在途运行；求解和独立审计各有 120 秒上限。需要有可用的图形桌面环境。详情见[界面设计与选型](docs/v0.3-desktop-design.md)。 / The desktop uses synthetic equipment assumptions, keeps Tk responsive during checks and dispatch, cancels in-flight work on close or input change, and bounds solver and audit subprocesses to 120 seconds each. Interactive runs are capped at 96 periods.

v0.3.2 为小窗口和高 DPI 字体增加页面纵向滚动、按可用宽度排列的指标卡、短页签与保留时区的两行时间标签。v0.3.3 在创建 Tk 窗口前检查 Windows 进程的 DPI 感知状态；仅在仍为 unaware 时启用每显示器感知，不覆盖已有设置。只读日志可通过 Tab 到达，在操作控件上按 Page Up/Down 可滚动页面。忙碌条只表示任务正在运行。真实跨显示器 DPI 行为仍需有不同 DPI 显示器的环境验收。 / The desktop scrolls vertically, reflows metric cards, shortens chart tabs, and retains time zones on the axis. v0.3.3 enables per-monitor DPI awareness before Tk window creation only when the process is still unaware. The read-only log is reachable by Tab, and Page Up/Down scrolls the page from action controls. Native cross-monitor behavior remains unverified.

v0.3.4 增加可键盘操作的“取消 / Cancel”按钮及 Esc 快捷键。取消时保留运行中状态，待后台任务和子进程清理后恢复检查/运行按钮；关闭窗口也会等待已启动任务清理后再退出。取消结果不会显示为成功。`pyproject.toml` 的包版本仍是 `0.1.0`，v0.3.x 是内部阶段编号，不代表已发布的软件版本。项目尚无正式许可证，不能据此公开发布。 / v0.3.4 adds keyboard cancellation and waits for active worker cleanup when closing. The package metadata remains at version 0.1.0; v0.3.x labels internal development stages, not a released product. Licensing remains undecided.

审计命令仅使用 Python 标准库读取原始输入和导出文件；退出码 0 为通过，2 为数据或计算校验失败。 / The audit command uses only the standard library to inspect the source and exports; exit 0 means pass, 2 means an audit failure.

## 范围与路线 / Scope and roadmap

后续阶段可评估多场景分析与更丰富的图表交互；负荷预测、配电网模型、受控接口与部署需另行设计和验证。所有能力须按测试和真实数据授权分别验收。 / Later stages may add richer scenario analysis and chart interaction; forecasting, network models, controlled interfaces and deployment require separate design and validation.

项目许可证尚未确定；请勿据此假定代码已获得开放源代码许可。 / The project license is undecided; do not assume this code has an open-source license yet.

详细契约见 `docs/`。 / See `docs/` for detailed contracts.
