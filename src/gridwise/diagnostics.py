"""Conservative, display-only diagnostics for failed local runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .run_artifacts import RunArtifactError


class FailureCategory(str, Enum):
    INPUT_INVALID = "INPUT_INVALID"
    INFEASIBLE = "INFEASIBLE"
    SOLVER_UNAVAILABLE = "SOLVER_UNAVAILABLE"
    SOLVER_TIMEOUT = "SOLVER_TIMEOUT"
    SOLVER_ERROR = "SOLVER_ERROR"
    CANCELLED = "CANCELLED"
    AUDIT_FAILED = "AUDIT_FAILED"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    UNKNOWN = "UNKNOWN"


class FailureStage(str, Enum):
    INPUT = "input"
    SOLVE = "solve"
    AUDIT = "audit"
    ARTIFACT = "artifact"
    CONTROL = "control"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RunDiagnostic:
    category: FailureCategory
    stage: FailureStage
    summary: str
    evidence: tuple[str, ...]
    suggestions: tuple[str, ...]
    cause_chain: tuple[str, ...]
    original_error: BaseException
    solver_status: str | None = None
    termination_condition: str | None = None


_NONOPTIMAL = re.compile(
    r"gridwise: no optimal solution: ([A-Za-z_][A-Za-z0-9_]*), "
    r"([A-Za-z_][A-Za-z0-9_]*)\Z")
_UNAVAILABLE = re.compile(r"gridwise: solver unavailable: ([^\r\n]+)\Z")
_SOLVER_FAILED = re.compile(r"gridwise: solver failed: ([^\r\n]+)\Z")


def _safe_text(error: BaseException) -> str:
    try:
        return str(error)
    except Exception as formatting_error:
        return (f"<{type(error).__name__}: message unavailable; "
                f"formatting failed with {type(formatting_error).__name__}>")


def _chain(error: BaseException) -> tuple[BaseException, ...]:
    result, seen = [], set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen and len(result) < 16:
        result.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return tuple(result)


def _stage(value, fallback: FailureStage) -> FailureStage:
    try:
        return FailureStage(value)
    except (TypeError, ValueError):
        return fallback


def _suggestions(category: FailureCategory) -> tuple[str, ...]:
    return {
        FailureCategory.INPUT_INVALID: (
            "检查错误字段、单位、时间步长和设备参数；不会自动修改输入。",
            "Review the reported field, units, time step and device values; no value is changed automatically.",
        ),
        FailureCategory.INFEASIBLE: (
            "检查电网/峰值上限、初末 SOC、容量和充放电功率；这些只是排查方向，不是根因结论。",
            "Review grid or peak limits, initial/terminal SOC, capacity and battery power; these are checks, not a root-cause claim.",
        ),
        FailureCategory.SOLVER_UNAVAILABLE: (
            "检查已验证环境中的 Pyomo、highspy 和 HiGHS 可用性。",
            "Check Pyomo, highspy and HiGHS availability in the verified environment.",
        ),
        FailureCategory.SOLVER_TIMEOUT: (
            "检查场景规模和本机负载；如有明确需求再调整超时，不会自动放宽约束。",
            "Review scenario size and host load; adjust timeouts only deliberately, without relaxing constraints.",
        ),
        FailureCategory.SOLVER_ERROR: (
            "保留求解器错误和环境信息，先确认后端故障再调整参数。",
            "Preserve solver and environment evidence before changing any model parameter.",
        ),
        FailureCategory.CANCELLED: (
            "任务由取消请求停止；重新运行前确认输入和参数仍是预期值。",
            "The task stopped on cancellation; recheck the selected input and parameters before retrying.",
        ),
        FailureCategory.AUDIT_FAILED: (
            "不要使用未通过独立审计的结果；检查审计日志和导出一致性。",
            "Do not use unaudited results; inspect the audit log and export consistency.",
        ),
        FailureCategory.ARTIFACT_INVALID: (
            "不要读取或修复损坏运行；保留目录并检查清单、哈希和完成标记。",
            "Do not consume or auto-repair the run; inspect its manifest, hashes and completion marker.",
        ),
        FailureCategory.UNKNOWN: (
            "保留完整日志和异常链；当前证据不足，不能确认根因。",
            "Keep the full log and exception chain; current evidence is insufficient to identify a cause.",
        ),
    }[category]


def _summary(category: FailureCategory) -> str:
    return {
        FailureCategory.INPUT_INVALID: "输入或参数校验失败 / Input or parameter validation failed",
        FailureCategory.INFEASIBLE: "求解器明确报告模型不可行 / Solver explicitly reported infeasibility",
        FailureCategory.SOLVER_UNAVAILABLE: "求解器不可用 / Solver unavailable",
        FailureCategory.SOLVER_TIMEOUT: "求解阶段超时 / Solver stage timed out",
        FailureCategory.SOLVER_ERROR: "求解器执行失败或非最优终止 / Solver failed or stopped non-optimally",
        FailureCategory.CANCELLED: "任务已取消 / Task cancelled",
        FailureCategory.AUDIT_FAILED: "独立审计阶段失败 / Independent audit failed",
        FailureCategory.ARTIFACT_INVALID: "运行产物校验失败 / Run artifact validation failed",
        FailureCategory.UNKNOWN: "根因无法确认 / Root cause is unknown",
    }[category]


def diagnose_failure(error: BaseException, *,
                     stage_hint: FailureStage = FailureStage.UNKNOWN) -> RunDiagnostic:
    """Return a conservative diagnosis; never raise or mutate run parameters."""
    try:
        chain = _chain(error)
        cause_chain = tuple(f"{type(item).__name__}: {_safe_text(item)}" for item in chain)
        evidence = [f"exception_type={type(error).__name__}"]
        artifact = next((item for item in chain if isinstance(item, RunArtifactError)), None)
        stage = _stage(getattr(artifact, "stage", None), stage_hint)
        reason = getattr(artifact, "reason", None)
        stderr = (getattr(artifact, "stderr", None) or "").strip()
        if artifact is not None:
            evidence.extend((f"stage={stage.value}", f"reason={reason or 'unspecified'}"))

        category = FailureCategory.UNKNOWN
        solver_status = termination = None
        if reason == "cancelled":
            category = FailureCategory.CANCELLED
        elif stage is FailureStage.INPUT:
            category = FailureCategory.INPUT_INVALID
        elif stage is FailureStage.AUDIT:
            category = FailureCategory.AUDIT_FAILED
        elif stage is FailureStage.ARTIFACT:
            category = FailureCategory.ARTIFACT_INVALID
        elif stage is FailureStage.SOLVE and reason == "timeout":
            category = FailureCategory.SOLVER_TIMEOUT
        elif stage is FailureStage.SOLVE and reason == "command_failed":
            match = _NONOPTIMAL.fullmatch(stderr)
            if match:
                solver_status, termination = match.groups()
                category = (FailureCategory.INFEASIBLE if termination == "infeasible"
                            else FailureCategory.SOLVER_ERROR)
                evidence.extend((f"solver_status={solver_status}",
                                 f"termination_condition={termination}"))
            elif _UNAVAILABLE.fullmatch(stderr):
                category = FailureCategory.SOLVER_UNAVAILABLE
            elif _SOLVER_FAILED.fullmatch(stderr):
                category = FailureCategory.SOLVER_ERROR
        elif stage is FailureStage.SOLVE and any(
                type(item).__name__ == "SolverError" for item in chain):
            category = FailureCategory.SOLVER_ERROR

        evidence.extend(cause_chain)
        return RunDiagnostic(
            category, stage, _summary(category), tuple(evidence),
            _suggestions(category), cause_chain, error,
            solver_status, termination)
    except Exception as diagnostic_error:
        fallback = (f"{type(error).__name__}: {_safe_text(error)}",
                    f"diagnostic_failure={type(diagnostic_error).__name__}: "
                    f"{_safe_text(diagnostic_error)}")
        return RunDiagnostic(
            FailureCategory.UNKNOWN, FailureStage.UNKNOWN,
            _summary(FailureCategory.UNKNOWN), fallback,
            _suggestions(FailureCategory.UNKNOWN), fallback, error)
