# v0.3.6 导出一致性边界 / Export consistency boundary

## 现行行为 / Current behavior

CLI 在目标目录暂存四个文件，逐一发布 `summary.json`、`optimized.csv`、`baseline.csv`、`grid-purchase.svg`。默认模式用硬链接独占创建，遇到同名目标拒绝覆盖；`--overwrite` 为旧文件创建恢复副本，再逐个替换。普通 Python 异常触发回滚；回滚失败会保留暂存目录并报告位置。暂存目录清理失败会明确报告发布是否已经完成，并保留目录供人工检查。

The CLI stages four files in the destination and publishes them one by one. Default mode uses no-clobber hard links. Explicit `--overwrite` makes recovery copies and replaces regular files. A normal Python exception triggers rollback. Failed rollback retains the stage directory; failed stage cleanup reports whether publication completed and retains the directory.

## 故障边界 / Failure boundary

单个 `os.replace` 可原子替换一个文件，但四个文件没有共同的原子提交点。受控子进程试验证实：首文件替换后，读者可同时看到新汇总和三份旧文件；强制终止后恢复副本留在暂存目录，CLI 无法自动回滚。普通异常、备份失败和清理失败由合成故障注入测试覆盖；这些测试不证明断电后的持久性。两个默认模式写入者竞争同一新目录时，一方成功、一方拒绝，仍须由读者检查进程退出码和结果完整性。`--overwrite` 与不协作写入者之间仍有竞态；不能将预检当成互斥锁。

One `os.replace` can atomically replace one file, not a four-file bundle. A controlled child demonstrated mixed versions after its first replacement, and forced termination left a recovery copy. Fault injection covers ordinary exceptions, backup failure and cleanup failure; it does not establish power-loss durability. Two default writers targeting the same new directory yielded one success and one conflict. Explicit overwrite has no lock against uncooperative writers.

## 方案评估与本阶段选择 / Options and stage decision

| 方案 / Option | 一致性与恢复 / Consistency and recovery | 本阶段决定 / Decision |
|---|---|---|
| 逐文件发布 / Sequential files | 保持旧接口；普通异常可回滚，崩溃和并发读取可见混合版本。 / Compatible; mixed versions remain possible. | 保留为兼容模式，明确限制。 / Retain with explicit limits. |
| 每次使用唯一新目录 / Fresh directory per run | 已有 CLI 可直接支持；只在退出码 0 且独立审计通过后使用；失败目录不可当成完整运行。 / Supported now; consume only after success and audit. | 推荐当前工作流。 / Recommended now. |
| 完成标记或清单 / Completion marker or manifest | 读者必须新增校验规则；只有遵守规则的读者才会忽略未完成目录。 / Requires a reader contract. | 暂不增加协议。 / Defer. |
| 不可变目录加已完成运行选择器 / Immutable runs with selector | 可避免覆盖旧结果，但需要选择器、并发和清理治理。 / Requires selector and retention rules. | 留待独立设计。 / Defer. |

推荐命令流程：为每次运行选择不存在的独立目录；检查 CLI 退出码；使用原始场景和导出文件执行 `tools/audit_exports.py`；审计通过后才将该目录交给读者。不要在正在写入的目录读取结果，也不要依赖 `--overwrite` 提供四文件事务保证。独立审计证明导出算术与物理约束符合其契约，不能证明原始输入真实可信。

Recommended workflow: choose a fresh directory, check the CLI exit code, audit its exports against the original scenario, and only then pass that directory to readers. Do not read an in-progress directory or treat overwrite as a bundle transaction. The auditor checks its arithmetic and physics contract; it does not establish source authenticity.
