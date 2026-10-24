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
