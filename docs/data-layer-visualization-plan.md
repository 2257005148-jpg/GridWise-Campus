# v0.2.0 能源数据层与可视化方案 / Energy data layer and visualization plan

状态 / Status: **方案先行；仅合成与用户明确提供的数据 / Proposal first; synthetic and explicitly supplied data only**.

## 现状与边界 / Existing system and boundary

现有路径为 `合成场景 JSON → Pyomo/HiGHS 纯电费调度 → summary.json + CSV + SVG → 独立审计`。场景模型要求 kW、kWh、CNY/kWh、带偏移时间戳和固定步长；现有 CSV 是**结果导出**，没有通用输入质量报告。v0.2.0 先补独立数据解析层，不改变模型、求解器、目标函数或历史审计契约；暂不实现实时连接、数据库、设备控制和交互式 UI。 / The first gap is a reusable input and quality contract, separate from dispatch and device access.

## 开源参考评估 / Open-source reference assessment

以下为 2026-10-25 对项目自身仓库和许可证文件的静态查阅，不代表安装、集成或法律授权结论。 / These are source-backed design references, not installed dependencies or legal clearance.

| 项目 / Project | 官方范围与许可 / Official scope and license | 对本阶段适配度 / Fit | 可借鉴与决定 / Decision |
|---|---|---|---|
| [OpenEMS](https://github.com/OpenEMS/openems) | Edge/Backend 为 EPL-2.0，UI 为 AGPL-3.0；含设备通信、聚合和实时界面。 / Edge and Backend EPL-2.0; UI AGPL-3.0. | 架构概念高，代码复用低；当前禁止设备连接且项目许可未定。 / High conceptual fit, low code fit. | 借鉴采集、分析、展示分层的**概念**；不复制代码或接入 Edge。 / Concept only. |
| [Emoncms](https://github.com/emoncms/emoncms) · [许可证](https://github.com/emoncms/emoncms/blob/master/LICENSE.txt) | 时序输入、feed、图表和仪表板；AGPL-3.0。 / Time-series feeds and dashboards; AGPL-3.0. | 指标展示概念高，PHP/MySQL/Redis 栈与当前 Python 离线原型不符。 / Good visualization reference, poor stack fit. | 借鉴“输入质量→时序→图表”的信息组织；不复制 UI、服务或代码。 / Information architecture only. |
| [PyPSA](https://github.com/PyPSA/PyPSA) · [许可证](https://github.com/PyPSA/PyPSA/blob/master/LICENSE) | 电力系统分析/优化；MIT。 / Power-system analysis and optimization; MIT. | 后期多节点电网研究可参考；对本阶段 CSV 质量层和轻量可视化不直接适配。 / Future modeling reference, limited data-layer fit. | 仅记录为后续独立模型评估候选；不更换 Pyomo + HiGHS。 / No integration now. |

许可证信息来自各项目官方仓库。采用设计思想不等于取得复制代码、商标或数据的许可；项目自身正式许可证仍待有权人决定，发布前另行核验。 / No upstream code, assets or data are copied in this stage.

## 最小数据契约 / Minimum data contract

输入 CSV 采用 UTF-8（可带 BOM），唯一且固定列顺序：`timestamp,load_kw,pv_kw,price_cny_per_kwh`。字段名自带单位，必须精确匹配；`load_w`、`pv_mw` 或其他列视为单位/契约错误，不自动换算。调用方显式传 `step_hours`（小时，正数且 ≤24）；时间戳为带 UTC 偏移的 ISO 8601 文本，逐行严格递增，按绝对时间间隔等于 `step_hours`。负荷、光伏、电价为非负有限数，数值幅度沿用输入上限 `10¹²`。 / Exact headers encode units; no implicit unit conversion or time-zone assumption.

CSV 检查沿用单文件 32 MiB、物理行 64 KiB、最多 50,000 时段、数值 token 最多 128 字符。质量报告提供 `ready`、`row_count`、每列 `missing_counts`、带文件/行/字段的 `issues`。缺失值包括空格或空字符串；同时统计并阻止下游计算。重复表头、额外列、畸形 CSV、缺失/重复/错序/不连续时间、非有限或越界数值明确失败。`ready=false` 时不提供可用于优化或图表的部分数据；原始 CSV 只读。 / Quality reporting is diagnostic; invalid data never silently become a usable partial series.

## 可视化规划与指标定义 / Visualization plan and metric definitions

未来界面至少包括：负荷与可用光伏 kW 时序；优化与无储能购电 kW 对比；逐时及累计购电成本 CNY；储能 SOC kWh；同一时间窗、同一步长的成本/购电量/购电峰值三张独立指标卡。输入质量应先于任何图表显示，给出缺失数、时间缺口、单位和行号。 / Charts should receive validated, chart-ready data only.

本阶段只准备图表数据契约，不添加 UI 框架。图表数据从同一原始输入和已导出优化/基线行按时间戳对齐；原始输入负荷、光伏、电价必须在两方案一致。`period_cost_cny = grid_kw × price_cny_per_kwh × step_hours`，`grid_energy_kwh = Σ grid_kw × step_hours`，`peak_grid_kw = max(grid_kw)`。按原始顺序提供序列与单位，UI 不自行改变定义。 / Chart data preserve the comparison basis and units.

**电费优化**只表示购电费用变化；**削峰**只由 `peak_grid_kw` 变化表述；**减少购电量**只由 `grid_energy_kwh` 变化表述。当前合成样例电费下降，但峰值和购电量上升；不标成削峰或节能。若未来要削峰，需另设目标或约束及独立对比。 / Cost, peak and energy outcomes remain separate.

## 最小实施与验收 / Minimum implementation and acceptance

1. 使用 Python 标准库实现只读 CSV 导入与质量报告；不新增 pandas、UI 框架或外部服务。 / Standard library first.
2. 增加图表数据准备函数，要求已验证的输入与优化/基线导出对齐，输出含明确单位的序列和三类指标；该函数不证明求解最优或输入真实。 / Prepare aligned chart data without replacing independent audit.
3. 用合成临时文件测试有效 15/30/60 分钟、空值统计、重复/错序/缺口/无时区时间、错误单位/列、畸形/非有限数值、指标公式与图表序列对齐。运行定向与完整测试，并由现有独立审计器复核合成求解结果。 / Synthetic positive and negative tests are required.
4. 更新中英双语契约、架构和阶段报告，记录实际命令、退出码及 `NOT_RUN`。仅确认本阶段文件后作本地提交。 / Keep evidence and scope explicit.
