# 架构 / Architecture

当前数据流 / Current flow: `scenario JSON → parse_scenario → Pyomo model → HiGHS → Python arithmetic validator → JSON/CSV/SVG`.

- `core.py`: 输入契约、模型构建、求解状态、独立数值校验。 / Input contract, model, solver status, independent arithmetic check.
- `cli.py`: 文件读写、基线运行、导出；无网络和设备命令。 / File I/O, baseline and exports; no network or device commands.
- v0.3.5 CLI 导出边界：预检目标路径与输入别名，默认拒绝同名文件；暂存完整导出后以独占创建发布，显式覆盖时备份旧文件并在普通失败时回滚。四文件无法保证对并发读者的原子快照。 / The CLI stages a complete export set, refuses existing targets by default, and uses best-effort rollback for explicit overwrite; readers cannot assume an atomic four-file snapshot.
- `examples/`: 合成案例；不含真实学校数据。 / Synthetic examples only.
- `tests/`: 行为与失败模式测试。 / Behavior and failure tests.

目前无后台服务、数据库、前端、实时监控或硬件适配器。将来设备数据接口须与离线优化内核隔离，并另行设计认证、授权及只读边界。 / No backend, database, frontend, live monitoring, or hardware adapter exists. Future data adapters require separate access and safety design.

## v0.1.6 审计边界 / Audit boundary

`environment.py` 只采集当前 Python、OS 与包版本；`cli.py` 将其与 Git/场景信息写入 `summary.json.run`。`tools/audit_exports.py` 为标准库独立审计入口，严格匹配当前检出源码和版本约束，再逐时核算四个导出文件。v0.1.6 时历史版本的只读隔离方案仅有[设计](historical-audit-design.md)。 / The environment collector records local metadata; the standard-library auditor validates the current checkout and export arithmetic. Historical audit was still a design in v0.1.6.

v0.1.7 `tools/audit_history.py` 增加一个独立、只读的历史入口：先验证指定 Git 提交、`run-v015` schema、模型/约束/依赖来源；再在临时 detached worktree 中运行固定哈希的历史标准库审计器；当前上下文单独核对，不借用物理检查结果。四个输入/结果原件前后均计算 SHA-256，工作树正常退出后移除。 / The history prototype binds one schema and reviewed auditor to a detached worktree, reports three independent results, and hashes original artifacts before and after.

v0.1.8 在历史入口增加四文件字节快照：历史标准库审计器读取快照，原件在审计前后重新散列；检出时以命令局部 Git 设置禁用钩子和 fsmonitor，并继续核对检出的审计器及锁文件字节。临时工作树不是恶意仓库沙箱；`--repo` 仅面向已审查项目和受控合成临时仓库。 / The historical auditor reads stable snapshots; Git checkout hooks and fsmonitor are disabled for that invocation. Arbitrary untrusted repositories remain out of scope.

v0.1.9 当前模型源码不可读时，`current_context=UNKNOWN` 并保留定位信息；历史上下文通过独立预检后仍可单独执行固定摘要的历史审计器。其余安全边界与纯电费模型均不变。 / An unavailable current model source no longer aborts an otherwise independent historical audit; the current verdict remains unknown.

## v0.2.0 离线数据层 / Offline data layer

v0.2.0 当时新增独立路径 `合成或明确提供的 CSV → data.import_energy_csv → 质量报告 + 完整有效时序 → prepare_visualization_data(既有导出汇总) → 图表数据契约`。`data.py` 只使用标准库，既不调用 Pyomo，也不读取设备或网络。质量不通过时不给下游部分时序；图表准备要求优化与基线的时间、输入回显和三个汇总指标一致。当时 CSV 尚未自动生成优化场景，后续桌面集成见下文。 / v0.2.0 introduced the bounded CSV quality and chart-data path before the later desktop integration described below.

数据层设计和开源参考比较见[方案](data-layer-visualization-plan.md)。OpenEMS、Emoncms 和 PyPSA 只作为设计参考，未安装或复制其代码。 / External projects are design references only.

v0.2.0 验收复核：CSV 入口为只读，质量报告与完整时序分离；文件、行、时段、数值 token 均有上限。图表准备只消费已通过质量检查的时序及既有优化/基线汇总，不触发求解、网络或设备操作。相同场景要求逐行时间和输入回显一致，成本、购电量、峰值分别计算。 / Acceptance review confirms bounded read-only import and a separate chart-preparation path; no solver or live integration is invoked.

## v0.3.0 本机界面 / Local desktop

`desktop.py (Tkinter) → data.import_energy_csv → dashboard.build_scenario → cli.run (既有 Pyomo + HiGHS) → audit_exports.py (独立子进程) → data.prepare_visualization_data → Tkinter Canvas`。质量失败时不求解；独立审计失败时不绘图。临时运行文件自动清理，原始 CSV 不写入。合成设备参数沿用 `examples/synthetic-day.json`；交互式求解上限 96 时段。Tkinter 仅呈现既有指标和时序，不实现新的目标函数或指标公式。 / The desktop is an offline orchestration and presentation layer over existing validation, solver, audit and chart-preparation interfaces.

v0.3.1：质量检查和对比调度由后台工作线程执行；线程只传结果到队列，所有 Tk 控件更新留在主线程。求解 CLI 与审计器均为可取消、限时的子进程；换输入与关闭窗口设置取消事件，过期队列结果按代次丢弃。任一环节失败即清空卡片和图表，临时目录由上下文管理器清理。 / Background workers pass results through a queue; Tk updates stay on the main thread. Cancel events and generation IDs prevent stale results, while bounded subprocesses and a temporary-directory context contain solver and audit work.

v0.3.2：仅调整 Tk 呈现层。页面由可滚动视口容纳内容，图表保留可读高度；页签在宽度不足时使用短标题，完整双语标题仍在图内。指标卡按视口宽度在三、二或一列间重排。图表时间标签分行显示日期、时刻与 UTC 偏移。 / Presentation changes keep charts reachable and metric cards readable as the window or Tk font scaling changes; model, data preparation and audit remain separate.

v0.3.4：桌面入口沿用 v0.3.3 的有条件 Windows DPI 感知设置；取消按钮与 Esc 仅设置后台取消事件。UI 等待后台线程及子进程结束后再恢复按钮，取消后的结果不进入图表。关闭窗口时等待当前及输入切换后尚未完成的后台任务把完成事件送回主线程，再销毁 Tk 窗口；期间不再显示结果。子进程关闭仍由 `dashboard._run_command` 的 `finally` 负责，运行文件仍由 `TemporaryDirectory` 管理。模型、数据层和审计器未变。 / Keyboard cancellation and window closing wait for current and stale worker cleanup before finishing; the optimizer, data layer and auditors are unchanged.

## v0.3.7 运行结果契约 / Verified run artifact contract

可选路径 `run_artifacts.publish_run → 既有 cli 子进程 → 既有独立 audit_exports.py 子进程 → manifest 校验 → COMPLETE`。每次新建一个运行目录；`read_verified_run` 只返回完成标记、固定文件集合、大小、哈希与元数据全部通过校验的字节。旧 CLI 与桌面临时运行路径不变。目录在生成期间可见，协作读者必须通过安全读取接口；不承诺断电恢复或恶意本机写入者防护。 / The opt-in publisher uses the unchanged solver CLI and independent auditor. The verified reader returns checked bytes. Existing CLI and desktop workflows remain intact; power-loss and adversarial-writer guarantees are out of scope. See [the full contract](run-artifact-contract.md).

## v0.3.8 桌面集成 / Desktop integration

桌面后台链路为 `data.import_energy_csv → dashboard.build_scenario → run_artifacts.publish_run（既有 CLI 求解、独立审计、清单与完成标记）→ read_verified_run → data.prepare_visualization_data → Tk 主线程`。图表层只接收安全读取接口返回的 `summary.json` 字节；读取历史目录时重新验清单与哈希，并用当前已检查 CSV 逐时核对输入。运行历史限量枚举并区分有效、未完成、损坏；不会自动删除目录。临时场景 JSON 仍由上下文管理器清理，已发布目录持久保存于用户指定的本地根目录。取消或关闭仍等待后台任务；进程取消后可能留下明确标记为未完成的目录供检查。 / The desktop now publishes and rereads verified bundles before chart preparation. Historical selection revalidates the bundle and matches it to the currently checked CSV. Temporary scenario files are cleaned up; completed and incomplete run directories are retained for inspection. The model and independent audit contract are unchanged.

## v0.3.9 运行历史与读取复核 / History and read hardening

新增标准库 `run_storage.py`，仅在用户显式保存时写入本地运行根目录偏好；启动时读取有界设置，恢复目录并异步刷新历史，失效时提示并使用原临时默认目录。`read_verified_run` 在完成五文件校验后再次检查运行目录身份、修改时间与精确条目集合，以拒绝验证期间可观察的目录变动。Tk 诊断脚本将 Tcl 初始化纳入异常捕获，并限制次数、每次超时及 JSON 输出大小。上述检查不能抵御可同时伪造清单的恶意本机写入者，也不提供断电持久性。/ An explicit local preference restores the selected root after restart. The reader now rechecks visible directory changes at the end of verification. The bounded Tk probe records initialization failures instead of losing its JSON result; adversarial-writer and power-loss guarantees remain out of scope.

## v0.3.12 桌面削峰接入 / Desktop peak-cap integration

Tk 主线程持有模式单选项、上限输入和结果策略栏；后台调用仍为 `dashboard.run_comparison → run_artifacts.publish_run → 独立 audit_exports.py → read_verified_run → prepare_visualization_data`。纯电费模式传入 `None`，峰值模式传入显式 kW 数值。运行中模式控件禁用；取消、关闭、代次隔离和错误队列沿用原生命周期。历史条目从已校验清单读取模式与上限，选择后再次安全读取并恢复显示。相对纯电费的成本只在同场景、同求解器、同模型源码摘要的已校验运行存在时计算；该差值不改变三项指标或模型目标。/ Policy selection remains a Tk presentation concern; solving, independent audit, verified publication and chart preparation stay in their existing layers. A compatible verified cost-only run is required for the optional cost delta.

## v0.3.13 自定义 CSV 体验 / Custom CSV experience

`data.import_energy_csv` 将通过校验的时间戳规范化为 `datetime.isoformat()`，因此 `Z`、`+0000` 与 `+00:00` 等同一合法偏移表示进入相同的后续场景表示；绝对时间、顺序和间隔不变，原文件保持只读。`data.summarize_energy_input` 仅消费完整有效的 `ImportResult`，用 `Decimal` 计算功率/价格范围和 `Σ功率×step_hours` 电量；失败导入没有摘要。Tk 只格式化并显示该结果，不重复业务计算。/ Valid timestamp spellings are canonicalized in memory. The data layer, rather than Tk, calculates input ranges and load/PV energy from complete validated records.

自定义 CSV 只提供时间戳、负荷、可用光伏和电价。`dashboard.build_scenario` 继续从 `examples/synthetic-day.json` 取得电网和电池/SOC 参数，模型、审计器及运行产物格式不变。桌面质量检查最多接受数据层的 50,000 时段，但交互求解仍限 96 时段；超限数据可查看质量和摘要，不进入求解。运行目录保存规范化 `scenario.json` 而不是原始 CSV 字节，其哈希证明文件一致性，不证明输入真实性。/ Custom CSV data replaces only the time series. Equipment assumptions remain synthetic, desktop solving remains limited to 96 periods, and verified bundles preserve the normalized scenario rather than the source CSV bytes.

## v0.3.14 自定义设备参数 / Custom device parameters

新增标准库模块 `device.py`，将电网功率、容量、初末 kWh、充放电功率和效率作为冻结结构集中校验。默认结构从合成模板解析；GUI 文本经同一公共解析器转成结构后传给 `dashboard.build_scenario`，从而真正进入既有 `core.parse_scenario` 和 Pyomo 约束。模型、CLI、审计器及产物格式均未改。/ A small standard-library module validates the existing equipment constants. The GUI passes that object through scenario construction into the unchanged model constraints.

完整链路为 `CSV ImportResult + DeviceParameters → build_scenario → publish_run → audit_exports.py → read_verified_run → DeviceParameters.from_scenario + 图表`。设备变化只使结果和所选历史失效，不丢弃已经通过的 CSV 质量报告。选择历史运行时，从安全读取返回的 `scenario.json` 恢复该运行实际参数；同输入成本参考仍要求完整场景 SHA-256 相同，因此不同设备参数不会被错误比较。/ Equipment edits invalidate stale outputs while preserving CSV quality. Historical values are restored from verified scenario bytes, and cost-reference matching remains bound to the full scenario digest.
