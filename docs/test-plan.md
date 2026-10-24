# 测试计划 / Test plan

当前 pytest 覆盖 / Current pytest cases: 示例求解及文件导出；零负荷、零光伏、满电、空电组合；购电上限导致不可行；缺字段、无效单位、不连续时间、无效效率；求解器不可用；篡改结果被独立校验拦截；半小时时步下的充放电效率及终端 SOC；输出输入字段一致性。 / Example solve and export; energy boundaries; infeasibility; bad data; unavailable solver; corrupted result rejection; efficiency and terminal SOC at a half-hour step; input fields echoed in output.

2026-10-25 本机运行：`20 passed, 0 failed, 0 skipped`。另用只含 Python 标准库的独立脚本读取原始 JSON 和两个 CSV，未调用 `gridwise` 或 Pyomo，逐时复核输入字段、功率平衡、SOC 递推、效率、上下界、终端 SOC、成本和峰值，均在 `1e-5` 容差内通过。 / Local pytest: 20 passed; separate standard-library arithmetic audit of source JSON and both CSV files passed within `1e-5`.

| 方案 / Scenario | 每时段电费 / Period costs (CNY) | 总电费 / Total | 购电量 / Import | 峰值 / Peak |
|---|---|---:|---:|---:|
| 储能优化 / optimized | 45 + 28.5110803324 + 0 + 0 + 15 + 42.5 | 131.0110803324 CNY | 247.0221606648 kWh | 90 kW |
| 无储能 / baseline | 30 + 22.5 + 13.5 + 24 + 45 + 27.5 | 162.50 CNY | 240 kWh | 60 kW |

两方案读取同一 JSON，时间步均为 1 小时，逐时负荷、光伏和电价完全一致；峰值均定义为 `max(grid_kw)`。在该案例中，电费下降约 31.49 CNY，但峰值从 60 升至 90 kW；没有削峰目标或约束。 / Both use the same source, one-hour steps, load, PV and prices; peak is `max(grid_kw)`. Cost falls while peak rises, because peak is absent from the objective.

样例的解析交叉检查 / Analytic cross-check: 往返效率为 `0.95² = 0.9025`，低价充电每提供 1 kWh 放电需至少 `1/0.9025` kWh 充电，因此边际购电费至少 `0.5/0.9025 ≈ 0.5540` CNY/kWh。高价三个时段可有效替代的购电量为 `15 + 20 + 30 = 65` kWh，避免电费 `15×0.9 + 20×1.2 + 30×1.0 = 67.5` CNY；所需低价充电 `65/0.9025 = 72.0221606648` kWh，增加电费 `36.0110803324` CNY。其余低价时段放电会因效率损失增加成本。导出调度满足 SOC、功率和容量限制，达到该下界，故样例成本 `162.5 − 67.5 + 36.0110803324 = 131.0110803324` CNY。 / The exported feasible schedule attains this lower bound for the synthetic example; this argument does not generalize to arbitrary inputs.

通过标准 / Pass criteria: 所有输入不合法时明确失败；可行解状态为 optimal；每时段功率、SOC、边界、目标汇总在 `1e-5` 容差内；同一输入生成可行无储能基线。 / Explicit failure for invalid input, optimal feasible solves, numerical checks within tolerance, comparable baseline.

尚未测试 / Not yet tested: 大规模性能、跨平台、恶意文件、真实校园数据、时区夏令时、安装重现性、图像视觉验收、部署和安全渗透。 / Scale, cross-platform, malicious files, real data, DST, reproducible installation, visual QA, deployment and security remain open.
