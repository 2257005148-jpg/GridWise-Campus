"""Offline dashboard orchestration; all metrics come from the data layer."""

import json
import math
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .data import DataError, ImportResult, prepare_visualization_data
from .device import DeviceParameterError, DeviceParameters
from .diagnostics import FailureStage, RunDiagnostic, diagnose_failure
from .run_artifacts import RUN_NAME, RunArtifactError, VerifiedRun, publish_run, read_verified_run


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_TEMPLATE = ROOT / "examples" / "synthetic-day.json"
MAX_INTERACTIVE_PERIODS = 96
MAX_HISTORY_ENTRIES = 50
MAX_HISTORY_SCAN = 1000
HISTORY_STATUSES = frozenset({"VALID", "INCOMPLETE", "DAMAGED"})
HISTORY_MODES = frozenset({"cost", "peak_cap"})
HISTORY_SORT_KEYS = frozenset({"name", "status", "optimization_mode", "peak_cap_kw"})


class DashboardError(RuntimeError):
    """The requested comparison cannot be shown reliably."""

    def __init__(self, message, *, diagnostic: RunDiagnostic | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class DashboardResult:
    chart: dict
    audit_message: str
    run_name: str | None = None
    optimization_mode: str = "cost"
    peak_cap_kw: float | None = None
    cost_mode_cost_cny: float | None = None
    device_parameters: DeviceParameters | None = None


@dataclass(frozen=True)
class RunHistoryEntry:
    name: str
    status: str
    detail: str
    optimization_mode: str | None = None
    peak_cap_kw: float | None = None


def filter_run_history(entries, *, query: str = "", status: str | None = None,
                       optimization_mode: str | None = None) -> list[RunHistoryEntry]:
    """Filter one already-enumerated history snapshot without filesystem access."""
    if status is not None and status not in HISTORY_STATUSES:
        raise ValueError(f"unsupported history status: {status}")
    if optimization_mode is not None and optimization_mode not in HISTORY_MODES:
        raise ValueError(f"unsupported optimization mode: {optimization_mode}")
    needle = str(query).strip().casefold()
    return [
        entry for entry in entries
        if (not needle or needle in entry.name.casefold())
        and (status is None or entry.status == status)
        and (optimization_mode is None or entry.optimization_mode == optimization_mode)
    ]


def sort_run_history(entries, *, sort_key: str = "name",
                     descending: bool = False) -> list[RunHistoryEntry]:
    """Stably sort an in-memory history snapshot; missing peak caps stay last."""
    if sort_key not in HISTORY_SORT_KEYS:
        raise ValueError(f"unsupported history sort key: {sort_key}")
    items = list(entries)
    if sort_key == "peak_cap_kw":
        present = [entry for entry in items if entry.peak_cap_kw is not None]
        missing = [entry for entry in items if entry.peak_cap_kw is None]
        present.sort(key=lambda entry: float(entry.peak_cap_kw), reverse=descending)
        return present + missing
    if sort_key == "name":
        key = lambda entry: entry.name.casefold()
    elif sort_key == "status":
        order = {"VALID": 0, "INCOMPLETE": 1, "DAMAGED": 2}
        key = lambda entry: order[entry.status]
    else:
        order = {"cost": 0, "peak_cap": 1, None: 2}
        key = lambda entry: order[entry.optimization_mode]
    return sorted(items, key=key, reverse=descending)


@dataclass(frozen=True)
class ComparisonMetrics:
    """The three independently recalculated metrics shown to the user."""

    cost_cny: float
    grid_energy_kwh: float
    peak_grid_kw: float


@dataclass(frozen=True)
class RunHistoryPreview:
    """One safely reread historical result aligned with the current checked CSV."""

    run_name: str
    optimization_mode: str
    peak_cap_kw: float | None
    metrics: ComparisonMetrics
    units: tuple[tuple[str, str], ...] = (
        ("cost_cny", "CNY"),
        ("grid_energy_kwh", "kWh"),
        ("peak_grid_kw", "kW"),
    )
    integrity_status: str = "VERIFIED"


@dataclass(frozen=True)
class ComparedRun:
    """One verified side of a historical-run comparison."""

    name: str
    optimization_mode: str
    peak_cap_kw: float | None
    metrics: ComparisonMetrics
    device_parameters: DeviceParameters
    model_version: str
    solver_name: str
    chart: dict | None = None


@dataclass(frozen=True)
class HistoricalRunComparison:
    """A complete two-run comparison returned only after all checks pass."""

    run_a: ComparedRun
    run_b: ComparedRun
    delta_b_minus_a: ComparisonMetrics
    units: dict[str, str]
    scenario_sha256: str
    integrity_status: str = "VERIFIED"
    fairness_status: str = "MATCHED"


def default_runs_root() -> Path:
    return Path(tempfile.gettempdir()) / "gridwise-campus-runs"


def _chart_from_verified(imported: ImportResult, verified: VerifiedRun) -> dict:
    """Prepare charts only from bytes returned by the verified reader."""
    summary = json.loads(verified.files["summary.json"])
    return prepare_visualization_data(imported, summary)


def _policy_from_verified(verified: VerifiedRun) -> tuple[str, float | None]:
    mode = verified.metadata.get("optimization_mode", "cost")
    cap = verified.metadata.get("peak_cap_kw")
    return mode, float(cap) if cap is not None else None


def _devices_from_verified(verified: VerifiedRun) -> DeviceParameters:
    scenario = json.loads(verified.files["scenario.json"])
    return DeviceParameters.from_scenario(scenario)


def _model_source_digest(version: str) -> str | None:
    marker = "+sha256."
    return version.split(marker, 1)[1] if marker in version else None


def _comparison_metrics(chart: dict, kind: str = "optimized") -> ComparisonMetrics:
    metrics = chart["metrics"][kind]
    return ComparisonMetrics(float(metrics["cost_cny"]),
                             float(metrics["grid_energy_kwh"]),
                             float(metrics["peak_grid_kw"]))


def _compared_run(verified: VerifiedRun, chart: dict) -> ComparedRun:
    mode, cap = _policy_from_verified(verified)
    return ComparedRun(verified.path.name, mode, cap,
                       _comparison_metrics(chart),
                       _devices_from_verified(verified),
                       str(verified.metadata["model_version"]),
                       str(verified.metadata["solver_name"]),
                       chart)


def compare_verified_runs(imported: ImportResult, run_a_name: str, run_b_name: str,
                          runs_root: Path, *,
                          cancel_event: threading.Event | None = None) -> HistoricalRunComparison:
    """Safely reread and fairly compare two complete historical runs."""
    if not imported.report.ready or not imported.records:
        raise DashboardError("CSV quality check failed; history cannot be compared")
    if not RUN_NAME.fullmatch(run_a_name) or not RUN_NAME.fullmatch(run_b_name):
        raise DashboardError("运行名称无效 / Invalid run name")
    if run_a_name == run_b_name:
        raise DashboardError("请选择两个不同运行 / Select two distinct runs")
    if cancel_event is not None and cancel_event.is_set():
        raise DashboardError("history comparison cancelled")
    try:
        verified_a = read_verified_run(Path(runs_root) / run_a_name)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history comparison cancelled")
        verified_b = read_verified_run(Path(runs_root) / run_b_name)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history comparison cancelled")
        chart_a = _chart_from_verified(imported, verified_a)
        chart_b = _chart_from_verified(imported, verified_b)
        scenario_digest = verified_a.metadata["scenario_sha256"]
        if verified_b.metadata["scenario_sha256"] != scenario_digest:
            raise DashboardError("场景摘要不一致 / Scenario SHA-256 differs")
        if verified_a.metadata["solver_name"] != verified_b.metadata["solver_name"]:
            raise DashboardError("求解器不一致 / Solver differs")
        digest_a = _model_source_digest(str(verified_a.metadata["model_version"]))
        digest_b = _model_source_digest(str(verified_b.metadata["model_version"]))
        if digest_a is None or digest_b is None or digest_a != digest_b:
            raise DashboardError("模型源码摘要不一致 / Model source digest differs")
        devices_a = _devices_from_verified(verified_a)
        devices_b = _devices_from_verified(verified_b)
        if devices_a != devices_b:
            raise DashboardError("设备参数不一致 / Device parameters differ")
        baseline_a = _comparison_metrics(chart_a, "baseline")
        baseline_b = _comparison_metrics(chart_b, "baseline")
        if any(not math.isclose(getattr(baseline_a, field), getattr(baseline_b, field),
                                rel_tol=0, abs_tol=1e-5)
               for field in ("cost_cny", "grid_energy_kwh", "peak_grid_kw")):
            raise DashboardError("无储能基线指标不一致 / Baseline metrics differ")
        run_a = _compared_run(verified_a, chart_a)
        run_b = _compared_run(verified_b, chart_b)
        delta = ComparisonMetrics(
            run_b.metrics.cost_cny - run_a.metrics.cost_cny,
            run_b.metrics.grid_energy_kwh - run_a.metrics.grid_energy_kwh,
            run_b.metrics.peak_grid_kw - run_a.metrics.peak_grid_kw,
        )
        return HistoricalRunComparison(
            run_a, run_b, delta,
            {"cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"},
            str(scenario_digest),
        )
    except DashboardError:
        raise
    except (RunArtifactError, DataError, DeviceParameterError, OSError,
            ValueError, KeyError, TypeError) as exc:
        raise DashboardError(
            f"历史运行不可比较 / Historical runs unavailable for comparison: {exc}") from exc


def _matching_cost_mode_cost(imported: ImportResult, target: VerifiedRun,
                             runs_root: Path) -> float | None:
    """Return a cost-only result only when source, input and solver context match."""
    if target.metadata.get("optimization_mode") != "peak_cap":
        return None
    target_digest = _model_source_digest(target.metadata["model_version"])
    try:
        entries = list_run_history(runs_root)
    except DashboardError:
        return None
    for entry in entries:
        if entry.status != "VALID" or entry.optimization_mode != "cost" or \
                entry.name == target.path.name:
            continue
        try:
            candidate = read_verified_run(Path(runs_root) / entry.name)
            if candidate.metadata["scenario_sha256"] != target.metadata["scenario_sha256"] or \
                    candidate.metadata["solver_name"] != target.metadata["solver_name"] or \
                    _model_source_digest(candidate.metadata["model_version"]) != target_digest:
                continue
            chart = _chart_from_verified(imported, candidate)
            return float(chart["metrics"]["optimized"]["cost_cny"])
        except (RunArtifactError, DataError, OSError, ValueError, KeyError, TypeError):
            continue
    return None


def list_run_history(runs_root: Path, *,
                     cancel_event: threading.Event | None = None) -> list[RunHistoryEntry]:
    """Classify a bounded set of local run directories without modifying them."""
    root = Path(runs_root)
    try:
        if not root.exists():
            return []
        if not root.is_dir() or root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise DashboardError(f"运行根目录无效 / Invalid runs root: {root}")
        candidates = []
        for scanned, path in enumerate(root.iterdir(), 1):
            if cancel_event is not None and cancel_event.is_set():
                raise DashboardError("history refresh cancelled")
            if scanned > MAX_HISTORY_SCAN:
                raise DashboardError(f"运行目录项过多 / Too many entries: {root}")
            if RUN_NAME.fullmatch(path.name):
                candidates.append(path)
                if len(candidates) > MAX_HISTORY_ENTRIES:
                    raise DashboardError(f"运行目录过多 / Too many runs: {root}")
        entries = []
        for path in sorted(candidates, key=lambda item: item.name):
            if cancel_event is not None and cancel_event.is_set():
                raise DashboardError("history refresh cancelled")
            try:
                verified = read_verified_run(path)
            except RunArtifactError as exc:
                status = "INCOMPLETE" if not (path / "COMPLETE").exists() else "DAMAGED"
                entries.append(RunHistoryEntry(path.name, status, str(exc)))
            else:
                mode, cap = _policy_from_verified(verified)
                entries.append(RunHistoryEntry(path.name, "VALID",
                                               "marker, manifest and files verified",
                                               mode, cap))
        return entries
    except OSError as exc:
        raise DashboardError(f"无法读取运行目录 / Cannot list runs root {root}: {exc}") from exc


def preview_verified_run(imported: ImportResult, run_name: str,
                         runs_root: Path, *,
                         cancel_event: threading.Event | None = None) -> RunHistoryPreview:
    """Safely reread one run and reuse the existing metric preparation path."""
    if not imported.report.ready or not imported.records:
        raise DashboardError("CSV quality check failed; history cannot be previewed")
    if not RUN_NAME.fullmatch(run_name):
        raise DashboardError(f"运行名称无效 / Invalid run name: {run_name}")
    if cancel_event is not None and cancel_event.is_set():
        raise DashboardError("history preview cancelled")
    try:
        verified = read_verified_run(Path(runs_root) / run_name)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history preview cancelled")
        chart = _chart_from_verified(imported, verified)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history preview cancelled")
        mode, cap = _policy_from_verified(verified)
        return RunHistoryPreview(
            run_name, mode, cap, _comparison_metrics(chart))
    except DashboardError:
        raise
    except (RunArtifactError, DataError, OSError, ValueError, KeyError, TypeError) as exc:
        raise DashboardError(
            f"历史运行无法预览 / Historical run cannot be previewed: {exc}") from exc


def load_verified_comparison(imported: ImportResult, run_name: str,
                             runs_root: Path, *,
                             cancel_event: threading.Event | None = None) -> DashboardResult:
    """Reverify a selected historical run against the current checked CSV."""
    if not imported.report.ready or not imported.records:
        raise DashboardError("CSV quality check failed; history cannot be shown")
    if not RUN_NAME.fullmatch(run_name):
        raise DashboardError(f"运行名称无效 / Invalid run name: {run_name}")
    if cancel_event is not None and cancel_event.is_set():
        raise DashboardError("history load cancelled")
    try:
        verified = read_verified_run(Path(runs_root) / run_name)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history load cancelled")
        chart = _chart_from_verified(imported, verified)
        if cancel_event is not None and cancel_event.is_set():
            raise DashboardError("history load cancelled")
        mode, cap = _policy_from_verified(verified)
        cost_mode_cost = _matching_cost_mode_cost(imported, verified, Path(runs_root))
        return DashboardResult(chart, "运行文件校验通过 / Verified run files", run_name,
                               mode, cap, cost_mode_cost,
                               _devices_from_verified(verified))
    except (RunArtifactError, DataError, DeviceParameterError, OSError, ValueError) as exc:
        raise DashboardError(f"历史运行不可用 / Historical run unavailable: {exc}") from exc


def default_device_parameters(template_path: Path = SYNTHETIC_TEMPLATE) -> DeviceParameters:
    """Load the documented default equipment values from the synthetic template."""
    try:
        template = json.loads(Path(template_path).read_text(encoding="utf-8"))
        return DeviceParameters.from_scenario(template)
    except (OSError, ValueError, KeyError, TypeError, DeviceParameterError) as exc:
        raise DashboardError(f"synthetic equipment template unavailable: {exc}") from exc


def build_scenario(imported: ImportResult, template_path: Path = SYNTHETIC_TEMPLATE,
                   *, device_parameters: DeviceParameters | None = None) -> dict:
    """Combine a validated series with validated grid and battery parameters."""
    if not imported.report.ready or not imported.records:
        raise DashboardError("CSV quality check failed; no dispatch was run")
    if len(imported.records) > MAX_INTERACTIVE_PERIODS:
        raise DashboardError(f"interactive run supports at most {MAX_INTERACTIVE_PERIODS} periods")
    try:
        devices = device_parameters or default_device_parameters(template_path)
        equipment = devices.to_scenario_fields()
        return {
            "power_unit": "kW", "energy_unit": "kWh", "price_unit": "CNY/kWh",
            "step_hours": float(imported.report.step_hours),
            "grid_max_kw": equipment["grid_max_kw"],
            "battery": equipment["battery"],
            "timeseries": [
                {"timestamp": row.timestamp, "load_kw": float(row.load_kw),
                 "pv_kw": float(row.pv_kw),
                 "price_cny_per_kwh": float(row.price_cny_per_kwh)}
                for row in imported.records
            ],
        }
    except (ValueError, KeyError, TypeError, DeviceParameterError) as exc:
        raise DashboardError(f"invalid device parameters: {exc}") from exc


def _validated_peak_cap(value, grid_max_kw: float) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise DashboardError("peak cap must be a finite nonnegative number")
    value = float(value)
    if value > grid_max_kw:
        raise DashboardError(
            f"峰值上限 {value:g} kW 超过场景电网功率上限 {grid_max_kw:g} kW / "
            "Peak cap exceeds scenario grid_max_kw")
    return value


def run_comparison(imported: ImportResult, template_path: Path = SYNTHETIC_TEMPLATE,
                   *, cancel_event: threading.Event | None = None,
                   solve_timeout_seconds: float = 120,
                   audit_timeout_seconds: float = 120,
                   runs_root: Path | None = None,
                   peak_cap_kw: float | None = None,
                   device_parameters: DeviceParameters | None = None) -> DashboardResult:
    """Publish, independently audit, safely reread, then prepare charts."""
    try:
        scenario = build_scenario(imported, template_path,
                                  device_parameters=device_parameters)
        peak_cap_kw = _validated_peak_cap(peak_cap_kw, float(scenario["grid_max_kw"]))
    except DashboardError as exc:
        raise DashboardError(
            str(exc), diagnostic=diagnose_failure(
                exc, stage_hint=FailureStage.INPUT)) from exc
    cancel_event = cancel_event or threading.Event()
    if solve_timeout_seconds <= 0 or audit_timeout_seconds <= 0:
        raise DashboardError("process timeouts must be positive")
    stage = FailureStage.ARTIFACT
    try:
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-") as temporary:
            source = Path(temporary) / "scenario.json"
            source.write_text(json.dumps(scenario, ensure_ascii=False, allow_nan=False),
                              encoding="utf-8")
            destination_root = Path(runs_root or default_runs_root())
            stage = FailureStage.SOLVE
            directory = publish_run(source, destination_root,
                                    cancel_event=cancel_event,
                                    solve_timeout_seconds=solve_timeout_seconds,
                                    audit_timeout_seconds=audit_timeout_seconds,
                                    peak_cap_kw=peak_cap_kw)
            if cancel_event.is_set():
                cancelled = RunArtifactError(
                    "run cancelled", stage="control", reason="cancelled")
                raise DashboardError(
                    "run cancelled", diagnostic=diagnose_failure(cancelled))
            stage = FailureStage.ARTIFACT
            verified = read_verified_run(directory)
            chart = _chart_from_verified(imported, verified)
            summary = json.loads(verified.files["summary.json"])
            run = summary["run"]
            cost_mode_cost = _matching_cost_mode_cost(imported, verified, destination_root)
            return DashboardResult(chart, "AUDIT PASSED · VERIFIED / 独立审计及文件校验通过",
                                   directory.name,
                                   run.get("optimization_mode", "cost"),
                                   run.get("peak_cap_kw"),
                                   cost_mode_cost,
                                   _devices_from_verified(verified))
    except (DataError, DeviceParameterError, RunArtifactError, OSError, ValueError) as exc:
        raise DashboardError(
            f"comparison unavailable: {exc}",
            diagnostic=diagnose_failure(exc, stage_hint=stage)) from exc
