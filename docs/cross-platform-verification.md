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
