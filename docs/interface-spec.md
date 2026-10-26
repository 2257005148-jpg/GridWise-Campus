# 接口契约 / Interface contract

当前仅有本地 CLI / Local CLI only: `python -m gridwise.cli INPUT.json OUTPUT_DIR [--overwrite]`。成功退出码 0，校验、求解或文件冲突错误退出码 2。成功时生成 `summary.json`, `optimized.csv`, `baseline.csv`, `grid-purchase.svg`。默认只允许创建不存在的导出目标；任何同名目标已存在时拒绝整次运行。`--overwrite` 显式替换已有普通文件，但拒绝目录、符号链接、junction 和输入文件别名。失败时不应将旧文件当作本次新结果。 / Existing exports are refused by default; explicit overwrite accepts only regular export files and never aliases the source input.

v0.3.11 可选 `--peak-cap-kw 55` 启用逐时购电上限模式；`python -m gridwise.run_artifacts publish SCENARIO RUNS_PARENT --peak-cap-kw 55` 使用同一模式。仅优化方案施加上限，基线不施加；上限不可行时退出 2 且不发布成功产物。默认命令和 CSV/汇总顶层结构不变。启用时 `summary.run` 增加 `optimization_mode` 与 `peak_cap_kw`，`model_version` 家族为 `peak-cap-mip-v1`，已校验运行目录清单使用格式 2。独立审计按模式和版本核对逐时上限。/ The opt-in flag constrains optimized grid import, leaves the baseline alone, and records the policy in versioned metadata. Infeasible runs fail closed.

四个导出先在输出目录内暂存，暂存成功后才发布。默认模式用独占创建避免检查后被并发创建的文件遭覆盖；普通发布失败回滚本次新建文件。显式覆盖会备份旧文件并在普通替换失败时回滚。若回滚本身失败，错误会指向保留的 `.gridwise-stage-*` 恢复目录，不会删除其中的备份。跨四文件发布不具备事务原子性：并发读者可能观察到过渡状态，进程崩溃或外部不协作写入仍需人工核对；重要运行请使用唯一新目录并检查退出码和独立审计。 / Staging and best-effort rollback do not make a multi-file transaction crash safe; failed rollback retains recovery copies.

v0.3.6：发布过程中的普通 Python 异常也触发回滚；清理暂存目录失败时 `ExportCleanupError` 会注明发布是否完成及暂存路径。四文件发布期间的混合版本已由受控子进程实测；强制终止不能由异常回滚捕获。详情见[导出一致性设计](export-consistency-design.md)。 / Normal Python exceptions during publication also trigger rollback. `ExportCleanupError` states whether publication completed and identifies retained stage files. Controlled subprocess observation confirms mixed versions during publication; forced termination cannot be caught by exception rollback.

v0.3.7 增加独立、可选入口 `python -m gridwise.run_artifacts publish SCENARIO.json RUNS_PARENT` 和 `verify RUN_DIR`；Python API 为 `publish_run()` 与 `read_verified_run()`，后者返回已校验的字节及运行元数据，拒绝未完成、损坏或非约定目录。旧 CLI 的参数及四文件输出不变。格式、限制及保证边界见[运行目录契约](run-artifact-contract.md)。 / The opt-in run-bundle CLI and Python API verify bytes before consumption; the original CLI remains unchanged.

`summary.json` 含 `optimized` 与 `baseline`，各含 `status`, `with_battery`, `rows`, `total_cost_cny`, `grid_energy_kwh`, `peak_grid_kw`, `pv_curtailed_kwh`。CSV 为对应 `rows`，UTF-8 BOM 便于 Windows 表格软件读取。 / Summary has both scenarios and the listed fields; CSV uses UTF-8 BOM for Windows spreadsheet compatibility.

未来 HTTP API、授权、版本迁移和真实数据连接均未实现。 / HTTP API, authentication, migrations and real data connections are not implemented.

独立审计命令 / Independent audit command: `python tools/audit_exports.py SCENARIO.json OPTIMIZED.csv BASELINE.csv SUMMARY.json`。只依赖 Python 标准库，不构建 Pyomo 模型；校验通过退出 0，缺失、篡改或数值不合法退出 2，并将文件、方案和行号写入错误信息。 / This standard-library-only auditor exits 0 on success and 2 for missing, altered or invalid data, with location details in errors.

v0.1.2 审计器对空/截断文件、非法编码、畸形 CSV、额外或重复字段、资源超限也退出 2。成功标记 `AUDIT PASSED` 只在所有四个文件全部通过后输出；失败时写入 stderr，不输出成功标记。 / Empty, malformed, extra-field or over-limit inputs exit 2. The success marker is printed only after all four files pass.

v0.1.3 汇总新增 `run` 对象，含 `model_version`, `scenario_sha256`, `solver_name`, `status`；详细原始字节指纹规则见 `docs/input-provenance.md`。独立审计器要求此对象并核对给定输入字节。旧汇总缺少该对象时退出 2。 / The summary now binds to the exact scenario bytes and records model, solver, and status metadata. Legacy summaries without `run` fail audit.

v0.1.4 `run` 还要求 `git_commit` 与 `source_state`，完整 `model_version` 带规范化 `core.py` 的 SHA-256。默认审计要求当前模型源码、Git HEAD 和源码状态与汇总匹配；Git 不可用时使用显式 `unavailable` 值。不同提交或提交前后的旧导出需在匹配环境复核或重新生成。 / Audit checks the current model source and Git context; old or incomplete metadata fails explicitly.

v0.1.5 起 `run` 还必须含 `python_version`, `operating_system`, `solver_version`, `dependency_versions`, `dependency_lock_sha256`。当前普通审计命令没有历史参数；旧 schema、不同提交或缺依赖约束文件均退出 2，且不改写导出。v0.1.6 时历史只读模式尚在[设计](historical-audit-design.md)。 / Current exports require environment fields. The ordinary auditor has no historical option and fails closed without modifying exports; history was design-only in v0.1.6.

v0.1.7 新增独立历史命令 `python tools/audit_history.py --ref FULL_SHA --schema run-v015 SCENARIO OPTIMIZED_CSV BASELINE_CSV SUMMARY_JSON`。JSON 返回 `historical_context`, `current_context`, `arithmetic_physics`, `artifacts_unchanged` 和定位说明；当前上下文不匹配可与历史审计通过共存。只有历史上下文、历史算术/物理和原件不变都为 `PASS` 时退出 0，其余退出 2。仅支持此已知 schema；缺失证据标 `UNKNOWN`，未运行历史检查标 `NOT_RUN`，具体安全边界见[设计](historical-audit-design.md)。 / A separate history CLI reports three independent statuses plus artifact preservation, with one reviewed schema only.

v0.2.0 新增只读质量命令 `python -m gridwise.data INPUT.csv --step-hours 1`。stdout 为 JSON `QualityReport`：`source`, `ready`, `row_count`, `step_hours`, `missing_counts`, `issues[{code,line,field,detail}]`；有效退出 0，无效/缺文件退出 2。它不写入输入文件或优化产物。Python API `import_energy_csv(path, step_hours)` 仅在 `ready=true` 时返回完整 `records`；`prepare_visualization_data(imported, summary)` 对齐既有 `summary.json` 两方案的输入回显、时间与成本/电量/峰值，输出 `timestamps`, `units`, `step_hours`, `series`, `metrics`, `delta_optimized_minus_baseline`。该 API 是图表准备，不替代独立物理审计或最优性证明。 / A standard-library CSV quality CLI and chart-data API are available without a UI or solver change.

图表 API 以输入 CSV 的 `step_hours` 重算两方案逐时费用、累计费用、购电量与峰值，要求汇总指标在绝对容差 `1e-5` 内相符。时间戳文本必须逐行一致；输入负荷、可用光伏、电价需回显一致。不合格输入抛 `DataError`，没有部分图表输出。/ The chart API recalculates both scenarios from aligned input and raises `DataError` on any quality or alignment failure; it does not return partial chart data.

v0.3.0 本机入口 `python -m gridwise.desktop`。`dashboard.build_scenario(imported)` 要求 `ready=true`、非空且 ≤96 时段，以既有合成模板提供设备参数；`dashboard.run_comparison(imported)` 在临时目录求解两方案、独立审计并返回 `DashboardResult(chart, audit_message)`。任何质量、求解、审计或对齐失败均不返回图表并明确报错。原始 CSV 不自动修改或保存为结果。 / The desktop entry point accepts a validated series and returns chart data only after an audited comparison.

v0.3.1 `run_comparison` 增加仅供调用方控制的 `cancel_event`、`solve_timeout_seconds`、`audit_timeout_seconds` 可选关键字参数，默认各 120 秒。取消、超时或非零退出抛 `DashboardError`；未取得独立审计成功标记或图表数据不完整时，UI 不显示成功。界面工作线程不得直接访问 Tk 控件。 / Optional cancellation and subprocess timeouts bound interactive runs; failures leave no successful chart state, and all Tk updates occur on the main thread.

v0.3.8 `dashboard.run_comparison(imported, *, runs_root=None, ...)` 将成功结果发布到唯一运行目录，并在返回 `DashboardResult(chart, audit_message, run_name)` 前再次调用 `read_verified_run`。`dashboard.list_run_history(runs_root)` 只读枚举最多 50 个匹配目录（扫描上限 1000 项），返回 `VALID`、`INCOMPLETE` 或 `DAMAGED`。`dashboard.load_verified_comparison(imported, run_name, runs_root)` 重新读取和校验指定目录，随后要求已通过质量检查的当前 CSV 与汇总逐时一致；不返回部分图表。历史目录中的哈希仅证明读取到的字节与清单一致，不证明输入真实性。/ The controller publishes a unique audited run, rereads verified bytes, and prepares charts. Bounded history listing reports validity; loading rechecks the selected bundle against the currently checked CSV. No partial chart is returned on failure.

v0.3.9 `run_storage.load_saved_runs_root(config_path)` 在设置不存在时返回 `None`，无效、不可读、版本不符或目录失效时抛 `RunStorageError`；`save_runs_root(config_path, runs_root)` 仅接受已有绝对本地目录并执行有界单文件保存。桌面默认仍使用系统临时根目录，只有点击“保存目录 / Save root”才写设置。启动时读取已保存路径并异步刷新；设置错误时回退临时目录且保留原设置文件。`read_verified_run` 在返回字节前再次检查目录条目和可观察的目录身份变化。/ The storage API has explicit load/save behavior, and the desktop writes only on user request. Verified reading now rechecks visible directory mutations before returning bytes.

v0.3.12 `dashboard.run_comparison(..., peak_cap_kw=None)` 默认保持纯电费运行；显式数值时在构建的合成场景范围内校验并传给 `publish_run`。`DashboardResult` 返回 `optimization_mode`、`peak_cap_kw`，以及可选的 `cost_mode_cost_cny`。该对照值只来自安全读取通过、场景指纹相同、求解器相同、模型源码摘要相同且与当前 CSV 对齐的纯电费运行。`RunHistoryEntry` 同步返回模式与上限；桌面重新加载历史时恢复并显示这些字段。非法或不可行上限、非最优求解、审计或读取失败均不返回图表。/ The controller exposes the existing cap through a validated optional argument and carries verified policy metadata through history loading. Optional cost comparison requires a verified compatible cost-only run.
