# 架构 / Architecture

当前数据流 / Current flow: `scenario JSON → parse_scenario → Pyomo model → HiGHS → Python arithmetic validator → JSON/CSV/SVG`.

- `core.py`: 输入契约、模型构建、求解状态、独立数值校验。 / Input contract, model, solver status, independent arithmetic check.
- `cli.py`: 文件读写、基线运行、导出；无网络和设备命令。 / File I/O, baseline and exports; no network or device commands.
- `examples/`: 合成案例；不含真实学校数据。 / Synthetic examples only.
- `tests/`: 行为与失败模式测试。 / Behavior and failure tests.

目前无后台服务、数据库、前端、实时监控或硬件适配器。将来设备数据接口须与离线优化内核隔离，并另行设计认证、授权及只读边界。 / No backend, database, frontend, live monitoring, or hardware adapter exists. Future data adapters require separate access and safety design.

## v0.1.6 审计边界 / Audit boundary

`environment.py` 只采集当前 Python、OS 与包版本；`cli.py` 将其与 Git/场景信息写入 `summary.json.run`。`tools/audit_exports.py` 为标准库独立审计入口，严格匹配当前检出源码和版本约束，再逐时核算四个导出文件。v0.1.6 时历史版本的只读隔离方案仅有[设计](historical-audit-design.md)。 / The environment collector records local metadata; the standard-library auditor validates the current checkout and export arithmetic. Historical audit was still a design in v0.1.6.

v0.1.7 `tools/audit_history.py` 增加一个独立、只读的历史入口：先验证指定 Git 提交、`run-v015` schema、模型/约束/依赖来源；再在临时 detached worktree 中运行固定哈希的历史标准库审计器；当前上下文单独核对，不借用物理检查结果。四个输入/结果原件前后均计算 SHA-256，工作树正常退出后移除。 / The history prototype binds one schema and reviewed auditor to a detached worktree, reports three independent results, and hashes original artifacts before and after.

v0.1.8 在历史入口增加四文件字节快照：历史标准库审计器读取快照，原件在审计前后重新散列；检出时以命令局部 Git 设置禁用钩子和 fsmonitor，并继续核对检出的审计器及锁文件字节。临时工作树不是恶意仓库沙箱；`--repo` 仅面向已审查项目和受控合成临时仓库。 / The historical auditor reads stable snapshots; Git checkout hooks and fsmonitor are disabled for that invocation. Arbitrary untrusted repositories remain out of scope.

v0.1.9 当前模型源码不可读时，`current_context=UNKNOWN` 并保留定位信息；历史上下文通过独立预检后仍可单独执行固定摘要的历史审计器。其余安全边界与纯电费模型均不变。 / An unavailable current model source no longer aborts an otherwise independent historical audit; the current verdict remains unknown.
