# v0.3.7 已校验运行目录 / Verified run artifact contract

## 开源参考与选择 / Open-source references and decision

| 项目 / Project | 官方许可 / Official license | 适配度 / Fit | 决定 / Decision |
|---|---|---|---|
| [DVC](https://github.com/treeverse/dvc) | [Apache-2.0](https://github.com/treeverse/dvc/blob/main/pyproject.toml) | 内容哈希和目录清单思想契合；其缓存、远程与依赖规模超出本地五文件运行。 / Content hashes and directory metadata are relevant; the full cache/remote stack is excessive. | 只参考概念，不安装或复制代码。 / Design reference only. |
| [MLflow](https://github.com/mlflow/mlflow) | [Apache-2.0](https://github.com/mlflow/mlflow/blob/master/pyproject.toml) | 运行与产物归档概念契合；服务与依赖规模不适合当前离线原型。 / Run/artifact concepts fit; service and dependencies do not. | 只参考概念，不安装或复制代码。 / Design reference only. |
| Python 标准库 / Python standard library | 随已安装 CPython 提供 / Supplied with CPython | `hashlib`、`json`、`pathlib`、`os` 和 `subprocess` 足以实现本地有界校验。 / Sufficient for this bounded local contract. | 本阶段实现；零新增依赖。 / Chosen; no new dependency. |

以上参考项目的许可证不决定 GridWise Campus 自身的授权状态；本项目正式许可证仍待有权人决定。 / Reference licenses do not grant this project a publication license. No source or resource was copied.

## 目录与格式 / Directory and format

新入口 / New entry point:

```text
python -m gridwise.run_artifacts publish SCENARIO.json RUNS_PARENT
python -m gridwise.run_artifacts verify RUNS_PARENT/run-<32 lowercase hex digits>
```

每次 `publish` 使用原子 `mkdir(exist_ok=False)` 预留新的 `run-<UUID4>` 目录；原有 `python -m gridwise.cli INPUT OUTPUT [--overwrite]` 不变。运行目录在生成期间可见，但安全读取接口要求完成标记，因此不能将未完成目录当作有效运行。 / Each publish reserves a fresh directory without overwriting another run. The legacy CLI remains unchanged. In-progress directories are visible but not readable through the verified interface.

完成目录恰有七项 / A completed directory has exactly seven entries:

```text
scenario.json
optimized.csv
baseline.csv
summary.json
grid-purchase.svg
manifest.json
COMPLETE
```

`manifest.json` 格式版本为整数 `1`，包含 `metadata` 与按上述前五个文件固定排序的 `files`。每项文件记录 `name`、`size_bytes`、小写十六进制 `sha256`。元数据固定为 `model_version`、`git_commit`、`source_state`、`scenario_sha256`、`solver_name`、`status`，并与 `summary.json` 的同名运行字段比较。`scenario_sha256` 还与目录内 `scenario.json` 的原始字节比较。`COMPLETE` 的内容严格为 `sha256:<manifest.json bytes digest>\n`；它只是清单字节绑定标记，不能单独证明有效性。 / The manifest has a strict schema, fixed order and per-file size/digest. The marker binds exact manifest bytes and is not a standalone trust proof.

限制 / Limits: 清单 64 KiB、标记 80 bytes、每个结果文件 32 MiB、五个文件合计 96 MiB；目录文件集合固定。拒绝清单中的绝对路径、路径穿越、重复名、额外字段、重复 JSON 键、非有限 JSON 值、链接或 junction 路径。 / Manifest and file reads are bounded; unexpected names, fields, duplicates, links and non-finite JSON fail closed.

## 发布顺序与安全读取 / Publication and verified reading

1. 复制显式提供的合成场景字节到新目录的 `stage/`，调用既有 CLI 生成四份导出。 / Copy the supplied scenario to `stage/`; run the existing CLI.
2. 对五个暂存文件在审计前后计算 SHA-256；中间调用未修改的 `tools/audit_exports.py` 对同一 `scenario.json`、两份 CSV 与汇总文件做独立审计。非零退出、缺少成功标记或前后字节摘要不同均失败。 / Hash all staged files before and after the independent audit; reject an audit failure or changed bytes.
3. 读取并限定五个文件，生成清单；在暂存目录校验清单、大小、哈希和元数据。 / Build and verify the bounded manifest in staging.
4. 将文件移入预留的运行目录，再校验；最后独占创建 `COMPLETE` 并再次调用安全读取接口。 / Move files into the reserved directory, reverify, then create the marker last.
5. 读取端先检查目录名、精确文件集合、标记、清单及每个文件的大小与 SHA-256，再返回**已校验的字节副本**，不返回要求调用方自行重新打开的裸路径。读取前后检查文件身份、大小和修改时间，并在末尾重读清单与标记。 / The reader returns verified bytes and performs visible replacement checks.

普通异常、取消、审计失败或清单失败会留下**未完成**的本次新目录，供定位检查；不自动删除任何目录。进程被强制终止时也可能留下未完成目录。安全读取接口拒绝这些目录。 / Failures leave an incomplete generated directory for inspection; no unknown directory is removed. A forced kill can do the same, and the reader rejects it.

## 保证范围 / Guarantee boundaries

- `os.replace` 只保证单个文件替换；本实现的跨文件有效性由“完整目录校验＋最后创建完成标记”的**协作读取契约**提供。手工直接读中途文件仍可能看到部分内容。 / Per-file replacement is not a multi-file transaction; cooperating readers use the marker and hashes.
- 对同一目录的并发安全读取返回各自校验过的字节。若其他进程能在校验后改写整个目录和清单，哈希不能证明来源真实性，且无法完全消除路径替换竞态。 / Concurrent readers get verified bytes; a writer able to forge the manifest and marker defeats provenance claims. Some filesystem races remain.
- 未实现数字签名、目录级持久化同步、断电恢复或抗恶意本机写入者的保证。完成标记的 `fsync` 不等于五个文件和父目录已在断电后持久化。 / No signature, directory durability, power-loss guarantee or adversarial local-writer protection is claimed.
- 独立审计在发布时检查其算术和物理契约；读取端不重复运行优化器或审计器。哈希和提交号不能证明输入真实，汇总中的 `optimal` 字段本身也不能替代求解终止检查。 / Publication audits arithmetic and physics; reading verifies bytes. Hashes, Git IDs and a status string are not authenticity or optimality proofs.

v0.3.8 桌面端通过 `publish_run()` 创建运行目录，再用 `read_verified_run()` 返回的已校验字节准备图表。刷新历史时会再次检查目录；选择历史运行时仍需重新安全读取，并与当前已通过质量检查的 CSV 按时间戳和输入值对齐。`VALID` 仅表示本次清单与文件检查通过；本地可写目录中的清单和文件若被同时恶意改写，哈希不能提供真实性保证。 / The desktop publishes, rereads verified bytes and only then prepares charts. Historical selection revalidates files and aligns them with the currently checked CSV. A valid listing is a current integrity check, not an authenticity guarantee against a malicious local writer.
- 若取消信号到达最终标记写入之后，完整运行可能已提交；取消只保证在检查到信号前不会继续下一步。 / Cancellation after the final marker may arrive after publication has completed.

建议运行目录位于当前用户可控的本地磁盘，读者只调用 `read_verified_run()` 或 `verify`，并保存返回的字节用于后续解析。 / Use a trusted local parent directory and consume the returned verified bytes.

v0.3.9 追加读取末尾的目录身份、修改时间和精确条目集合复核。确定性故障注入证实：旧读取器在读取过程中出现新目录项时可能仍返回成功；修复后拒绝该可观察变动。仍不能消除全部 TOCTOU 竞态，也不能将目录 `mtime` 或哈希解释为对恶意本机写入者的防护。长期保存及备份策略见[运行历史保存](run-history-storage.md)。/ A final directory identity, mtime and entry-set recheck now rejects a visible addition during reading. It does not eliminate all TOCTOU races or authenticate a writable local directory.
