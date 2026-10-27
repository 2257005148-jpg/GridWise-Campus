# 自定义设备参数 / Custom device parameters

GridWise Campus 桌面端可在运行前配置现有模型已经支持的电网与单电池参数。它不识别、发现或控制真实设备；参数由用户明确输入并仅用于本地离线优化。/ The desktop exposes the grid and single-battery constants already consumed by the offline model. It neither discovers nor controls physical equipment.

## 字段、单位与默认值 / Fields, units and defaults

| 界面字段 / UI field | 场景字段 / Scenario field | 单位 | 默认值 |
|---|---|---:|---:|
| 电网最大购电 / Grid max | `grid_max_kw` | kW | 120 |
| 电池容量 / Capacity | `battery.capacity_kwh` | kWh | 80 |
| 最大充电 / Max charge | `battery.max_charge_kw` | kW | 30 |
| 最大放电 / Max discharge | `battery.max_discharge_kw` | kW | 30 |
| 初始 SOC / Initial SOC | `battery.initial_kwh` | kWh | 40 |
| 终端 SOC / Terminal SOC | `battery.terminal_kwh` | kWh | 40 |
| 充电效率 / Charge efficiency | `battery.charge_efficiency` | ratio | 0.95 |
| 放电效率 / Discharge efficiency | `battery.discharge_efficiency` | ratio | 0.95 |

“SOC”沿用项目既有输出术语，但输入值是电池能量 kWh，不是 0–100% 百分比。默认值从 `examples/synthetic-day.json` 读取；任何字段变化后界面显示“用户自定义设备参数”。/ SOC input is stored energy in kWh, not a percentage. Defaults are loaded from the synthetic template and edits are visibly marked custom.

## 校验与行为 / Validation and behavior

- 所有字段必须是有限数值且不超过 10¹²；不接受空值、布尔值、NaN 或 Infinity。/ Values must be finite and bounded.
- 容量必须大于 0；电网、充电和放电功率必须非负。/ Capacity is positive; power bounds are nonnegative.
- 初始和终端能量必须在 `[0, capacity_kwh]`。/ Initial and terminal energy stay within capacity.
- 充电和放电效率位于 `(0,1]`。/ Efficiencies are in `(0,1]`.
- 参数合法不等于模型必然可行。例如购电与电池功率都为 0 时无法满足正负荷；求解器会明确失败，不生成成功运行。/ Valid parameters can still be infeasible; the run then fails closed.

设备参数改变时，已显示指标、图表和所选历史立即失效，但已通过的 CSV 质量报告保留。下一次运行把参数写入该次 `scenario.json`，随后由既有独立审计、清单和安全读取验证。选择历史运行时，界面从已校验场景恢复其实际参数。/ Edits invalidate stale results without discarding CSV quality. Each verified run stores and later restores the exact equipment values from its checked scenario.

## 来源与完整性边界 / Provenance and integrity boundary

运行清单覆盖 `scenario.json` 的大小与 SHA-256，`summary.run.scenario_sha256` 也绑定同一字节。这样可检测运行文件变化并避免不同设备场景被当作同一成本参考；它不能证明参数来自真实铭牌、获授权或适合控制设备。/ Scenario hashing detects content changes and separates cost references, but it does not prove authenticity, authorization or physical suitability.

本阶段只借鉴开源项目的配置思路，没有安装或复制其代码：PyPSA（MIT）以组件属性表达储能约束；EMHASS（MIT）区分电池容量、充放电功率和效率；python-microgrid（LGPL-3.0）使用模块化电池参数。它们的依赖和系统边界远大于本项目需求，因此继续使用标准库小型结构。/ External projects informed the explicit-configuration design only; their code and dependencies were not adopted.
