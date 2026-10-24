# 接口契约 / Interface contract

当前仅有本地 CLI / Local CLI only: `python -m gridwise.cli INPUT.json OUTPUT_DIR`。成功退出码 0，校验或求解错误退出码 2。成功时生成 `summary.json`, `optimized.csv`, `baseline.csv`, `grid-purchase.svg`。失败时不应将新结果当作有效；若复用已有输出目录，旧文件可能仍存在，调用方应使用新目录或检查退出码。 / Exit 0 on success, 2 for validation or solve errors. On failure, do not trust preexisting files in a reused output directory.

`summary.json` 含 `optimized` 与 `baseline`，各含 `status`, `with_battery`, `rows`, `total_cost_cny`, `grid_energy_kwh`, `peak_grid_kw`, `pv_curtailed_kwh`。CSV 为对应 `rows`，UTF-8 BOM 便于 Windows 表格软件读取。 / Summary has both scenarios and the listed fields; CSV uses UTF-8 BOM for Windows spreadsheet compatibility.

未来 HTTP API、授权、版本迁移和真实数据连接均未实现。 / HTTP API, authentication, migrations and real data connections are not implemented.
