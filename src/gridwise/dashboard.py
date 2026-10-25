"""Offline dashboard orchestration; all metrics come from the data layer."""

import json
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .data import DataError, ImportResult, prepare_visualization_data


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_TEMPLATE = ROOT / "examples" / "synthetic-day.json"
AUDITOR = ROOT / "tools" / "audit_exports.py"
MAX_INTERACTIVE_PERIODS = 96


class DashboardError(RuntimeError):
    """The requested comparison cannot be shown reliably."""


@dataclass(frozen=True)
class DashboardResult:
    chart: dict
    audit_message: str


def _run_command(args, timeout_seconds, cancel_event):
    """Bound a child process and stop it when the desktop closes or input changes."""
    if cancel_event.is_set():
        raise DashboardError("run cancelled")
    process = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               errors="replace")
    started = time.monotonic()
    try:
        while True:
            if cancel_event.is_set():
                raise DashboardError("run cancelled")
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise DashboardError(f"process timed out after {timeout_seconds:g} s")
            try:
                stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                return process.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


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
                   audit_timeout_seconds: float = 120) -> DashboardResult:
    """Run both existing scenarios, independently audit exports, then prepare charts."""
    scenario = build_scenario(imported, template_path)
    cancel_event = cancel_event or threading.Event()
    if solve_timeout_seconds <= 0 or audit_timeout_seconds <= 0:
        raise DashboardError("process timeouts must be positive")
    try:
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-") as temporary:
            root = Path(temporary)
            source = root / "scenario.json"
            exports = root / "exports"
            source.write_text(json.dumps(scenario, ensure_ascii=False, allow_nan=False),
                              encoding="utf-8")
            code, stdout, stderr = _run_command(
                [sys.executable, "-m", "gridwise.cli", str(source), str(exports)],
                solve_timeout_seconds, cancel_event)
            if code != 0:
                raise DashboardError(f"solver failed (exit {code}): {(stderr or stdout).strip()}")
            summary = json.loads((exports / "summary.json").read_text(encoding="utf-8"))
            code, stdout, stderr = _run_command(
                [sys.executable, str(AUDITOR), str(source),
                 str(exports / "optimized.csv"), str(exports / "baseline.csv"),
                 str(exports / "summary.json")],
                audit_timeout_seconds, cancel_event)
            if code != 0 or "AUDIT PASSED" not in stdout:
                detail = (stderr or stdout).strip()
                raise DashboardError(f"independent export audit failed: {detail}")
            if cancel_event.is_set():
                raise DashboardError("run cancelled")
            chart = prepare_visualization_data(imported, summary)
            return DashboardResult(chart, "AUDIT PASSED / 独立审计通过")
    except (DataError, OSError, ValueError) as exc:
        raise DashboardError(f"comparison unavailable: {exc}") from exc
