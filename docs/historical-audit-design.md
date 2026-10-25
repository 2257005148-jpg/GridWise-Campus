# 历史结果只读审计设计 / Read-only historical export audit design

状态 / Status: **v0.1.7 只读原型，仅支持 `run-v015` / v0.1.7 read-only prototype, `run-v015` only**.

公开命令 / Public command:

```text
python tools/audit_history.py --ref FULL_40_HEX_SHA --schema run-v015 SCENARIO.json OPTIMIZED.csv BASELINE.csv SUMMARY.json
```

可选 `--repo REPOSITORY` 仅用于本项目或受控的本地合成临时仓库；不得指向未经审查的外来仓库，因为 Git checkout 仍受该仓库属性和本机 Git 配置影响。无远程访问。默认仓库为本项目。成功退出 0 要求 `historical_context`、`arithmetic_physics` 和 `artifacts_unchanged` 均为 `PASS`；`current_context=FAIL` 可以与历史成功同时存在。其他情况退出 2。输出 JSON 中 `PASS`/`FAIL`/`UNKNOWN`/`NOT_RUN` 均为显式状态。 / The optional repository path is for this project or controlled synthetic local repositories, not unreviewed external repositories. Exit 0 requires historical context, arithmetic and artifact preservation to pass; a current-context mismatch is reported separately.

## 关系与证据 / Context and evidence

| 维度 / Dimension | v0.1.4 及以前 / v0.1.4 and earlier | v0.1.5 及以后 / v0.1.5 onward |
|---|---|---|
| 结果 schema / Export schema | 逐步加入 `run`、源码摘要和 Git 字段；旧汇总缺少环境字段。 / `run`, model digest and Git fields arrived incrementally; environment fields absent. | `run` 还含 Python、OS、求解器/依赖版本和约束摘要。 / Adds environment versions and constraints digest. |
| 源码 / Source | `git_commit` 只在 v0.1.4 起出现；`source_state=dirty` 不代表可由提交重建。 / Git commit appears only from v0.1.4; dirty source cannot be reconstructed from that commit alone. | 同理；`model_version` 只覆盖规范化的 `core.py`，不是完整源码身份。 / Model digest covers normalized `core.py`, not all source. |
| 依赖 / Dependencies | 旧汇总未记录完整安装环境。 / Complete environment was not recorded. | 精确约束文件的 SHA-256 和四个运行包版本；没有 wheel 哈希。 / Constraint digest and four runtime versions, without wheel hashes. |
| 输出 / Exports | 场景原始 JSON、优化 CSV、基线 CSV、汇总 JSON 必须作为一组保持原字节。 / Four artifacts must be kept together. | 同左；审计只读且不自动迁移。 / Same; audit does not migrate files. |

现有汇总没有显式 `schema_version` 字段，因此调用者必须传 `--schema run-v015`，原型只接受 v0.1.5 的完整 `run` 字段集合和经审查的审计器 SHA-256。缺失或其他 schema 返回 `UNKNOWN`，不猜测版本、不转换旧汇总。v0.1.4 及更旧的结果仍不受支持。 / The summary has no schema field. The caller must name `run-v015`; no missing field is inferred or migrated. Older results remain unsupported.

## 拟议执行流程 / Proposed execution flow

1. 调用者指定完整 40 位提交 SHA 与四个现存文件。所有路径只读打开；先记录 SHA-256，再将每份文件复制到独立临时目录并校验快照摘要等于原摘要。历史脚本只读取快照，不写回原件。文件读取及快照各受 32 MiB 上限约束。 / Hash the four originals and create matching bounded byte snapshots; the historical auditor reads only the snapshots.
2. 检查本地 Git 对象存在且是提交；`summary.run.git_commit` 必须等于指定提交，`source_state` 必须为 `clean`。旧 schema 若没有提交或源码状态，标为**上下文不可证**并失败；dirty、unknown、unavailable 同样失败。 / Missing commit, dirty or unknown source fails historical context attestation.
3. 检出前拒绝历史树中的 `.gitattributes`、活跃的仓库 `info/attributes`，并要求四个必需源码/约束文件为普通 Git blob。在独立临时 Git worktree 检出该提交，验证工作树指向同一提交。检出命令仅本次使用 `core.autocrlf=false`、空的 `core.attributesFile`、专用空 `core.hooksPath` 和 `core.fsmonitor=false`，避免换行转换、全局属性、仓库钩子及文件监视器影响；不更改仓库配置。临时目录位于 `[LOCAL_TEMP_ROOT]`（Windows 已存在时）或系统临时目录。 / Command-local Git settings disable checkout hooks and fsmonitor and preserve lock bytes.
4. 只允许 SHA-256 为 `acb15253f3b9f0ea450e0d9dca175aa0299ba3f5a7c4d2dd2eb30eda5ee68c9a` 的已审查 v0.1.5 标准库审计器运行；Git 对象和检出脚本均核对。用当前解释器的 `-I` 隔离模式执行；先核对 Python/OS、四个运行包安装版本与历史约束文件，并读取历史 `pyproject.toml` 的直接依赖。缺失依赖或不匹配时历史算术为 `NOT_RUN`。 / Only the pinned reviewed standard-library auditor executes with Python isolation after interpreter and dependency checks.
5. 分别报告三个独立结论：`historical_context`（源码/提交/schema/环境关联）、`current_context`（当前 HEAD、源码状态、schema、约束是否匹配）、`arithmetic_physics`（选定版本的独立算术和物理检查）。任一项的 `pass` 不推导另一项的 `pass`。 / Report historical context, current context and arithmetic/physics separately; no conclusion implies another.
6. 再次比较四个原件的 SHA-256；若字节变化，历史上下文与算术均记 `FAIL`。正常情况下移除临时工作树与受控快照；清理失败则记 `FAIL` 并保留临时路径供人工检查。前后摘要只能确认两个观察点相同，不能发现中途被替换后又恢复的短暂变化；快照确保历史算术始终针对预先核对的同一组字节。 / Endpoint hashes cannot detect a transient replace-and-restore; snapshots keep the audited content stable.

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

v0.1.7 原型已在合成临时仓库中动态测试已知 schema、缺提交/schema、被改写的历史源码/约束/审计器、当前与历史不匹配、Git/依赖不可用、输入回显篡改、工作树创建/清理失败和原件审计期间变化。其他历史 schema、非 Windows 平台、恶意历史 Git 仓库及 wheel 来源真实性仍未验证。 / The prototype has synthetic temporary-repository tests for the listed paths. Older schemas, other platforms, malicious repositories and wheel origin are unverified.

v0.1.8 安全复核 / Security review: 在受控临时仓库中验证仓库配置的 `post-checkout` 钩子不能执行；原件于快照前或历史算术期间替换时失败；历史锁文件检出字节不同时在运行脚本前失败。`--repo` 仍只允许使用已审查的本项目或受控测试仓库。Git 的本地配置、环境变量和宿主机可信性没有被此原型全面沙箱化；对不可信仓库停止使用，不把哈希检查当成安全执行许可。 / The prototype is not a general sandbox for untrusted repositories.
