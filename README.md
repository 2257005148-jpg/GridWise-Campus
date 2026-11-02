# 长理智慧能源管理系统 / GridWise Campus

面向高校的通用能源分析与优化项目。现有离线原型支持合成数据的纯电费调度、显式峰值上限调度、无储能基线、CSV 质量检查及本机交互式可视化；无学校数据接口、实时控制或认证声明。 / The offline prototype includes cost-only and explicit peak-cap dispatch, a no-storage baseline, CSV quality checks and a local interactive desktop view. It has no campus connection or control claim.

## 已实现 / Implemented

- JSON 数据及单位、时间间隔校验 / JSON input validation, units and time spacing.
- 多时段光伏、购电、储能混合整数优化 / Multi-period PV, grid and battery mixed-integer optimization.
- 同输入无储能基线、独立结果校验 / Same-input no-storage baseline and independent result validation.
- JSON、CSV 和 SVG 导出 / JSON, CSV and SVG export.
- 只读 CSV 导入、缺失值与时间完整性报告、图表数据准备 / Read-only CSV quality reporting and chart-data preparation.
- 本机双语交互原型：文件选择、质量拦截、优化/基线对比与五张图 / Local bilingual desktop prototype with file selection, quality gating, comparison and five charts.
- 桌面端显式选择纯电费或峰值上限模式，记录并复核运行策略 / Desktop selection of cost-only or peak-cap policy with traceable verified metadata.
- 桌面端可校验并实际使用自定义电网与电池参数 / Validated custom grid and battery parameters used by the desktop solver.
- 本地场景预设可创建、加载、更新、删除，并以严格校验的单预设 JSON 导入或导出 / Local scenario presets support CRUD plus strictly validated single-preset JSON import and export.
- 已验证单次运行与公平 A/B 对比可导出自包含 HTML 报告 / Verified runs and fair A/B comparisons can be exported as self-contained HTML reports.

## 安装与运行 / Install and run

Python 3.12+。建议使用独立虚拟环境；以下命令不会连接学校系统。 / Python 3.12+ and a dedicated virtual environment are recommended. These commands do not connect to campus systems.

```powershell
py -3.12 -m venv [LOCAL_ENV_ROOT]\gridwise-campus-py312
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pip install -e '.[test]'
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.cli examples\synthetic-day.json outputs\sample
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.data examples\synthetic-day.csv --step-hours 1
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.desktop
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe tools\smoke_desktop_verified.py
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe tools\audit_exports.py examples\synthetic-day.json outputs\sample\optimized.csv outputs\sample\baseline.csv outputs\sample\summary.json
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pytest -q
```

打开 `outputs/sample/grid-purchase.svg` 查看购电功率比较。输出目录已被 Git 忽略。 / Open that SVG to view grid purchase. The output directory is Git-ignored.

CLI 默认拒绝覆盖输出目录中已有的四类导出文件，并以退出码 2 报告冲突。确认需要替换时显式追加 `--overwrite`。新导出先在输出目录内暂存；一般写入失败时回滚本次写入。四文件对并发读者不是原子快照，进程崩溃或不协作的外部写入仍需人工核对；请为重要运行使用独立新目录。 / The CLI refuses existing export targets by default. Add `--overwrite` only for deliberate replacement. Exports are staged before publication and ordinary write failures are rolled back; the four files are not an atomic snapshot for concurrent readers or process crashes. Use a fresh directory for important runs.

v0.3.6 的故障注入确认逐文件发布期间可出现新旧混合结果；失败回滚及暂存清理异常现在会显式报告，恢复副本可能保留供人工核对。推荐每次使用唯一新目录，并在 CLI 成功退出且[独立审计](docs/export-consistency-design.md)通过后读取结果。Tk 间歇初始化/焦点异常可用 `python tools/diagnose_tk_runtime.py --mode toplevel --count 30 --repeat 5 --output tk-evidence.json` 进行有界独立进程诊断；短时通过不等于根因已确定。 / Fault injection confirmed mixed versions during sequential publication. Rollback and staging-cleanup failures are now explicit; recovery copies can remain for manual review. Prefer a unique new directory and consume it only after a successful CLI exit and independent audit. The bounded Tk diagnostic captures runtime paths and focus traversal, but successful short probes do not establish a root cause.

v0.3.7 可选的[已校验运行目录](docs/run-artifact-contract.md)入口：`python -m gridwise.run_artifacts publish examples/synthetic-day.json outputs/runs`，随后对输出的 `run-...` 目录执行 `python -m gridwise.run_artifacts verify RUN_DIR`。发布前执行独立审计；读取端检查完成标记、清单及全部文件哈希。旧 CLI 用法保持不变。 / The optional verified-run interface publishes a unique run after independent audit and reads it only after marker, manifest and file checks. The legacy CLI remains compatible.

v0.3.11 可显式执行 `python -m gridwise.cli examples/synthetic-day.json outputs/peak55 --peak-cap-kw 55`，或在 `gridwise.run_artifacts publish` 命令后追加同一参数。只约束储能优化方案的逐时购电功率；目标仍为最小电费，基线不受该上限约束。默认不传参数时保持原纯电费方案。分别查看 CNY 电费、kWh 购电量和 kW 峰值；一种指标改善不代表其他指标改善。/ The opt-in cap constrains storage-run import per period while retaining cost minimization. The no-storage baseline remains unconstrained by this additional cap.

桌面原型默认载入合成 CSV；也可主动选择本地 CSV 并填写小时步长。先点“检查数据”，通过后再点“运行对比”。使用合成场景的设备参数；每次交互式求解最多 96 时段。v0.3.8 起，运行结果发布到界面指定的本地运行根目录（默认系统临时目录下的 `gridwise-campus-runs`），独立审计、完成标记和清单校验通过后才显示。可刷新运行历史，区分有效、未完成和损坏目录；选择历史运行时重新校验文件，并要求当前已检查 CSV 与结果逐时一致。失败目录不会自动删除。检查与调度在后台执行，关闭窗口或更换输入会取消在途运行；求解和独立审计各有 120 秒上限。需要有可用的图形桌面环境。详情见[界面设计与选型](docs/v0.3-desktop-design.md)及[运行目录契约](docs/run-artifact-contract.md)。 / The desktop checks CSV quality, then publishes each comparison into a unique local run directory. It displays charts only after independent audit and verified reading. Refreshing history classifies valid, incomplete and damaged runs; loading one rechecks its files and matches its series against the currently checked CSV. Failed directories are retained for inspection. Solver and auditor each have a 120-second limit.

v0.3.9 可在界面选择**已存在**的受控本地目录，再点击“保存目录 / Save root”；重启后会恢复该路径并刷新历史。默认临时目录不变，保存新路径不会搬迁旧运行。设置不可用时显示原因并回退到临时目录。保留、备份和手动清理说明见[运行历史保存](docs/run-history-storage.md)。/ Select an existing controlled local directory and explicitly save it to restore that root after restart. The temporary default remains; saving does not move old runs. See the storage guide for retention and backup.

v0.3.12 桌面默认仍为“纯电费优化”。选择“峰值上限优化”后输入有限、非负且不超过场景 `grid_max_kw` 的 kW 数值；只有独立审计、格式 2 清单及安全读取通过才显示结果。策略栏显示设置上限、实际峰值和达标状态。只有运行历史中存在同场景指纹、同求解器、同模型源码摘要且重新校验通过的纯电费结果时，才显示相对电费差额；否则明确显示无可靠对照。/ The desktop now exposes the opt-in cap without changing the default. Cost delta is shown only against a verified, same-input and same-model cost-only run.

v0.3.13 将 [`examples/synthetic-day.csv`](examples/synthetic-day.csv) 作为可复制的自定义输入模板。合法的 `Z`、`+0000` 和 `+00:00` 时间戳在内存中规范化为带冒号的偏移形式，原 CSV 字节不修改；质量通过后显示时间范围、功率范围、价格范围及按 `Σ功率×step_hours` 计算的负荷/光伏可用电量。CSV 只替换时间序列、负荷、光伏和电价，电网及电池/SOC 参数仍来自合成设备场景；桌面最多运行 96 时段。详见[自定义 CSV 导入](docs/custom-csv-import.md)。/ The existing sample CSV is the copyable template. Valid offset spellings are normalized in memory, while the source bytes remain untouched. Input summaries do not change the fixed synthetic equipment assumptions or the 96-period desktop limit.

v0.3.14 在桌面增加独立的[设备参数配置](docs/custom-device-parameters.md)：默认值仍取自合成模板，用户可修改电网购电上限、电池容量、充放电功率、以 kWh 表示的初末 SOC 及两种效率。参数改变会立即清除旧结果；实际值写入同次运行的 `scenario.json`，并由现有独立审计、清单哈希和安全读取复核。历史运行被选择后恢复其已校验设备参数。/ The desktop can now override validated grid and battery assumptions. Changes invalidate stale results, while the exact values are carried in the verified scenario and restored with a selected historical run.

v0.3.24 在既有预设区域增加“导入 / Import”和“导出 / Export”。交换文件只含名称、八项设备参数和优化策略，不含本地 UUID、CSV、运行结果或路径；导入会完整校验并生成新 ID，重名时只能取消或另存为新名称，不会自动加载参数或启动求解。导出按稳定 ID 重新读取已保存预设，目标已存在或文件系统不支持所需硬链接操作时失败关闭。格式、操作和限制见[场景预设交换](docs/scenario-preset-exchange.md)。/ Preset exchange uses one versioned UTF-8 JSON document without local IDs or run data. Import validates before creating a fresh local ID; export rereads the saved preset and atomically refuses overwrite. See the preset exchange guide for the exact contract and limitations.

v0.3.25 将桌面工作台划分为“配置与运行 / Configure & Run”和“历史与报告 / History & Reports”两个独立滚动页签。预设操作按“加载、保存为、更新 / 导入、导出、删除”两行排列；历史表格保留纵向滚动并增加水平滚动条；运行状态独占按钮下方整行，长文本可换行。页签切换不触发配置失效、generation 变化或业务回调。Windows 100% 缩放下已在 900×650 内容区完成可见验收；原生 150%/200% 显示缩放仍未执行。/ The desktop now separates configuration/run work from history/report work. Preset actions use a responsive two-row layout, the history table exposes both scrollbars, and run status has a full wrapping row. Visible acceptance covered a 900×650 client area at native 100% scaling; native 150%/200% display scaling remains untested.

v0.3.15 可从同一运行根目录选择两个 `VALID` 历史运行进行[公平对比](docs/run-comparison.md)。控制层重新安全读取两个运行并用当前已检查 CSV 重算指标；只有场景原始字节摘要、求解器、模型源码摘要、时间序列、时间步和设备参数均匹配时，才显示电费、购电量和购电峰值及 `B − A`。优化模式和峰值上限是允许不同的策略变量。完整性通过、公平条件通过和指标变化是三种独立结论。/ Two verified historical runs can be compared only after safe rereading, current-input alignment and fair-context checks. Strategy may differ, while source data, solver, model source and equipment must match.

v0.3.20 可在运行历史区导出[自包含 HTML 报告](docs/report-export.md)。单次报告会重新安全读取所选运行；A/B 报告会重新执行公平比较。报告只使用已校验控制层给出的指标、设备参数和时序，内联 SVG 展示购电功率、累计电费及电池 SOC，不在导出层重算业务指标。默认拒绝覆盖已有文件，并通过目标目录中的私有临时文件完整写入后发布。/ The desktop exports self-contained UTF-8 HTML only after rereading the selected verified run or rerunning the fair A/B comparison. Inline SVG charts show verified grid power, cumulative cost and battery SOC without external resources. Existing files are refused by default, and report publication uses a complete private temporary in the target directory.

v0.3.18 将现有[结构化运行诊断](docs/run-diagnostics.md)显示在紧凑双语面板中。失败时可查看类别、阶段、摘要、可信证据、异常链和人工检查建议，并复制完整详情；`UNKNOWN` 明确表示证据不足。新任务、输入、步长、设备、策略或运行根目录变化会清除旧诊断，取消不会伪装为求解失败。诊断仅展示证据，不会修改参数或放宽约束。/ The desktop now presents existing structured diagnostics in a compact, expandable and copyable panel. Diagnostics remain display-only and are invalidated with their run context.

v0.3.2 为小窗口和高 DPI 字体增加页面纵向滚动、按可用宽度排列的指标卡、短页签与保留时区的两行时间标签。v0.3.3 在创建 Tk 窗口前检查 Windows 进程的 DPI 感知状态；仅在仍为 unaware 时启用每显示器感知，不覆盖已有设置。只读日志可通过 Tab 到达，在操作控件上按 Page Up/Down 可滚动页面。忙碌条只表示任务正在运行。真实跨显示器 DPI 行为仍需有不同 DPI 显示器的环境验收。 / The desktop scrolls vertically, reflows metric cards, shortens chart tabs, and retains time zones on the axis. v0.3.3 enables per-monitor DPI awareness before Tk window creation only when the process is still unaware. The read-only log is reachable by Tab, and Page Up/Down scrolls the page from action controls. Native cross-monitor behavior remains unverified.

v0.3.4 增加可键盘操作的“取消 / Cancel”按钮及 Esc 快捷键。取消时保留运行中状态，待后台任务和子进程清理后恢复检查/运行按钮；关闭窗口也会等待已启动任务清理后再退出。取消结果不会显示为成功。`pyproject.toml` 的包版本仍是 `0.1.0`，v0.3.x 是内部阶段编号，不代表已发布的软件版本。项目尚无正式许可证，不能据此公开发布。 / v0.3.4 adds keyboard cancellation and waits for active worker cleanup when closing. The package metadata remains at version 0.1.0; v0.3.x labels internal development stages, not a released product. Licensing remains undecided.

审计命令仅使用 Python 标准库读取原始输入和导出文件；退出码 0 为通过，2 为数据或计算校验失败。 / The audit command uses only the standard library to inspect the source and exports; exit 0 means pass, 2 means an audit failure.

## 范围与路线 / Scope and roadmap

后续阶段可评估多场景分析与更丰富的图表交互；负荷预测、配电网模型、受控接口与部署需另行设计和验证。所有能力须按测试和真实数据授权分别验收。 / Later stages may add richer scenario analysis and chart interaction; forecasting, network models, controlled interfaces and deployment require separate design and validation.

项目许可证尚未确定；请勿据此假定代码已获得开放源代码许可。 / The project license is undecided; do not assume this code has an open-source license yet.

详细契约见 `docs/`。 / See `docs/` for detailed contracts.
