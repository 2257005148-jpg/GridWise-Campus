# 跨平台验证准备 / Cross-platform verification preparation

当前仅在 Windows 上动态验证；`wsl --list --quiet` 返回 1，未发现可用 Linux 发行版，也未安装 Docker。本阶段未运行 Linux、macOS 或 GitHub Actions。 / Only Windows has been exercised locally. No available WSL distribution or Docker was found. Linux, macOS, and hosted CI remain unrun.

## 已检查的可移植性要点 / Portability checks

- 路径使用 `pathlib.Path` 和命令行参数，未在审计器中拼接平台路径分隔符。测试包含 Unicode 和空格路径。 / Paths use `Path`; a Unicode path with spaces was tested.
- JSON 按 UTF-8 或带 BOM 的 UTF-8 读取，CSV 按 UTF-8/BOM 读取；LF 与 CRLF 的本机黑盒测试均通过。非法编码会定位文件并退出 2。 / UTF-8 with optional BOM, LF/CRLF, and invalid encodings were tested on Windows.
- CSV 使用严格解析、精确列集合及顺序；每个字段均按名称检查。换行与字段长度有资源上限。 / Strict CSV parsing and exact headers are enforced.
- 时间戳必须带 UTC 偏移，相邻时刻以绝对时间计算间隔；没有依赖机器本地时区。夏令时切换场景尚未测试。 / Offset-aware timestamps avoid local timezone dependence; DST cases remain untested.

## 手动复现 / Manual reproduction

在 Python 3.12+ 环境下，从项目根目录运行 / From the repository root with Python 3.12+:

```text
python -m pip install -e ".[test]"
python -m pytest -ra -q
python -m gridwise.cli examples/synthetic-day.json outputs/ci
python tools/audit_exports.py examples/synthetic-day.json outputs/ci/optimized.csv outputs/ci/baseline.csv outputs/ci/summary.json
```

`.github/workflows/synthetic-validation.yml` 为 **仅手动触发** 的三平台矩阵准备稿，使用仓库内合成样例、只读仓库权限、不上传构建产物或数据。当前仓库没有远程，工作流尚未执行，因此不能据此宣称跨平台通过。工作流采用的官方 [checkout](https://github.com/actions/checkout) 与 [setup-python](https://github.com/actions/setup-python) 均在其仓库声明 MIT 许可；项目本身仍无许可证。 / The workflow is a manual three-OS preparation only; no hosted run has occurred.

v0.1.3 复核：本机 WSL 列表命令退出 1，未发现可用发行版；Docker/Podman 命令不存在，因此没有独立 Linux 运行。工作流新增 OS 与 Python 版本打印，仍只用合成数据，未上传数据或产物。场景原始字节指纹可避免路径/本地时区影响，但 LF/CRLF 或 BOM 不同会有不同摘要；生成器与审计器均在 Windows 上测试这些编码组合。仅解析 YAML 或审阅工作流不能算 Linux/macOS 测试通过。 / No Linux environment was available; provenance and newline behavior were checked on Windows only.

v0.1.4 静态复核：工作流仍为手动触发；权限仅 `contents: read`；使用 `actions/checkout@v7`、`actions/setup-python@v7`、Python 3.12，安装 `.[test]` 后运行完整 pytest、生成合成导出并审计。无上传产物步骤、无仓库密钥引用。`pip install` 在未来的托管运行中仍需网络及第三方包来源，当前未运行或下载；现有依赖未完全锁定。Git HEAD/状态现在进入汇总，测试生成与审计必须在同一工作流检出状态执行。 / The workflow was reviewed, not executed; dependency resolution and hosted behavior remain unverified.

v0.1.6 静态复核：矩阵仍为手动触发、只读仓库权限、合成数据且无上传步骤。Windows 安装分支使用实测的 `verified-win-py312.txt`；Ubuntu/macOS 安装分支只使用 `pyproject.toml` 直接声明，传递依赖未锁。**动态验证仅在本机 Windows 的独立虚拟环境完成**；没有运行 Linux、macOS 或托管矩阵。存在工作流配置不等于跨平台通过。 / The matrix is still manual and synthetic-only. Windows uses the observed snapshot; other OS branches use declarations. Only an isolated local Windows install was exercised.

v0.1.7 静态复核：工作流仍为 Ubuntu/Windows/macOS 手动合成矩阵、`contents: read`、无上传步骤；本阶段没有触发托管运行。历史审计原型另外使用 Git worktree、Python `-I` 和固定哈希的 v0.1.5 审计器，只在本机 Windows 的合成临时仓库动态验证。Linux/macOS 的历史审计行为标记为 `NOT_RUN`。 / The workflow was reviewed but not run. Historical worktree behavior was exercised only on local Windows synthetic repositories.

v0.1.8 静态复核：历史测试通过 `git show` 读取已审查的 v0.1.5 提交，故 CI checkout 改为 `fetch-depth: 0`；合成临时仓库的传递依赖约束按测试解释器已安装版本生成，避免将 Windows 快照当成跨平台锁。矩阵仍仅手动触发、`contents: read`，没有上传步骤。本机 `wsl --list --quiet` 退出 1，没有可用 Linux 发行版；未启用系统功能。Ubuntu、macOS 和托管 CI 均为 **NOT_RUN**，配置修正不是运行通过。 / Full Git history is required by the historical test fixture; other-platform validation remains unexecuted.

v0.1.9 再次静态审查相同手动矩阵、Python 3.12、完整 Git 历史、只读权限、合成测试及无上传步骤，未更改 CI。`wsl --list --quiet` 本机退出 1；只有 Windows 11 / CPython 3.12.14 的本地独立环境提供动态证据。Ubuntu、macOS 与托管 CI 仍为 **NOT_RUN**；Git Bash 或 YAML 检查不算 Linux/macOS 执行。 / The workflow remains unrun outside the local Windows environment.
