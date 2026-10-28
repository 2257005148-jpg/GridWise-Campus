# 历史运行公平对比 / Fair Historical-Run Comparison

## 用途 / Purpose

桌面端可选择同一运行根目录下两个 `VALID` 运行，比较电费（CNY）、购电量（kWh）和购电峰值（kW）。差值统一为 `B − A`；正负号只描述该指标的增减，电费下降不自动表示削峰或节能。/ The desktop compares cost, grid energy and grid peak for two verified runs. Every delta is `B − A`; no metric is used as a proxy for another.

## 验证顺序 / Verification order

1. A、B 分别通过 `read_verified_run()` 的完成标记、清单、大小和 SHA-256 校验。/ Each bundle passes the existing verified reader.
2. 两份已校验汇总分别与当前已检查 CSV 逐行对齐，并由数据层重算指标。/ Each summary is aligned to the current checked CSV and recalculated by the data layer.
3. 比较场景原始字节摘要、求解器名称、模型源码摘要、时间序列、时间步、设备参数和无储能基线。/ Fair-context fields are matched.
4. 全部通过后才返回并显示完整比较；没有部分成功。/ The controller returns no partial result.

优化模式和峰值上限是允许不同的策略变量。相同运行标识被拒绝。`VERIFIED` 只表示文件完整性检查通过，`MATCHED` 只表示公平条件通过；二者都不证明输入真实，也不证明某项指标一定改善。/ Optimization mode and cap may differ. Integrity and fairness statuses are separate and neither proves source truth or improvement.

## 操作 / Use

先选择并检查当前 CSV，再刷新运行历史。仅 `VALID` 条目可用于 A/B。选择两个不同运行后点击“公平对比 / Compare”。输入 CSV、时间步、设备参数或运行根目录改变会立即清除旧比较。损坏或未完成目录不会被自动修复或删除。/ Check the current CSV, refresh history, choose two distinct valid runs, then compare. Context changes invalidate the table.

## 边界 / Limits

- 文件哈希检测内容变化，不证明输入来源可信。/ Hashes detect change, not truth.
- 对比不重新求解，也不改变纯电费或峰值上限模型。/ Comparison does not solve again or change either model.
- 当前接口只比较同一运行根目录中的已验证本地运行。/ Only verified local runs under the selected root are listed.
- 对恶意本机写入者、断电持久性和跨平台 GUI 行为的限制沿用运行产物契约。/ Existing artifact-contract limitations still apply.
