# 输入指纹与结果关联 / Input fingerprint and result linkage

## 契约 / Contract

每次 CLI 运行只读取输入场景文件一次。`scenario_sha256` 是 `sha256:` 加上该文件**原始字节**的 SHA-256 小写十六进制摘要。汇总 `summary.json` 的 `run` 对象还记录 `model_version = cost-mip-v1`、`solver_name = appsi_highs` 与 `status = optimal`。两方案各自保留 `status`。独立审计器重新读取给定的场景文件、计算摘要，并核对 `run` 元数据后检查每时段物理约束与汇总。 / The CLI hashes the exact source bytes it parsed. The independent auditor recomputes the digest and validates run metadata before auditing both schedules.

## 覆盖范围与规范化 / Coverage and normalization

- 指纹覆盖整个输入 JSON 文件：单位、时间戳原文及 UTC 偏移、步长、电网和电池参数、负荷、光伏、电价、字段名，以及空白、键顺序和任何额外字段。 / All source bytes are covered, including units, timestamp spelling and offset, values, key order, and whitespace.
- 允许 UTF-8 或 UTF-8 BOM 输入；不执行 Unicode、换行、时间戳、电价或数值文本规范化。`1` 与 `1.0`、LF 与 CRLF、带或不带 BOM、等价的时区表示都会产生不同指纹。 / No text or semantic normalization is performed. Byte-distinct but equivalent JSON produces different digests.
- 文件路径、修改时间、运行机器、本机时区与输出目录不进入指纹。仅相同字节得到相同指纹。 / Paths, file timestamps, host time zone, and output location are excluded.
- 审计器仍要求场景和结果使用当前精确字段集合、正确单位及带偏移的连续时间戳。旧版本没有 `run` 元数据的汇总会失败关闭；需重新生成。 / Legacy summaries without metadata fail closed and should be regenerated.

## 边界 / Limits

指纹能发现“新输入 + 旧结果”或汇总指纹的单独变化；同步改写原始输入、指纹及全部导出后，只靠哈希不能证明原始输入真实、求解器确实运行或模型代码未变。`model_version` 是人工维护的数学模型标识，并非代码哈希或软件包发布版本。没有签名、密钥管理、可信时间戳或外部服务。 / Hashes detect content mismatch, not authenticity or trusted execution. The model identifier is manually maintained and is not a source-code hash.

## 公开复核 / Reproduction

```text
python -c "import hashlib,pathlib; print('sha256:'+hashlib.sha256(pathlib.Path('examples/synthetic-day.json').read_bytes()).hexdigest())"
python -m gridwise.cli examples/synthetic-day.json outputs/example
python tools/audit_exports.py examples/synthetic-day.json outputs/example/optimized.csv outputs/example/baseline.csv outputs/example/summary.json
```

Windows、Linux 和 macOS 上，只要文件字节一致，SHA-256 结果应一致；本阶段尚未在 Linux/macOS 动态验证。 / Cross-platform digest agreement is expected for identical bytes but has not been exercised on Linux or macOS in this stage.

## v0.1.4 模型版本与 Git 状态 / Model version and Git state

`cost-mip-v1` 现在是**模型家族**。完整 `model_version` 为 `cost-mip-v1+sha256.<64 位摘要>`，摘要基于 `src/gridwise/core.py` 的源码字节，将 CRLF 和孤立 CR 换成 LF 后计算。模型目标、变量、约束或单位语义发生变化时，维护者还应升级家族标识；任何 `core.py` 内容变化（除换行符）都会自动改变完整版本。审计器独立读取当前 `core.py` 并核对完整版本，不构建 Pyomo 模型。 / The full model version changes automatically with normalized model source bytes; semantic contract changes also require an intentional family bump.

`git_commit` 记录运行时仓库 HEAD，`source_state` 记录 `clean` 或 `dirty`。状态范围为 `src/`、`tools/` 和 `pyproject.toml`；已有 `docs/progress/` 报告与忽略的 `outputs/` 不影响它。**未提交源码**仍可运行，但标记 `dirty`；此时 HEAD 只是最近提交，不能代表未提交源码。审计器要求当前 HEAD 与状态均与汇总一致，因此提交代码后旧的 dirty 结果会被拒绝，应在新状态重新生成。 / Dirty runs are explicit; their HEAD is not a complete source identity. Default audit requires the same checkout and source state.

若 Git 不存在、当前目录不属于本项目的仓库或 Git 命令失败，两个字段均为字符串 `unavailable`；独立审计器在相同无 Git 环境中仍可完成模型源码与算术检查。跨有/无 Git 环境或不同 HEAD 审计会明确失败，不能把不可用误写成 clean。旧汇总缺少任何必需的 `run` 字段时退出 2。 / Git-unavailable runs use explicit sentinels; legacy or mismatched provenance fails closed.

Git 提交号、模型源码摘要与场景 SHA-256 都只能关联当前提供的内容和检出状态，不证明代码、输入或求解器执行真实可信。`core.py` 摘要不覆盖 CLI、依赖 wheel 或审计器；这些变化依赖 Git 状态与提交号识别。没有 Git 的分发包不具备这一层来源识别。 / None of these identifiers is an authenticity proof; the model digest covers only `core.py`.

## v0.1.5 运行环境 / Execution environment

运行汇总另记录 Python、操作系统、求解器与运行依赖版本，以及本机版本约束快照的 SHA-256；核验范围和历史审计设计见 [运行环境快照](environment-reproducibility.md)。旧汇总缺少新增字段时当前审计器会失败关闭，不会自动补写。 / The run manifest now records observed environment versions and the constraints snapshot digest. See the linked environment note for scope and historical-audit design. Old summaries missing the new fields fail closed.
