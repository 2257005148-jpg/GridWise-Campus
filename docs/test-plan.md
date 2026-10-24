# 测试计划 / Test plan

当前 pytest 覆盖 / Current pytest cases: 示例求解及文件导出；零负荷、零光伏、满电、空电组合；购电上限导致不可行；缺字段、无效单位、不连续时间、无效效率；求解器不可用；篡改结果被独立校验拦截；半小时时步下的充放电效率及终端 SOC；输出输入字段一致性。 / Example solve and export; energy boundaries; infeasibility; bad data; unavailable solver; corrupted result rejection; efficiency and terminal SOC at a half-hour step; input fields echoed in output.

v0.1 基线于 2026-10-25 本机运行：`20 passed, 0 failed, 0 skipped`。另用只含 Python 标准库的独立脚本读取原始 JSON 和两个 CSV，未调用 `gridwise` 或 Pyomo，逐时复核输入字段、功率平衡、SOC 递推、效率、上下界、终端 SOC、成本和峰值，均在 `1e-5` 容差内通过。 / The v0.1 baseline had 20 passing tests; separate standard-library arithmetic checks passed within `1e-5`.

| 方案 / Scenario | 每时段电费 / Period costs (CNY) | 总电费 / Total | 购电量 / Import | 峰值 / Peak |
|---|---|---:|---:|---:|
| 储能优化 / optimized | 45 + 28.5110803324 + 0 + 0 + 15 + 42.5 | 131.0110803324 CNY | 247.0221606648 kWh | 90 kW |
| 无储能 / baseline | 30 + 22.5 + 13.5 + 24 + 45 + 27.5 | 162.50 CNY | 240 kWh | 60 kW |

两方案读取同一 JSON，时间步均为 1 小时，逐时负荷、光伏和电价完全一致；峰值均定义为 `max(grid_kw)`。在该案例中，电费下降约 31.49 CNY，但峰值从 60 升至 90 kW；没有削峰目标或约束。 / Both use the same source, one-hour steps, load, PV and prices; peak is `max(grid_kw)`. Cost falls while peak rises, because peak is absent from the objective.

样例的解析交叉检查 / Analytic cross-check: 往返效率为 `0.95² = 0.9025`，低价充电每提供 1 kWh 放电需至少 `1/0.9025` kWh 充电，因此边际购电费至少 `0.5/0.9025 ≈ 0.5540` CNY/kWh。高价三个时段可有效替代的购电量为 `15 + 20 + 30 = 65` kWh，避免电费 `15×0.9 + 20×1.2 + 30×1.0 = 67.5` CNY；所需低价充电 `65/0.9025 = 72.0221606648` kWh，增加电费 `36.0110803324` CNY。其余低价时段放电会因效率损失增加成本。导出调度满足 SOC、功率和容量限制，达到该下界，故样例成本 `162.5 − 67.5 + 36.0110803324 = 131.0110803324` CNY。 / The exported feasible schedule attains this lower bound for the synthetic example; this argument does not generalize to arbitrary inputs.

通过标准 / Pass criteria: 所有输入不合法时明确失败；可行解状态为 optimal；每时段功率、SOC、边界、目标汇总在 `1e-5` 容差内；同一输入生成可行无储能基线。 / Explicit failure for invalid input, optimal feasible solves, numerical checks within tolerance, comparable baseline.

尚未测试 / Not yet tested: 大规模性能、跨平台、恶意文件、真实校园数据、时区夏令时、安装重现性、图像视觉验收、部署和安全渗透。 / Scale, cross-platform, malicious files, real data, DST, reproducible installation, visual QA, deployment and security remain open.

v0.1.1 新增审计工具与黑盒失败测试：标准库脚本读取原始 JSON、两个 CSV 和汇总 JSON；对单字段篡改、两份导出同步篡改、缺失列/行/汇总字段、重复或错序时间、非有限数值、错误单位及输入回显不一致均明确拒绝。另测试 15/30/60 分钟步长、零和高价、不可行终端 SOC、求解器非最优状态。阶段运行数量以对应进度报告中的实际 `pytest` 输出为准，不沿用上段 v0.1 的 20 项数字。 / v0.1.1 adds a standard-library export audit and black-box rejection tests; see its progress report for the measured run count.

v0.1.2 增加审计器公开 CLI 黑盒测试：正常退出 0；空/截断文件、畸形 CSV、空 CSV 行、错误编码、额外/重复/错序字段、异常数值、文件/行/时段/步长超限、缺失或拒绝读取的文件均退出 2，stderr 包含定位信息，stdout 不含 `AUDIT PASSED`。同步检查模型输入的步长、时段与数值幅度上限。具体通过/失败/跳过数量以 v0.1.2 阶段报告中的最终命令输出为准。 / CLI black-box cases cover valid inputs, malformed and over-limit inputs, file errors, and fail-closed diagnostics; final counts are recorded in the v0.1.2 report.

v0.1.3 增加原始字节指纹及汇总元数据回归：相同场景可复核；输入字段、数值文本或时区原文改变后旧汇总失败；伪造/缺失模型版本、指纹、求解器或状态失败；同步更新指纹但保留旧结果仍触发输入回显失败；UTF-8 BOM 与 LF/CRLF 生成并审计通过。性能工具 `tools/benchmark_audit.py` 生成 6 与 50,000 时段的合成导出、近 32 MiB 有效汇总及三类超限输入，测量审计 CLI 耗时和 Windows 子进程峰值工作集。最终测试数量和资源数值见 v0.1.3 报告。 / Provenance and encoding regressions plus bounded performance cases are recorded in the stage report.

v0.1.4 回归覆盖完整模型源码摘要、Git 提交及 clean/dirty/unavailable 状态、缺失字段和旧汇总；复制并改变 `core.py` 可复现版本不匹配。性能工具对 5 类合成输入各运行 2–10 次（默认 3），记录每次端到端时间与峰值工作集，并汇总中位数、最小值、最大值。拒绝覆盖已有输出目录。测试及性能测量的实际结果以 v0.1.4 阶段报告为准。 / Regression covers source-linked versions, Git context, legacy metadata, and repeated bounded audit timing.

v0.1.6 当前审计器增加旧 `run` schema 不改写的黑盒负例、缺少依赖约束文件的失败定位。历史模式的完整正反测试矩阵见[历史审计设计](historical-audit-design.md)；设计用例尚未执行，不计入 pytest 通过数。独立 Windows Python 3.12 环境安装、测试与合成审计的实际结果见 v0.1.6 阶段报告。 / New black-box tests confirm fail-closed legacy and missing-lock behavior. Historical-mode cases remain a plan, not passing tests.
