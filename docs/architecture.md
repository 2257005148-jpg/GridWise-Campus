# 架构 / Architecture

当前数据流 / Current flow: `scenario JSON → parse_scenario → Pyomo model → HiGHS → Python arithmetic validator → JSON/CSV/SVG`.

- `core.py`: 输入契约、模型构建、求解状态、独立数值校验。 / Input contract, model, solver status, independent arithmetic check.
- `cli.py`: 文件读写、基线运行、导出；无网络和设备命令。 / File I/O, baseline and exports; no network or device commands.
- `examples/`: 合成案例；不含真实学校数据。 / Synthetic examples only.
- `tests/`: 行为与失败模式测试。 / Behavior and failure tests.

目前无后台服务、数据库、前端、实时监控或硬件适配器。将来设备数据接口须与离线优化内核隔离，并另行设计认证、授权及只读边界。 / No backend, database, frontend, live monitoring, or hardware adapter exists. Future data adapters require separate access and safety design.

## v0.1.6 审计边界 / Audit boundary

`environment.py` 只采集当前 Python、OS 与包版本；`cli.py` 将其与 Git/场景信息写入 `summary.json.run`。`tools/audit_exports.py` 为标准库独立审计入口，严格匹配当前检出源码和版本约束，再逐时核算四个导出文件。历史版本的只读隔离方案见 [历史审计设计](historical-audit-design.md)；本阶段没有历史审计执行入口。 / The environment collector records local metadata; the standard-library auditor validates the current checkout and export arithmetic. The linked historical design has no executable mode yet.
