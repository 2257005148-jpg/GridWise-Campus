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
