# HTML 报告导出 / HTML report export

## 范围 / Scope

v0.3.20 提供两种离线、自包含、UTF-8 HTML 报告：一个已验证历史运行的单次报告，以及两个公平历史运行的 A/B 对比报告。报告不调用新的优化算法，不重新计算电费、购电量或峰值，也不改变运行产物、独立审计器或数学模型。/ v0.3.20 exports a self-contained UTF-8 HTML report for one verified run or one fair A/B comparison. It does not introduce a solver path, metric implementation, artifact format or audit rule.

报告保留指标卡和表格，并增加三张内联 SVG 时序图。报告可由浏览器离线打开；没有脚本、远程字体、CDN 或网络请求。/ Reports retain their metric cards and tables and add three inline SVG timelines. They contain no scripts, remote fonts, CDN assets or network calls.

## 验证入口 / Verification inputs

单次报告必须重新执行 `dashboard.load_verified_comparison()`。该接口通过 `read_verified_run()` 校验完成标记、清单、大小和 SHA-256，然后把已校验汇总与当前质量通过的 CSV 逐行对齐并重算三项指标。报告格式化返回的 `DashboardResult`。/ A single-run report rereads the verified bundle and aligns it with the currently checked CSV before formatting its `DashboardResult`.

A/B 报告必须重新执行 `dashboard.compare_verified_runs()`。两个运行分别安全读取和对齐；场景原始字节摘要、求解器、模型源码摘要、时间序列、时间步、设备参数及无储能基线均匹配后，才格式化 `HistoricalRunComparison`。优化模式和峰值上限是允许不同的策略变量。状态必须为 `VERIFIED` 和 `MATCHED`；任一失败都不生成部分报告。/ An A/B report reruns the complete fair-comparison contract and accepts only `VERIFIED` and `MATCHED` results.

## 内容 / Contents

两种报告都包含：

- 报告类型、带时区生成时间；
- 当前输入来源、起止时间、时段数及小时步长；
- 优化模式、适用的峰值上限和运行标识；
- 电网功率、电池容量、充放电功率、初末 SOC 能量和效率；
- 电费 CNY、购电量 kWh、购电峰值 kW；
- 完整性状态，以及 A/B 报告的公平状态和 `B − A`；
- 已验证的购电功率、累计电费和电池 SOC 时序；
- 指标公式、单位和适用限制。

Both report types include the input context, policy, equipment parameters, verified metrics, units, timelines and limitations. The comparison report additionally includes the signed `B − A` and fair-comparison status.

单次报告的购电功率和累计电费同时显示优化方案与无储能基线，SOC 显示优化方案。A/B 报告显示运行 A 与 B 的优化购电功率、累计电费和 SOC。当前图表数据没有独立充/放电功率序列，因此报告不编造这些曲线。横轴为时段编号，纵轴明确标注 `kW`、`CNY` 或 `kWh`。/ Single-run charts compare optimized and baseline grid power and cumulative cost, plus optimized SOC. A/B charts compare the two verified strategies. Charge/discharge series are not available in the current chart contract and are not fabricated.

为避免长序列标签拥挤，横轴只标注起点、中点和终点时段；极大值及小于 `0.01` 的非零纵轴刻度使用紧凑科学计数法。图表保留 `680px` 最小可读宽度，窄窗口在图表区域内横向滚动，不压缩图例和刻度文字。该规则只改变显示，不改变曲线点或指标。/ The period axis labels only its start, midpoint and end. Extreme axis values use compact scientific notation, and narrow viewports scroll the chart at a readable minimum width rather than shrinking labels into illegibility.

电费、购电量和峰值始终分开解释。电费降低不自动证明削峰或节能；购电量改变也不等于现场节能效果。哈希只检测所读内容变化，不能证明数据来源真实、授权或可信。报告不证明真实校园设备行为、跨平台兼容或求解结果以外的外部效果。/ Cost, energy and peak remain independent. Hashes detect byte changes but do not prove source truth, authorization or real-device behavior.

## 桌面操作 / Desktop workflow

1. 导入 CSV，设置步长和设备参数，完成质量检查。
2. 刷新运行历史并选择一个 `VALID` 运行。此时可点击“导出所选运行报告 / Export selected run”。
3. 若需要 A/B 报告，选择不同的 A、B 运行并完成比较；只有状态为 `VERIFIED / MATCHED` 时可点击“导出 A/B 报告 / Export A/B report”。
4. 在保存对话框中选择一个**尚不存在**的绝对 `.html` 文件。
5. 后台重新验证并发布后，状态区显示最终路径；取消或失败时不显示成功。

Changing the input CSV, step, equipment parameters, runs root or A/B selection invalidates the corresponding export eligibility. Loading any historical run clears the previous A/B result before restoring that run's equipment values.

## 文件发布边界 / File publication boundary

完整文档先在内存中生成，所有动态字符串用 HTML 转义。写入接口拒绝空/超限/非 UTF-8/不完整文档、相对路径、非 `.html` 后缀、目录、符号链接、junction 和不可用父目录。默认不覆盖已有文件；桌面没有覆盖开关。/ The complete document is rendered and escaped in memory. Publication rejects malformed payloads and unsuitable paths, and the desktop never requests overwrite.

内容写入目标目录内随机命名的私有临时文件，随后刷新、`fsync` 并以 `os.replace` 发布。普通失败删除本次临时文件；若清理也失败，错误同时包含原始异常、清理异常和残留文件绝对路径，并保留原始异常链。已有目标在已覆盖模式的失败路径保持原字节；桌面默认不会进入覆盖模式。/ A same-directory private temporary is fully written and synced before replacement. Cleanup failure reports both errors and the residual absolute path without masking the original cause.

桌面为报告任务使用专用 `ReportCancellation` 门闩。临时文件同步完成后，取消请求与最终 `os.replace` 竞争同一个锁：取消先获得锁时，替换不执行且临时文件清理；发布先获得锁并完成替换时，后续取消返回未接受，桌面等待并显示实际成功结果。普通求解、审计和历史任务的取消模型不变。/ A report-specific gate linearizes cancellation with the irreversible replacement. Cancellation wins before commit, or a committed publication wins and a late cancellation is explicitly not accepted; the existing worker model is unchanged.

该边界提供单文件正常失败下的明确行为，不声称抵御恶意本机写入者、检查与替换之间的竞争、存储设备丢失或断电。重要报告应在生成后打开核对并纳入用户自己的备份流程。/ This is a normal-use single-file safety boundary, not a guarantee against hostile local writers, race attacks or power loss.

## 未实现 / Not implemented

- 负荷/PV、逐时电费及独立充/放电功率曲线；
- PDF、Word 或联网发布；
- 自动覆盖、自动清理或历史报告数据库；
- 电子签名、真实性证明或发布授权。

The project license remains undecided. A generated local report is not a software release or evidence that the repository may be published.
