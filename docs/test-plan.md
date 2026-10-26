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

v0.1.7 `tests/test_audit_history.py` 只用合成场景与临时 Git 仓库测试 `run-v015` 原型：历史成功但当前上下文失败、当前成功但历史失败、缺提交/schema、改写历史审计器/模型/约束、Git attributes、来源状态不明、Git/依赖缺失、结果输入回显不符、临时工作树创建/清理失败和审计期间原件变化。所有失败路径要求显式状态与非零退出；成功路径要求四个原件字节不变、临时 worktree 已清理。具体通过/失败/跳过数量见本阶段报告。 / Synthetic temporary-repository cases exercise status, checkout safety and cleanup paths.

v0.1.8 安全回归补充 / Security regressions: 临时仓库配置有可观察 `post-checkout` 钩子时不得执行；检出锁文件字节与 Git blob 不同时，历史算术不得运行；快照前及快照后原件被替换都要失败；成功路径校验四原件字节与临时工作树清理。测试仓库的依赖约束按实际安装的四个运行包生成，以避免把 Windows 观察到的传递版本误当成其他平台的锁。CI 需完整 Git 历史才能读取已审查的 v0.1.5 blob；其他平台执行状态见阶段报告。 / Tests exercise checkout safety and stable snapshots using only synthetic repositories.

v0.1.9 增加当前模型源码不可读的合成回归：`current_context=UNKNOWN`，而历史预检和算术可独立为 `PASS`；修复前该路径抛出未处理的 `AuditError`，没有结构化状态。继续复用既有创建/清理失败、attributes、换行、原件变化和缺提交/schema 测试。最终数量见 v0.1.9 报告，不能从静态覆盖推断其他平台通过。 / A new regression confirms independent status reporting when the current source cannot be read.

v0.2.0 `tests/test_data.py` 覆盖合成输入 CSV 与 JSON 一致、15/30/60 分钟步长、缺失值统计且无部分数据、错误列名/单位/顺序、重复/错序/缺口/无偏移时间、异常数值、空/截断/错误编码/空白行和缺文件，以及质量 CLI 退出码。图表准备测试从合成求解生成两方案，核对成本、购电量、峰值与 SOC 时序，并拒绝时间、输入回显或汇总篡改。最终实际数量和平台范围见 v0.2.0 阶段报告；图表准备不替代既有独立导出审计。 / Synthetic tests cover parsing, quality, series completeness, metrics and chart alignment.

v0.2.0 验收补测 / Acceptance closure: 对 BOM、空白值、跨偏移同一瞬间、无效时间、32 MiB 文件、64 KiB 物理行、50,000 时段、128 字符数值 token 进行单独断言；检查导入前后字节一致和失败时无时序；用两时段手算样例精确核对逐时/累计成本、购电量、峰值、顺序及单位，并分别篡改两方案输入回显与时间戳。实际测试次数见新的验收进度报告。 / Explicit boundary and exact-metric regressions supplement the initial v0.2.0 suite.

v0.3.0 `tests/test_dashboard.py` 使用合成 CSV 检查实际求解、独立审计、成本/峰值与五组图表序列；无效输入、超过 96 时段、独立审计失败都不得产生图表。Windows 图形桌面还应执行 Tk 窗口创建、质量检查、运行和 Canvas 刷新的动态烟测。完整 pytest 与既有导出审计仍须通过；具体数量与平台范围见阶段报告。 / Controller regressions and a GUI smoke check cover the local prototype; platform claims require actual execution.

v0.3.1 增加 Windows Tk 控制层与后台流程回归：重复点击不重启任务、输入切换丢弃过期结果、关闭时取消、残缺图表不得展示成功、错误 CSV 禁用调度且原件字节不变；控制层还检验求解超时、求解失败、审计失败与临时目录清理。GUI 烟测须实际创建窗口并走有效和无效输入路径；平台范围、数量和退出码见 v0.3.1 阶段报告。 / Tk and controller tests exercise cancellation, stale-result rejection, failure gating, cleanup and a real Windows window smoke test.

v0.3.2 增加四档 Tk 程序化缩放的最小窗口布局断言：图表高度、页签宽度、指标卡宽度、按钮边界和滚动可达性；双倍缩放的时间标签不得重叠。超长路径应报告失败且不崩溃，后台运行条在结束时隐藏。与 v0.3.1 异步、审计和数据层测试一起运行；真实系统显示缩放、Linux/macOS 与托管 CI 的执行状态须在阶段报告中单列。 / Layout and state regressions cover four Tk scaling factors, chart label separation, long paths, scrolling and activity feedback; platform execution claims remain evidence based.

v0.3.3 增加独立子进程中的 DPI 入口检查、只读日志的 Tab 焦点顺序、Page Up/Down 页面滚动、重复质量检查保护，以及键盘触发选择文件、质量检查、运行和页签切换。验收脚本在可见 Windows Tk 窗口连续执行 20 次合成比较，并检查无效输入拦截、输入字节与临时目录状态。它是短时重复运行证据，不代表长时间耐久性。只有在真实不同 DPI 显示器上移动窗口并观察后，才能将跨显示器验证标为通过。 / The added checks exercise process DPI setup, keyboard workflow, duplicate-check protection and twenty short synthetic GUI runs. They do not establish long-duration or cross-monitor DPI behavior.

v0.3.4 补测取消按钮/Esc、取消后重试、质量检查的迟到结果、后台子进程取消后的回收，以及窗口关闭必须等待当前/旧后台任务清理，并重跑原有数据/图表和全量测试。可见窗口烟测应覆盖有效与无效 CSV、20 次短时合成运行、原始文件 SHA-256、子进程/线程/临时目录清理；物理键盘、屏幕阅读器、双显示器原生 DPI 和长时间耐久性分别报告。 / The cancellation, close-time cleanup and existing regressions supplement the suites. Synthetic GUI smoke tests do not substitute for physical-keyboard, screen-reader, dual-monitor or long-duration acceptance.

v0.3.5 CLI 定向测试须先复现默认覆盖，再检查四个目标文件各自冲突、显式覆盖、目录/链接/输入别名拒绝、并发创建冲突及中途替换失败回滚。Tk 焦点采用独立 Tk 最小探针与应用测试有限重复；任何失败记录原始堆栈，不能把重跑通过当作根因消失。质量列表超过八项时，最后一项仍应在可聚焦日志中完整可读。UI Automation 控件树、真实物理键盘与屏幕阅读器分开记录。 / CLI collision and rollback probes, isolated Tk focus loops, complete quality-list access, and separate accessibility evidence are required.

v0.3.6 新增 CLI 故障注入（非 I/O 异常、备份创建失败、清理失败）、两个默认写入者竞争、强制终止后的混合版本观察；测试仅操作临时目录与自身创建的子进程。Tk 诊断脚本在独立子进程记录 Tcl/Tk 版本、库路径、关键 Tcl 文件、焦点遍历及异常；桌面测试关闭路径须取消定时回调。Windows UI Automation 实测、Tk 自动化、物理键盘和屏幕阅读器结果分开报告。测试通过不能推断断电恢复、多文件原子提交或既往间歇焦点问题根因已解决。 / Added fault injection and bounded isolated Tk diagnostics. Results do not establish power-loss recovery, bundle atomicity or resolution of the earlier intermittent focus issue.

v0.3.7 运行目录测试覆盖 CLI 发布/验证、两次运行不覆盖、文件损坏/缺失、清单及标记篡改、路径穿越/重复文件名、大小上限、链接拒绝、元数据不一致、并发读取、审计失败、取消、文件发布失败、清单失败、读取期间改写及本阶段子进程强制终止。旧 CLI、数据/控制、桌面与独立审计仍需完整回归；强制终止试验只杀测试自身子进程。结果数字以阶段报告实际运行输出为准。 / The new suite exercises the bundle contract, corruption and failure paths; existing suites and independent audit remain required.

v0.3.8 控制层与 Windows Tk 回归覆盖发布后重新安全读取、历史目录重新校验、损坏拒绝、不同 CSV 拒绝、两次运行隔离、输入变更清空显示，以及既有取消、重复点击和关闭等待。完整求解、独立导出审计和全量 pytest 的实测数字以本阶段报告为准；旧 Tk 间歇异常根因仍需单独诊断。 / Controller and Windows Tk regressions exercise verified publication, history revalidation, corruption rejection, input matching, repeated runs and stale-state prevention. Existing cancellation and close checks remain; measured results and limitations are in the stage report.

v0.3.9 增加临时设置文件中的持久目录保存/恢复、损坏设置拒绝、保存失败保留旧设置、桌面重启后历史选择、不可读与不可写根目录、读取时增加目录项及删除文件、junction、重复键和非有限 JSON 故障注入。Tk 探针对初始化失败输出结构化记录；重复诊断需分别记录同进程与独立进程次数和失败。完整测试与独立审计数量以阶段报告实测为准；之前的间歇 `tk_focusNext` 失败不得被后续全绿覆盖。/ Regressions cover explicit storage, restart, access failures, visible directory mutation and structured Tk startup failure evidence. Passing reruns do not erase the earlier intermittent focus failure.
