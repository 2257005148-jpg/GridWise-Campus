# Contributing to GridWise Campus / 参与 GridWise Campus

Thank you for considering a contribution. GridWise Campus is an offline campus-energy analysis and optimization prototype. Contributions must preserve its validation, audit, traceability, and fail-closed behavior.

感谢你考虑参与 GridWise Campus。本项目是离线高校能源分析与优化原型；所有贡献都应保持既有的数据校验、独立审计、可追溯性和失败关闭行为。

## Before contributing / 开始之前

- Use Python 3.12 or later in a dedicated virtual environment.
- Do not include real campus data, credentials, personal paths, generated run directories, dependency Wheels, or third-party binaries.
- Do not weaken physical constraints, validation, independent audit, artifact verification, or test assertions merely to make a test pass.
- A new dependency requires a technical need, version review, and license review before acceptance.

请使用 Python 3.12 或更高版本的独立虚拟环境；不要提交真实校园数据、凭据、个人路径、运行目录、依赖 Wheel 或第三方二进制。不得为了让测试通过而放宽物理约束、数据校验、独立审计、产物验证或测试断言。新增依赖必须先说明技术必要性，并完成版本与许可证审查。

## Fork, branch, and pull request / Fork、分支与拉取请求

1. Fork `2257005148-jpg/GridWise-Campus` on GitHub.
2. Create a focused branch from the current `master`, for example `feature/short-description` or `fix/short-description`.
3. Make the smallest coherent change. Keep unrelated formatting and refactors out of the same pull request.
4. Add or update tests first for changed behavior, then implement the change.
5. Update user and architecture documentation when the public behavior or contract changes.
6. Run the applicable safe test layers below and report the exact commands, pass/fail/skip counts, and anything not run.
7. Open a pull request describing the problem, design, evidence, risks, and compatibility impact.

请从当前 `master` 创建范围明确的分支，保持改动最小且单一；行为变更应先补测试，再实现，并同步更新必要文档。Pull Request 应写清问题、设计、验证证据、风险及兼容影响。

## Test markers and safe commands / 测试标记与安全命令

Every collected test must have exactly one primary marker:

- `unit`: isolated in-process logic without Tk, subprocess, real solver, or independent auditor.
- `gui`: Tk/Tcl widget, window, event-loop, or desktop-state behavior.
- `integration`: filesystem, subprocess, persistence, publication, or cross-module behavior.

Risk markers may be added in addition to the primary marker:

- `solver`: may invoke the real Pyomo `SolverFactory` or a real solver subprocess.
- `audit`: may start or validate an independent export/history audit workflow.
- `slow`: intentionally repeated, multi-process, timeout, or long-running behavior.

每个测试必须且只能有一个主分类 `unit`、`gui` 或 `integration`；真实求解器、独立审计及慢速行为分别叠加 `solver`、`audit`、`slow`。不得通过漏标风险标记绕过安全门禁。

Collect and validate classification:

```powershell
python -m pytest --collect-only -q
```

Safe unit layer:

```powershell
python -m pytest -q -m "unit and not solver and not audit and not slow"
```

Safe integration layer:

```powershell
python -m pytest -q -m "integration and not solver and not audit and not slow"
```

Safe GUI layer (requires a usable Tk desktop environment):

```powershell
python -m pytest -q -m "gui and not solver and not audit and not slow"
```

Daily non-GUI safe layer:

```powershell
python -m pytest -q -m "(unit or integration) and not solver and not audit and not slow"
```

Tests marked `solver`, `audit`, or `slow` are not part of the default safe workflow. Run them only with explicit maintainer authorization, in an isolated environment with synthetic data, and report them separately. Do not describe an unexecuted or skipped layer as passed.

## Code and review expectations / 代码与审查要求

- Follow the existing Python style and keep public interfaces typed and documented where the surrounding module does so.
- Prefer small functions, immutable result structures, and existing validation/control-layer APIs over duplicate business rules in Tk callbacks.
- Preserve UTF-8, the repository's line-ending contract, and a final newline. Run `git diff --check` before submitting.
- Use synthetic, isolated test data. Tests must not modify protected historical reports, user files, or external services.
- Separate static review, dynamic tests, skipped checks, and `NOT_RUN` items in the pull-request evidence.
- Do not commit generated reports, caches, virtual environments, local run artifacts, or secrets.

## Licensing of contributions / 贡献许可

Unless you explicitly state otherwise, any contribution intentionally submitted for inclusion in GridWise Campus is submitted under the project's Apache License 2.0 terms, consistent with Section 5 of that license. You must have the right to submit the work. Clearly identify third-party or adapted material and provide its source and license.

除非贡献者明确另行声明，主动提交并拟纳入 GridWise Campus 的贡献将按项目 Apache License 2.0 条款处理，与该许可证第 5 节一致。贡献者必须有权提交相关内容；第三方或改编材料必须明确标注来源与许可证。

The project does **not** currently require a Contributor License Agreement (CLA) or Developer Certificate of Origin (DCO) sign-off. A DCO may be considered later if contributor volume or governance needs justify it; it must not be imposed retroactively without a documented decision.

项目当前暂不要求 CLA，也不强制 DCO `Signed-off-by`。若未来贡献规模或治理需要发生变化，可再经书面决策引入 DCO，不应在没有明确决策时追溯强制。

## Reporting a vulnerability / 漏洞报告

Do not publish an undisclosed vulnerability in a public issue.

Dedicated security contact: `smartcampusenergy@outlook.com`

Send a concise description, affected version or commit, reproduction conditions, and potential impact. Do not include passwords, tokens, private campus data, or unrelated personal information. Allow the maintainer reasonable time to investigate before public disclosure.

请勿在公开 Issue 中披露尚未修复的漏洞。请发送至项目专用邮箱 `smartcampusenergy@outlook.com`，并提供受影响版本或提交、复现条件和潜在影响；不要附带密码、令牌、真实校园数据或无关个人信息。公开披露前请给予维护者合理的调查时间。
