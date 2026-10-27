# 数据字典 / Data dictionary

输入 JSON 使用 UTF-8。`timeseries` 非空，时间戳为带 UTC 偏移的 ISO 8601 字符串，连续且等间隔；示例使用 `+08:00`，没有强制特定校园时区。 / UTF-8 JSON; nonempty regular time series with offset-aware ISO 8601 timestamps. Example uses UTC+08:00 without hardcoding a campus timezone.

| 字段 / Field | 类型与单位 / Type, unit | 规则 / Rule |
|---|---|---|
| `power_unit`, `energy_unit`, `price_unit` | string | 必须为 `kW`, `kWh`, `CNY/kWh` / exact values |
| `step_hours` | number, h | > 0；与相邻时间戳一致 / matches timestamps |
| `grid_max_kw` | number, kW | ≥ 0 |
| `timeseries[].timestamp` | ISO 8601 string | 必须带 UTC 偏移 / offset required |
| `load_kw`, `pv_kw`, `price_cny_per_kwh` | number | ≥ 0，有限值；缺失直接报错 / finite, no missing values |
| `battery.capacity_kwh` | number, kWh | > 0 |
| `battery.initial_kwh`, `terminal_kwh` | number, kWh | 0 到容量 / within capacity |
| `battery.max_charge_kw`, `max_discharge_kw` | number, kW | ≥ 0 |
| `battery.charge_efficiency`, `discharge_efficiency` | number | (0, 1] |

输出每行含输入时间、负荷、光伏可用量、电价，以及光伏利用/弃光、购电、充放电、步末 SOC；汇总为成本 CNY、购电量 kWh、峰值 kW、弃光 kWh。浮点容差 `1e-5` 用于结果校验。 / Output includes period values and aggregate cost, import, peak and curtailment; validation tolerance is `1e-5`.

独立审计使用 `Decimal`，对各数值约束与汇总采用**绝对容差** `0.00001`，分别以被检字段的单位计；输入单位字符串和时间戳要求精确一致。校验前不舍入，命令行展示时才保留两位小数。CSV 使用 UTF-8 BOM，数值以文本读入；JSON 不允许 `NaN` 或 `Infinity`。 / The auditor uses Decimal and an absolute `0.00001` tolerance in each checked field's unit. It never rounds before validation; CLI display uses two decimals. Units and timestamps match exactly; non-finite JSON is rejected.

v0.1.2 离线验证资源边界 / Offline audit bounds: 单文件 ≤ 32 MiB，CSV 单物理行 ≤ 64 KiB，时段数 ≤ 50,000，数值 token ≤ 128 字符，单步时长 ≤ 24 小时，任意数值绝对值 ≤ 10¹²（相应字段单位）。文件、行、时段及 token 限制审计过程内存/解析成本；24 小时保持当前子日调度粒度；10¹² 是为拒绝明显荒谬输入设置的宽松软件边界，并非校园设备的物理额定值。这些适用输入界限已同步到模型输入。50,000 个 15 分钟时段约覆盖 1.4 年。 / File, line, row and token caps bound parsing resources. The 24-hour and 10¹² caps define the supported synthetic-input envelope, not equipment ratings.

审计器要求场景、汇总及 CSV 使用当前契约的**精确字段集合**，CSV 列顺序也固定；额外字段或重复 JSON 键均失败，避免悄然忽略新数据。 / Exact schema and column order are required; extra fields and duplicate JSON keys fail closed.

## v0.2.0 离线输入 CSV / Offline input CSV

新输入 CSV 与上述**结果 CSV** 是不同契约。输入列固定为 `timestamp,load_kw,pv_kw,price_cny_per_kwh`，按此顺序且不允许额外/重复列；字段后缀就是单位，不自动转换 W、MW 或其他币种。UTF-8 可带 BOM；逐行时间带 UTC 偏移，按显式 `step_hours`（h）绝对时间连续。负荷、光伏和电价非负、有限、≤10¹²；缺失值统计后使该次导入 `ready=false`，不返回部分有效时序。 / Input CSV uses exact unit-bearing headers, offset-aware timestamps and all-or-nothing readiness.

`QualityReport` 含来源路径、`ready`、原始数据行数、步长、每列 `missing_counts` 及带代码/物理行/字段的 `issues`。CSV 单文件 ≤32 MiB、单物理行 ≤64 KiB、时段 ≤50,000、数值 token ≤128 字符、步长为 (0,24] 小时。异常不会被插值或自动修复。 / The report records missingness and localizable issues without imputation.

图表数据的功率为 kW、SOC/购电量为 kWh、电价为 CNY/kWh、费用为 CNY；逐时电费按 `grid_kw × price_cny_per_kwh × step_hours` 计算。成本、峰值和购电量分别展示，不将电费下降称作削峰或节能。 / Chart metrics preserve distinct units and meanings.

验收补充 / Acceptance detail: `grid_energy_kwh = Σ(grid_kw × step_hours)`；`peak_grid_kw = max(grid_kw)`，峰值按单时段购电功率定义。不同 UTC 偏移的时间戳按绝对瞬间判断重复与间隔；不填补缺口。导入器保留原始 CSV 字节，质量不合格时 `records` 为空。 / Peak uses period grid power, timestamps are compared as instants, and failed imports expose no partial series.

v0.3.13：合法时间戳通过检查后，内部 `EnergyRecord.timestamp` 使用 `datetime.isoformat()` 规范化；例如 UTC 的 `Z` 与 `+0000` 统一为 `+00:00`。规范化不改变绝对时间、顺序或间隔，也不写回原始 CSV。`summarize_energy_input` 只接受 `ready=true` 的非空导入，返回起止时间、行数、步长、负荷/PV/价格最小最大值，以及 `load_energy_kwh=Σ(load_kw×step_hours)` 和 `pv_available_energy_kwh=Σ(pv_kw×step_hours)`。该摘要描述输入，不是求解结果或真实性证明。/ Valid timestamps are canonicalized in memory without rewriting the CSV. The input summary uses validated records and equal steps to calculate load and available-PV energy; it is not an optimization result or authenticity claim.

桌面自定义 CSV 仅替换 `timeseries` 中的时间、负荷、光伏和电价。`grid_max_kw` 与全部 `battery` 字段继续来自 `examples/synthetic-day.json`；桌面交互求解最多 96 时段。运行产物保存生成后的规范化 `scenario.json`，不保存原始 CSV 字节。/ Desktop CSV import changes only the time series; grid and battery assumptions remain synthetic, and verified run bundles contain the normalized scenario rather than the source CSV bytes.

v0.1.3 `summary.run.scenario_sha256` 为 `sha256:` 前缀加原始 UTF-8 输入字节的 64 位小写十六进制摘要；不规范化 BOM、换行、键顺序、数值文本或时间戳。`model_version=cost-mip-v1` 表示未改变的纯电费优化模型；`solver_name=appsi_highs`，`status=optimal`。完整来源边界见 `docs/input-provenance.md`。 / The digest covers exact source bytes; metadata identifies the unchanged cost model and configured solver, without asserting input authenticity.

v0.1.4 完整 `model_version` 改为 `cost-mip-v1+sha256.<规范化 core.py 摘要>`；`cost-mip-v1` 保留为家族名。`run.git_commit` 为 40 位 HEAD 或 `unavailable`；`run.source_state` 为 `clean`、`dirty` 或 `unavailable`。Git 状态检查范围与未提交源码行为见 `docs/input-provenance.md`。 / The full version now binds to normalized model source; Git fields explicitly describe the checkout state.

v0.3.11 可选 `--peak-cap-kw K` 为**储能优化方案**的逐时购电上限，单位 kW，有限数值且 `0 ≤ K ≤ grid_max_kw`。启用时 `summary.run` 额外包含 `optimization_mode="peak_cap"` 和数值 `peak_cap_kw=K`，模型家族为 `peak-cap-mip-v1`；默认模式没有这两个额外字段，家族仍为 `cost-mip-v1`。两种版本后缀均为当前规范化 `core.py` 的 SHA-256。基线仍用原有 `grid_max_kw`，不受 `K` 约束。/ The opt-in cap applies only to storage optimization; default metadata and baseline semantics remain unchanged. A source digest identifies code content, not authenticity.
