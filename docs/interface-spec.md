# 接口契约 / Interface contract

当前仅有本地 CLI / Local CLI only: `python -m gridwise.cli INPUT.json OUTPUT_DIR`。成功退出码 0，校验或求解错误退出码 2。成功时生成 `summary.json`, `optimized.csv`, `baseline.csv`, `grid-purchase.svg`。失败时不应将新结果当作有效；若复用已有输出目录，旧文件可能仍存在，调用方应使用新目录或检查退出码。 / Exit 0 on success, 2 for validation or solve errors. On failure, do not trust preexisting files in a reused output directory.

`summary.json` 含 `optimized` 与 `baseline`，各含 `status`, `with_battery`, `rows`, `total_cost_cny`, `grid_energy_kwh`, `peak_grid_kw`, `pv_curtailed_kwh`。CSV 为对应 `rows`，UTF-8 BOM 便于 Windows 表格软件读取。 / Summary has both scenarios and the listed fields; CSV uses UTF-8 BOM for Windows spreadsheet compatibility.

未来 HTTP API、授权、版本迁移和真实数据连接均未实现。 / HTTP API, authentication, migrations and real data connections are not implemented.

独立审计命令 / Independent audit command: `python tools/audit_exports.py SCENARIO.json OPTIMIZED.csv BASELINE.csv SUMMARY.json`。只依赖 Python 标准库，不构建 Pyomo 模型；校验通过退出 0，缺失、篡改或数值不合法退出 2，并将文件、方案和行号写入错误信息。 / This standard-library-only auditor exits 0 on success and 2 for missing, altered or invalid data, with location details in errors.
