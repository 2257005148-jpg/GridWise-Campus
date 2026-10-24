# 历史结果只读审计设计 / Read-only historical export audit design

状态 / Status: **设计与测试计划；未实现执行入口 / design and test plan, no execution entry point**.

## 关系与证据 / Context and evidence

| 维度 / Dimension | v0.1.4 及以前 / v0.1.4 and earlier | v0.1.5 及以后 / v0.1.5 onward |
|---|---|---|
| 结果 schema / Export schema | 逐步加入 `run`、源码摘要和 Git 字段；旧汇总缺少环境字段。 / `run`, model digest and Git fields arrived incrementally; environment fields absent. | `run` 还含 Python、OS、求解器/依赖版本和约束摘要。 / Adds environment versions and constraints digest. |
| 源码 / Source | `git_commit` 只在 v0.1.4 起出现；`source_state=dirty` 不代表可由提交重建。 / Git commit appears only from v0.1.4; dirty source cannot be reconstructed from that commit alone. | 同理；`model_version` 只覆盖规范化的 `core.py`，不是完整源码身份。 / Model digest covers normalized `core.py`, not all source. |
| 依赖 / Dependencies | 旧汇总未记录完整安装环境。 / Complete environment was not recorded. | 精确约束文件的 SHA-256 和四个运行包版本；没有 wheel 哈希。 / Constraint digest and four runtime versions, without wheel hashes. |
| 输出 / Exports | 场景原始 JSON、优化 CSV、基线 CSV、汇总 JSON 必须作为一组保持原字节。 / Four artifacts must be kept together. | 同左；审计只读且不自动迁移。 / Same; audit does not migrate files. |

目前没有显式 `schema_version` 字段；未来必须先建立按完整历史提交和已知字段集合的兼容表。不可从缺失字段猜测版本，也不可把格式转换后的新汇总称作旧运行原件。 / There is no explicit schema version. A future compatibility table must bind known field sets to exact commits; missing fields cannot be guessed into a version.

## 拟议执行流程 / Proposed execution flow

1. 调用者指定完整 40 位提交 SHA 与四个现存文件。所有路径只读打开；先记录 SHA-256、大小、mtime，不写回原件。 / Require a full commit ID and four existing files; record their identifiers without writing to them.
2. 检查本地 Git 对象存在且是提交；`summary.run.git_commit` 必须等于指定提交，`source_state` 必须为 `clean`。旧 schema 若没有提交或源码状态，标为**上下文不可证**并失败；dirty、unknown、unavailable 同样失败。 / Missing commit, dirty or unknown source fails historical context attestation.
3. 在独立临时 Git worktree 检出该提交，验证工作树指向同一提交，并对历史 `core.py`、审计器、约束文件和声明文件核对内容。路径必须在专用临时根内；退出前核对目标路径并清理工作树，失败时保留诊断。不能移动、切换或重置当前工作区。 / Use an isolated worktree; never switch or reset the current checkout. Cleanup needs verified paths and failure diagnostics.
4. 仅在审查历史脚本及运行边界后执行该提交的审计器，使用该历史版本支持的 schema 和依赖环境。不得静默在当前 Python/依赖上执行未知历史代码；缺依赖或版本不符应报告 `not executed`。 / Execute historical code only after reviewing its revision and matching environment. Missing dependencies yield `not executed`.
5. 分别报告三个独立结论：`historical_context`（源码/提交/schema/环境关联）、`current_context`（当前 HEAD、源码状态、schema、约束是否匹配）、`arithmetic_physics`（选定版本的独立算术和物理检查）。任一项的 `pass` 不推导另一项的 `pass`。 / Report historical context, current context and arithmetic/physics separately; no conclusion implies another.
6. 再次比较四个原件的字节摘要与状态；如发生变化，整体失败。 / Compare original artifact hashes and metadata again; any mutation fails.

历史审计即使三项全通过，也只证明所提供文件与所检查版本的规则一致；Git 提交、哈希和版本号不是来源真实性或求解器实际执行的证明。 / Even all three passes do not prove authentic input or trusted solver execution.

## 明确的失败用例 / Required negative tests

| 输入 / Case | 预期 / Expected result |
|---|---|
| v0.1.4 旧 schema 用当前 CLI 审计 / old schema with current auditor | 退出 2、定位缺失字段、不改写输出；目前已作为黑盒回归。 / Exit 2, locate missing field, leave files untouched; black-box regression exists. |
| 缺少或伪造 `git_commit` / missing or forged commit | 历史上下文失败，不能降级为当前审计。 / Historical context fails; no fallback. |
| `source_state` 为 dirty、unknown 或 unavailable / uncertain source state | 历史上下文失败；HEAD 不能代表工作区内容。 / Fail; HEAD does not identify dirty bytes. |
| 依赖版本、约束摘要缺失或不符 / missing or mismatched dependencies | 环境匹配失败，历史代码不执行；算术检查若单独运行须明确标注其版本。 / Environment failure; historical execution withheld. |
| Git 不可用、对象缺失、无法创建 worktree / Git unavailable or checkout failure | 历史上下文失败；保留当前工作区。 / Fail and preserve current checkout. |
| 历史审计器缺失或未审查 / old auditor unavailable or unreviewed | `arithmetic_physics=not executed`，不可用当前版的通过代替。 / Do not substitute a current-auditor pass. |
| 原始四文件在审计期间变化 / artifact mutation | 整体失败，指出文件。 / Fail with file location. |

当前仅实现了当前代码的严格 CLI 审计及旧 schema/缺锁文件的负例。没有历史模式命令、没有历史 worktree、没有执行历史脚本。 / Only current-code strict audit and negative regressions exist. No historical command, worktree or historical script execution was added.
