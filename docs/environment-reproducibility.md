# 运行环境快照 / Execution environment snapshot (v0.1.5)

## 已声明、已解析、已验证 / Declared, resolved, verified

| 层次 / Layer | 内容 / Content | 证据边界 / Evidence limit |
|---|---|---|
| 声明 / Declared | `pyproject.toml`: Python `>=3.12`; Pyomo `6.9.5`; highspy `1.15.1`; test extra pytest `8.4.2` | Python range permits newer interpreters. / The Python range admits later versions. |
| 本机解析 / Locally resolved | `requirements/verified-win-py312.txt`: Pyomo 6.9.5, highspy 1.15.1, NumPy 2.5.4, PLY 3.11, pytest 8.4.2, colorama 0.4.6, iniconfig 2.3.1, packaging 26.3, pluggy 1.7.0, Pygments 2.21.0 | 从 Windows CPython 3.12.14 环境观察；不是带 wheel 哈希的跨平台锁。 / Observed on Windows CPython 3.12.14; not a cross-platform wheel-hash lock. |
| 本机验证 / Locally verified | `[LOCAL_ENV_ROOT]\gridwise-campus-py312`, `pip check`, 合成求解与独立审计、测试 / synthetic solve, independent audit and tests | 只对当前已安装环境成立。 / Applies only to this installed environment. |

PyYAML 6.0.3 和 pip 25.0.1 已安装，但不属于项目运行或测试依赖闭包，因此未加入版本约束文件。构建后端 `setuptools>=75` 仅在隔离构建环境声明；该临时环境的精确版本未捕获。 / PyYAML and pip are installed local tools outside the project dependency closure. The isolated build backend version was not captured.

## 复现步骤 / Reproduction

在独立 Python 3.12 环境中使用已有 pip： / In a separate Python 3.12 environment using pip:

```text
python -m pip install -e ".[test]" -c requirements/verified-win-py312.txt
python -m pip check
python -m pytest -ra -q
python -m gridwise.cli examples/synthetic-day.json outputs/reproduction
python tools/audit_exports.py examples/synthetic-day.json outputs/reproduction/optimized.csv outputs/reproduction/baseline.csv outputs/reproduction/summary.json
```

本阶段未执行全新环境安装，也未下载或核验 wheel 哈希。约束文件可阻止解析到其他版本，但无法单独保证可用 wheel、构建工具、二进制一致性、操作系统或求解器行为相同。CI 安装命令已加约束文件，但本阶段未实际运行托管 CI；其他平台的版本可安装性待验证。 / A fresh installation and artifact-hash verification were not performed. Exact constraints cannot guarantee binary identity or cross-platform availability. The CI install command now uses this file, but hosted CI was not run and other platforms remain unverified.

## 运行清单与审计 / Run manifest and audit

`summary.json.run` 记录 Python 版本、`platform.platform()` 系统字符串、`highspy` 分发版本、四个运行包的实际安装版本，以及版本约束文件的原始字节 SHA-256。无法读取的安装包版本或锁文件分别标为 `unavailable`；独立审计在无法读取锁文件时失败关闭。审计器用标准库读取这些字段，核对锁摘要、四个运行依赖的精确版本、求解器版本与 highspy 版本的一致性；不调用 Pyomo。 / The summary records observed versions and an exact byte digest of the constraints snapshot. The standard-library auditor checks the lock digest and runtime pins without invoking Pyomo.

`model_version`、`git_commit`、`source_state`、`scenario_sha256`、求解器名称和最优状态仍按既有规则记录。Git 状态范围新增 `requirements/`。提交、哈希和版本只提供关联线索，不证明输入真实、求解器确实执行或运行环境可信。 / The existing model, source and scenario identifiers remain. Git status now includes `requirements/`. These values are linkage evidence, not authenticity or trusted-execution proof.

## 历史结果审计设计 / Historical result audit design

当前公开审计命令只接受**当前检出版本**的模型源码、Git HEAD、源码状态与锁摘要；旧结果若与当前代码或汇总格式不一致，会明确失败。不能把对旧 CSV 的当前算术校验称作“历史代码通过”。 / The public auditor currently requires the current checkout. It rejects old exports when source, commit, lock or schema differ; current arithmetic alone is not historical-code validation.

拟议只读模式应要求用户提供完整历史提交 SHA，并从该提交读取 `src/gridwise/core.py`、`tools/audit_exports.py`、`requirements/` 与 `pyproject.toml` 的 Git 对象；校验结果汇总中的提交、模型版本、锁摘要和 schema 与所指定提交一致。然后在**独立临时工作树或受限隔离目录**执行该版本的审计器，并单独运行当前审计器：分别报告“历史上下文匹配”“历史版算术通过”“当前代码匹配/不匹配”。需先解决旧 schema 的显式版本映射、历史脚本执行隔离与临时目录清理；未获得这些保障前不自动执行历史代码，不检出到当前工作区，也不改写旧结果。 / A future read-only mode should bind a full commit SHA to Git objects and run that revision's auditor in an isolated checkout. It must report historical-context match and current-code match separately. Old-schema mapping and safe execution/cleanup remain open, so this mode is not implemented here.

测试计划：固定 v0.1.4 结果在当前审计器中得到明确拒绝；构造完整历史提交的正例、错误 SHA、被改写的汇总提交/锁/模型版本、dirty 结果、Git 不可用、历史审计器缺失及旧 schema 的负例；证明所有检查只读且不改动工作区。 / Planned tests cover an old-result rejection today and future positive and negative cases for full-commit binding, dirty source, unavailable Git, missing old auditor and schema mismatch, with a read-only workspace check.

## v0.1.6 独立环境与跨平台约束 / Isolated install and platform scope

本机使用现有 CPython 3.12.14 在 `[LOCAL_TEMP_ROOT]\[LOCAL_TEMP_ENTRY]` 新建虚拟环境，未改动 `[LOCAL_ENV_ROOT]\gridwise-campus-py312`。运行 `pip install -e ".[test]" -c requirements/verified-win-py312.txt`、`pip check`、完整 pytest 与合成求解/独立审计均成功；精确命令和计数见阶段报告。安装的 Pyomo 6.9.5、highspy 1.15.1、NumPy 2.5.4、PLY 3.11 及六个测试传递依赖与本机快照相同。`highspy.Highs().version()` 在此环境返回 1.15.1。 / A fresh Windows venv installed the constrained project and passed dependency checks, tests and synthetic audit; the existing project venv was untouched.

版本来源分开记录：Python 来自解释器 `platform.python_version()`；Pyomo、highspy 和各传递依赖来自已安装 distribution metadata；HiGHS API 版本来自 `highspy.Highs().version()`；`pyproject.toml` 是直接声明，约束文件是上次 Windows 解析快照。当前 `summary.json.run.solver_version` 仍写 highspy **分发**版本，不能据此独立证明底层二进制来源。 / Interpreter, distribution and solver API versions have distinct sources. The run manifest's solver version is the highspy distribution version, not binary provenance.

`colorama` 是 pytest 在 Windows 上的条件依赖；`verified-win-py312.txt` 因此是 Windows 快照，不能称为跨平台锁。工作流现仅在 Windows 分支使用该文件；Ubuntu/macOS 分支按 `pyproject.toml` 安装，传递依赖仍可能漂移。三平台真正兼容的精确约束须各平台实际安装验证后形成，当前没有这样的文件或通过结论。 / The Windows snapshot is used only by the Windows CI branch. Other OS branches use declared direct dependencies and have not been run; no verified cross-platform exact lock exists.

精确版本约束能限制解析结果，不能验证下载来源或 wheel 内容。若将来需要 artifact 锁，应按 Python/OS/架构收集**每个**运行及测试 wheel 的文件名和 SHA-256，以 `pip --require-hashes` 在独立环境重装，另记录构建后端、索引来源及第三方许可证；项目自身的 editable 构建需分开处理。SHA-256 只能发现字节变化，仍不能证明维护者身份、仓库可信或构建未被污染。本阶段没有生成或核验 wheel 哈希，也未声称供应链真实性。 / Future artifact locking needs per-platform hashes for every transitive wheel and separate handling of the editable project/build backend. Hashes detect byte changes, not trustworthy origin.

v0.1.7 历史审计原型在执行历史标准库审计器前，要求当前解释器的 Python/OS 与结果记录一致，四个运行包的安装版本与历史约束文件及结果记录一致；没有自动下载或安装历史依赖。依赖不可用记 `historical_context=UNKNOWN`、`arithmetic_physics=NOT_RUN`。这仍不证明相同版本对应相同 wheel 或求解器二进制。 / The historical prototype checks the interpreter and installed runtime versions before executing reviewed historical code, without installing packages or asserting binary identity.
