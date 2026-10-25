# 接口契约 / Interface contract

当前仅有本地 CLI / Local CLI only: `python -m gridwise.cli INPUT.json OUTPUT_DIR`。成功退出码 0，校验或求解错误退出码 2。成功时生成 `summary.json`, `optimized.csv`, `baseline.csv`, `grid-purchase.svg`。失败时不应将新结果当作有效；若复用已有输出目录，旧文件可能仍存在，调用方应使用新目录或检查退出码。 / Exit 0 on success, 2 for validation or solve errors. On failure, do not trust preexisting files in a reused output directory.

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
