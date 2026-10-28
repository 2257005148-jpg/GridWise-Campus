# 运行失败诊断 / Run Failure Diagnostics

## 范围 / Scope

v0.3.17 在本地桌面工作流中增加只读、不可变的失败诊断。诊断只整理当前异常类型、执行阶段、子进程退出证据和可信的求解终止状态；它不会修改 CSV、电网功率、电池容量、SOC、效率、峰值上限或求解约束。/ v0.3.17 adds immutable, display-only failure diagnostics to the local desktop workflow. Diagnostics summarize available exception, stage, subprocess and trusted termination evidence; they never change inputs, equipment values or model constraints.

诊断不是根因预测器。证据不足、CLI 输出不符合严格格式或异常来自未知阶段时，类别必须为 `UNKNOWN`。建议只是人工排查方向，不能解释为自动修复或已证明的根因。/ Diagnostics do not predict root cause. Insufficient or ambiguous evidence produces `UNKNOWN`; suggestions are manual checks, not repairs or causal claims.

## 类别 / Categories

- `INPUT_INVALID`: 已确认的输入或参数校验阶段失败；
- `INFEASIBLE`: 求解阶段明确记录 `termination_condition=infeasible`；
- `SOLVER_UNAVAILABLE`: 可信求解阶段记录标准求解器不可用错误；
- `SOLVER_TIMEOUT`: 外部进程监控器在明确的求解阶段超时；
- `SOLVER_ERROR`: 求解器异常或其他明确的非最优终止；
- `CANCELLED`: 结构化运行错误记录了取消原因；
- `AUDIT_FAILED`: 独立审计阶段失败；
- `ARTIFACT_INVALID`: 清单、哈希、完成标记或安全读取失败；
- `UNKNOWN`: 证据不足或诊断自身无法安全读取异常。

Current HiGHS behavior for the synthetic infeasible probe is `solver_status=error` with `termination_condition=infeasible`. Classification therefore uses the termination condition rather than treating the word `error` alone as a backend crash.

## 证据与异常链 / Evidence and exception chain

`RunDiagnostic` 保存原始异常对象、最多 16 层异常链的安全文本、阶段、原因，以及可用的 solver status/termination condition。若异常的 `__str__` 自身失败，诊断返回 `UNKNOWN` 并保留原始异常引用；诊断故障不得覆盖运行故障。/ The immutable diagnostic retains the original exception, bounded cause-chain text, stage/reason and optional solver status. Formatting failures degrade to `UNKNOWN` without replacing the run error.

`RunArtifactError` 的阶段与原因只存在于进程内异常对象，不写入 `summary.json`、manifest 或运行目录，因此 v0.3.7 运行产物格式没有变化。CLI 和独立审计器也未改变。/ Stage and reason are in-memory exception metadata only. They do not alter the run bundle, CLI or auditor contract.

## 桌面状态一致性 / Desktop state consistency

桌面在既有工作线程和队列外增加一个小型任务门闩：

1. 任务仍在运行时，取消先取得锁并设置原有取消事件；
2. 工作线程完成并把结果入队时，门闩先记录完成；此后的取消返回未接受，UI 等待并展示真实结果；
3. 成功结果已经显示后，取消入口没有活动任务，保持当前成功状态。

输入、时间步、设备参数、策略或运行目录变化仍通过 generation 丢弃旧任务结果。失败会清除图表、指标、当前运行选择和单次报告导出资格，但不会删除历史运行目录。/ Existing generation isolation remains authoritative. Failures clear current presentation and export eligibility without deleting historical runs.

## v0.3.18 桌面诊断面板 / Desktop diagnosis panel

桌面复用同一个 `RunDiagnostic`，不重新分类错误。紧凑摘要显示类别、双语说明、执行阶段、可用的求解器/终止状态证据和人工检查建议；按“显示详情 / Show details”可展开一个可聚焦、可选择、可滚动的只读文本区，包含全部原始证据、安全格式化的异常链和完整建议。“复制详情 / Copy details”复制同一文本；剪贴板失败会写入既有日志并保留当前诊断。/ The desktop formats the existing immutable diagnosis rather than reclassifying errors. Its compact view includes trusted solver evidence when available; the expandable read-only view exposes the full evidence and cause chain, and a clipboard failure never discards the diagnosis.

`UNKNOWN` 摘要固定补充“证据不足，无法确定根因”。新任务开始、成功结束，或 CSV、步长、设备参数、优化模式、峰值上限、运行根目录变化时，旧诊断清除。用户取消只显示取消状态，不创建虚假的求解失败诊断；generation 不匹配的迟到结果仍被丢弃。原始错误继续写入原日志。/ Unknown diagnoses state the evidence limit explicitly. Context changes and new or successful jobs clear stale diagnoses, while cancellation and stale generations cannot repopulate a false failure.

## 明确边界 / Explicit limitations

- 字符串中偶然出现 “infeasible” 不足以分类；必须有可信阶段和严格终止格式。
- `SOLVER_TIMEOUT` 是本地子进程监控超时，不等同于 HiGHS 自身的 time-limit termination。
- `UNKNOWN` 需要保留日志后人工调查，不能自动调整参数重试。
- 诊断通过不证明输入真实、结果最优或运行产物完整；成功结果仍必须经过现有独立审计和安全读取。
- 本功能不连接真实校园系统或物理设备。
