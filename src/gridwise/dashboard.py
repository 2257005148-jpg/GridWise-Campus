"""Offline dashboard orchestration; all metrics come from the data layer."""

import json
import math
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .data import DataError, ImportResult, prepare_visualization_data
from .device import DeviceParameterError, DeviceParameters
from .run_artifacts import RUN_NAME, RunArtifactError, VerifiedRun, publish_run, read_verified_run


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_TEMPLATE = ROOT / "examples" / "synthetic-day.json"
MAX_INTERACTIVE_PERIODS = 96
MAX_HISTORY_ENTRIES = 50
MAX_HISTORY_SCAN = 1000


class DashboardError(RuntimeError):
    """The requested comparison cannot be shown reliably."""


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
    scenario = build_scenario(imported, template_path,
                              device_parameters=device_parameters)
    peak_cap_kw = _validated_peak_cap(peak_cap_kw, float(scenario["grid_max_kw"]))
    cancel_event = cancel_event or threading.Event()
    if solve_timeout_seconds <= 0 or audit_timeout_seconds <= 0:
        raise DashboardError("process timeouts must be positive")
    try:
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-") as temporary:
            source = Path(temporary) / "scenario.json"
            source.write_text(json.dumps(scenario, ensure_ascii=False, allow_nan=False),
                              encoding="utf-8")
            destination_root = Path(runs_root or default_runs_root())
            directory = publish_run(source, destination_root,
                                    cancel_event=cancel_event,
                                    solve_timeout_seconds=solve_timeout_seconds,
                                    audit_timeout_seconds=audit_timeout_seconds,
                                    peak_cap_kw=peak_cap_kw)
            if cancel_event.is_set():
                raise DashboardError("run cancelled")
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
        raise DashboardError(f"comparison unavailable: {exc}") from exc
