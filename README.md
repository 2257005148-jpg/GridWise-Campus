# 长理智慧能源管理系统 / GridWise Campus

面向高校的通用能源分析与优化项目。现有离线原型支持合成数据的纯电费调度、无储能基线，以及 v0.2.0 阶段新增的 CSV 质量检查和图表数据准备；尚无交互式界面、学校数据接口、实时控制或认证声明。 / The offline prototype now includes cost-only dispatch, a no-storage baseline, CSV quality checks and chart-data preparation. It has no interactive UI, campus connection or control claim.

## 已实现 / Implemented

- JSON 数据及单位、时间间隔校验 / JSON input validation, units and time spacing.
- 多时段光伏、购电、储能混合整数优化 / Multi-period PV, grid and battery mixed-integer optimization.
- 同输入无储能基线、独立结果校验 / Same-input no-storage baseline and independent result validation.
- JSON、CSV 和 SVG 导出 / JSON, CSV and SVG export.
- 只读 CSV 导入、缺失值与时间完整性报告、图表数据准备 / Read-only CSV quality reporting and chart-data preparation.

## 安装与运行 / Install and run

Python 3.12+。建议使用独立虚拟环境；以下命令不会连接学校系统。 / Python 3.12+ and a dedicated virtual environment are recommended. These commands do not connect to campus systems.

```powershell
py -3.12 -m venv [LOCAL_ENV_ROOT]\gridwise-campus-py312
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pip install -e '.[test]'
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.cli examples\synthetic-day.json outputs\sample
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m gridwise.data examples\synthetic-day.csv --step-hours 1
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe tools\audit_exports.py examples\synthetic-day.json outputs\sample\optimized.csv outputs\sample\baseline.csv outputs\sample\summary.json
& [LOCAL_ENV_ROOT]\gridwise-campus-py312\Scripts\python.exe -m pytest -q
```

打开 `outputs/sample/grid-purchase.svg` 查看购电功率比较。输出目录已被 Git 忽略。 / Open that SVG to view grid purchase. The output directory is Git-ignored.

审计命令仅使用 Python 标准库读取原始输入和导出文件；退出码 0 为通过，2 为数据或计算校验失败。 / The audit command uses only the standard library to inspect the source and exports; exit 0 means pass, 2 means an audit failure.

## 范围与路线 / Scope and roadmap

后续阶段：多场景分析与可视化界面；负荷预测、配电网模型、受控接口与部署需另行设计和验证。所有能力须按测试和真实数据授权分别验收。 / Later stages may add scenarios and a UI; forecasting, network models, controlled interfaces and deployment require separate design and validation.

项目许可证尚未确定；请勿据此假定代码已获得开放源代码许可。 / The project license is undecided; do not assume this code has an open-source license yet.

详细契约见 `docs/`。 / See `docs/` for detailed contracts.
