"""Offline dashboard orchestration; all metrics come from the data layer."""

import json
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .data import DataError, ImportResult, prepare_visualization_data
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


@dataclass(frozen=True)
class RunHistoryEntry:
    name: str
    status: str
    detail: str


def default_runs_root() -> Path:
    return Path(tempfile.gettempdir()) / "gridwise-campus-runs"


def _chart_from_verified(imported: ImportResult, verified: VerifiedRun) -> dict:
    """Prepare charts only from bytes returned by the verified reader."""
    summary = json.loads(verified.files["summary.json"])
    return prepare_visualization_data(imported, summary)


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
                read_verified_run(path)
            except RunArtifactError as exc:
                status = "INCOMPLETE" if not (path / "COMPLETE").exists() else "DAMAGED"
                entries.append(RunHistoryEntry(path.name, status, str(exc)))
            else:
                entries.append(RunHistoryEntry(path.name, "VALID", "marker, manifest and files verified"))
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
        return DashboardResult(chart, "运行文件校验通过 / Verified run files", run_name)
    except (RunArtifactError, DataError, OSError, ValueError) as exc:
        raise DashboardError(f"历史运行不可用 / Historical run unavailable: {exc}") from exc


def build_scenario(imported: ImportResult, template_path: Path = SYNTHETIC_TEMPLATE) -> dict:
    """Use validated series and the documented synthetic equipment assumptions."""
    if not imported.report.ready or not imported.records:
        raise DashboardError("CSV quality check failed; no dispatch was run")
    if len(imported.records) > MAX_INTERACTIVE_PERIODS:
        raise DashboardError(f"interactive run supports at most {MAX_INTERACTIVE_PERIODS} periods")
    try:
        template = json.loads(Path(template_path).read_text(encoding="utf-8"))
        return {
            "power_unit": "kW", "energy_unit": "kWh", "price_unit": "CNY/kWh",
            "step_hours": float(imported.report.step_hours),
            "grid_max_kw": template["grid_max_kw"],
            "battery": template["battery"],
            "timeseries": [
                {"timestamp": row.timestamp, "load_kw": float(row.load_kw),
                 "pv_kw": float(row.pv_kw),
                 "price_cny_per_kwh": float(row.price_cny_per_kwh)}
                for row in imported.records
            ],
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DashboardError(f"synthetic equipment template unavailable: {exc}") from exc


def run_comparison(imported: ImportResult, template_path: Path = SYNTHETIC_TEMPLATE,
                   *, cancel_event: threading.Event | None = None,
                   solve_timeout_seconds: float = 120,
                   audit_timeout_seconds: float = 120,
                   runs_root: Path | None = None) -> DashboardResult:
    """Publish, independently audit, safely reread, then prepare charts."""
    scenario = build_scenario(imported, template_path)
    cancel_event = cancel_event or threading.Event()
    if solve_timeout_seconds <= 0 or audit_timeout_seconds <= 0:
        raise DashboardError("process timeouts must be positive")
    try:
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-") as temporary:
            source = Path(temporary) / "scenario.json"
            source.write_text(json.dumps(scenario, ensure_ascii=False, allow_nan=False),
                              encoding="utf-8")
            directory = publish_run(source, runs_root or default_runs_root(),
                                    cancel_event=cancel_event,
                                    solve_timeout_seconds=solve_timeout_seconds,
                                    audit_timeout_seconds=audit_timeout_seconds)
            if cancel_event.is_set():
                raise DashboardError("run cancelled")
            verified = read_verified_run(directory)
            chart = _chart_from_verified(imported, verified)
            return DashboardResult(chart, "AUDIT PASSED · VERIFIED / 独立审计及文件校验通过",
                                   directory.name)
    except (DataError, RunArtifactError, OSError, ValueError) as exc:
        raise DashboardError(f"comparison unavailable: {exc}") from exc
