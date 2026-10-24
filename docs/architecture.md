# 架构 / Architecture

当前数据流 / Current flow: `scenario JSON → parse_scenario → Pyomo model → HiGHS → Python arithmetic validator → JSON/CSV/SVG`.

- `core.py`: 输入契约、模型构建、求解状态、独立数值校验。 / Input contract, model, solver status, independent arithmetic check.
- `cli.py`: 文件读写、基线运行、导出；无网络和设备命令。 / File I/O, baseline and exports; no network or device commands.
- `examples/`: 合成案例；不含真实学校数据。 / Synthetic examples only.
- `tests/`: 行为与失败模式测试。 / Behavior and failure tests.

目前无后台服务、数据库、前端、实时监控或硬件适配器。将来设备数据接口须与离线优化内核隔离，并另行设计认证、授权及只读边界。 / No backend, database, frontend, live monitoring, or hardware adapter exists. Future data adapters require separate access and safety design.
