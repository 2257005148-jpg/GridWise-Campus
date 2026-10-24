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
