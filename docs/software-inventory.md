# 软件清单 / Software inventory

本机核验于 2026-10-25。/ Local verification on 2026-10-25.

| 软件 / Software | 版本 / Version | 用途 / Purpose | 状态 / Status | 许可证 / License |
|---|---|---|---|---|
| Python | 3.12.14 (project venv); 3.14.7 (other local install) | 运行时 / runtime | 已安装 / installed | PSF |
| Pyomo | 6.9.5 | 数学模型 / modeling | 已装入 D 盘项目环境 / installed in D venv | BSD |
| highspy / HiGHS | 1.15.1 | MIP 求解 / solver | 已装入 D 盘项目环境 / installed | MIT |
| pytest | 8.4.2 | 自动测试 / tests | 已装入 D 盘项目环境 / installed | MIT |
| PyYAML | 6.0.3 | 本机 Skill 格式校验工具依赖 / local skill-validator dependency | 已装入 D 盘项目环境，非项目运行依赖 / installed, not runtime dependency | MIT |
| gridwise-campus | 0.1.0 | 当前项目可编辑安装 / editable local package | 已装入 D 盘项目环境 / installed | 项目许可证待定 / undecided |
| Git | 2.53.0.3 | 本地版本管理 / local version control | 已安装 / installed | GPL-2.0-only |
| pandas, Streamlit, Plotly | — | 后续数据与界面 / future work | 当前环境未安装 / not installed | 引入前复核 / review before adoption |

实际 venv: `[LOCAL_ENV_ROOT]\gridwise-campus-py312`。依赖在 `pyproject.toml` 锁定直接版本，间接依赖未锁定；发布前需要完整锁文件和许可证审查。 / Direct dependencies are pinned; transitive dependencies and release license review remain open.

## MIT 适用性与依赖许可复核 / MIT suitability and dependency licenses

截至 2026-10-25，仓库没有 `LICENSE`，因此项目代码尚未以 MIT 或其他开源许可证授权。对原创代码采用 MIT 在技术上与目前检查到的依赖许可没有明显冲突，但须由有权授权的著作权人确定版权主体并批准许可证文本。[OSI 的 MIT 文本](https://opensource.org/license/mit)要求再分发时保留版权和许可声明。 / No project license is granted yet. MIT appears compatible with the inspected dependency terms, subject to authorization by the copyright holder and notice retention.

运行依赖 / Runtime dependencies: Pyomo 6.9.5 为 BSD-3-Clause；其依赖 PLY 3.11 的已安装源文件头为 BSD-3-Clause。highspy 1.15.1 的已安装许可证为 MIT，其 `THIRD_PARTY_NOTICES.md` 列出 BSD-3、Apache-2.0、zlib 和 MIT 的附加组件；NumPy 2.5.4 的元数据为 `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`。本仓库未复制或打包这些依赖文件。 / The installed runtime licenses and notices were inspected; dependencies are not vendored here.

测试与本机校验依赖 / Test and local validation dependencies: pytest 8.4.2、pluggy 1.7.0、iniconfig 2.3.1、PyYAML 6.0.3 为 MIT；packaging 26.3 为 Apache-2.0 OR BSD-2-Clause；colorama 0.4.6 已安装许可证为 BSD-3-Clause；Pygments 2.21.0 为 BSD-2-Clause。PyPI 元数据不是独立法律审计，若将来随应用分发第三方 wheel，应随对应包保留其许可证与第三方声明，重新核对精确构建产物。 / Package metadata and installed texts are evidence for this review, not a release audit; redistributing wheels requires retaining their notices and reviewing the exact artifacts.

参考 / References: [Pyomo](https://pypi.org/project/pyomo/6.9.5/), [HiGHS Python](https://pypi.org/project/highspy/1.15.1/), [NumPy](https://pypi.org/project/numpy/2.5.4/), [pytest](https://pypi.org/project/pytest/8.4.2/), [PLY](https://pypi.org/project/ply/3.11/).
