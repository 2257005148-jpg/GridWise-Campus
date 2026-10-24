# 需求 / Requirements

状态定义 / Status: **已实现 implemented**, **已测试 tested**, **计划中 planned**, **未验证 unverified**。

| 能力 / Capability | 状态 / Status | 验收依据 / Acceptance |
|---|---|---|
| 合成 JSON 时序输入、单位和间隔校验 / synthetic input | 已测试 / tested | `tests/test_core.py` |
| 多时段经济调度、无储能基线 / dispatch and baseline | 已测试 / tested | 模型结果、独立校验、边界测试 / model and independent checks |
| CSV、JSON、SVG 输出 / exports | 已测试 / tested | 生成文件与内容检查 / generated artifacts |
| 真实校园数据导入、清洗、权限 / campus data | 计划中 / planned | 授权、隐私和质量方案先行 / authorization first |
| 负荷预测、潮流、节能基线 / forecasting, power flow, savings baseline | 计划中 / planned | 独立方法与验证集 / independent validation |
| Streamlit 界面、服务 API、数据库 / UI, API, database | 计划中 / planned | 契约、安全和端到端测试 / contracts and tests |

非功能要求 / Nonfunctional: 可重复安装、明确单位与时区、失败时不输出伪结果、离线运行、无设备控制；当前仅在本机 Python 3.12 上验证，跨平台尚未验证。 / Reproducible setup, explicit units and timezone, fail closed on invalid solutions, offline execution, no device control; only local Python 3.12 has been checked.
