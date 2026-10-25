"""Offline dashboard controller and chart selection tests."""

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

from gridwise import dashboard
from gridwise.data import import_energy_csv
from gridwise.desktop import CHARTS, chart_values


ROOT = dashboard.ROOT
SAMPLE = ROOT / "examples" / "synthetic-day.csv"
HEADER = "timestamp,load_kw,pv_kw,price_cny_per_kwh\n"


def test_valid_synthetic_csv_runs_audited_comparison(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    monkeypatch.setattr(dashboard.tempfile, "tempdir", str(tmp_path))
    result = dashboard.run_comparison(imported)
    chart = result.chart
    assert result.audit_message.startswith("AUDIT PASSED")
    assert len(chart["timestamps"]) == 6
    assert chart["metrics"]["optimized"]["cost_cny"] == pytest.approx(131.0110803324)
    assert chart["metrics"]["baseline"]["cost_cny"] == pytest.approx(162.5)
    assert chart["metrics"]["optimized"]["peak_grid_kw"] == 90
    assert chart["metrics"]["baseline"]["peak_grid_kw"] == 60
    assert chart["series"]["optimized_cumulative_cost_cny"][-1] == pytest.approx(
        chart["metrics"]["optimized"]["cost_cny"])
    for specification in CHARTS:
        stamps, series, unit = chart_values(chart, specification)
        assert stamps == chart["timestamps"]
        assert unit in {"kW", "kWh", "CNY"}
        assert all(len(values) == len(stamps) for _, _, values in series)
    assert list(tmp_path.glob("gridwise-ui-*")) == []


def test_invalid_quality_blocks_solver_and_charts(tmp_path, monkeypatch):
    path = tmp_path / "bad.csv"
    path.write_text(HEADER + "2026-01-01T00:00:00+00:00,,0,0.5\n", encoding="utf-8")
    imported = import_energy_csv(path, 1)
    assert not imported.report.ready
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *_args, **_kwargs:
                        pytest.fail("solver must not run"))
    with pytest.raises(dashboard.DashboardError, match="quality check failed"):
        dashboard.run_comparison(imported)


def test_interactive_period_limit_blocks_large_solve(tmp_path, monkeypatch):
    first = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = tmp_path / "many.csv"
    path.write_text(HEADER + "".join(
        f"{(first + timedelta(hours=index)).isoformat()},1,0,0.5\n"
        for index in range(dashboard.MAX_INTERACTIVE_PERIODS + 1)), encoding="utf-8")
    imported = import_energy_csv(path, 1)
    assert imported.report.ready
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda *_args, **_kwargs:
                        pytest.fail("large solve must not run"))
    with pytest.raises(dashboard.DashboardError, match="at most 96"):
        dashboard.run_comparison(imported)


def test_audit_failure_blocks_chart_preparation(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    auditor = tmp_path / "bad-auditor.py"
    auditor.write_text("import sys\nsys.stderr.write('summary mismatch')\nsys.exit(2)\n",
                       encoding="utf-8")
    monkeypatch.setattr(dashboard, "AUDITOR", auditor)
    monkeypatch.setattr(dashboard.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(dashboard, "prepare_visualization_data",
                        lambda *_args: pytest.fail("charts must not be prepared"))
    with pytest.raises(dashboard.DashboardError, match="summary mismatch"):
        dashboard.run_comparison(imported)
    assert list(tmp_path.glob("gridwise-ui-*")) == []


def test_chart_selection_rejects_mismatched_series_length():
    chart = {"timestamps": ["2026-01-01T00:00:00+00:00"],
             "series": {"load_kw": [], "pv_kw": [0]}}
    with pytest.raises(ValueError, match="load_kw: length mismatch"):
        chart_values(chart, CHARTS[0])


def test_solver_timeout_fails_and_cleans_temporary_files(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    monkeypatch.setattr(dashboard.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(dashboard.DashboardError, match="timed out"):
        dashboard.run_comparison(imported, solve_timeout_seconds=0.001)
    assert list(tmp_path.glob("gridwise-ui-*")) == []


def test_solver_failure_fails_closed_and_cleans_temporary_files(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    template = json.loads(dashboard.SYNTHETIC_TEMPLATE.read_text(encoding="utf-8"))
    template["grid_max_kw"] = 0
    template_path = tmp_path / "infeasible.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    monkeypatch.setattr(dashboard.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(dashboard.DashboardError, match="solver failed"):
        dashboard.run_comparison(imported, template_path)
    assert list(tmp_path.glob("gridwise-ui-*")) == []


def test_cancelled_child_process_is_reaped():
    cancel = threading.Event()
    created = []
    real_popen = subprocess.Popen

    def capture(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        created.append(process)
        return process

    from unittest.mock import patch
    with patch.object(dashboard.subprocess, "Popen", side_effect=capture):
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            with pytest.raises(dashboard.DashboardError, match="cancelled"):
                dashboard._run_command([sys.executable, "-c", "import time; time.sleep(10)"],
                                       timeout_seconds=5, cancel_event=cancel)
        finally:
            timer.cancel()
    assert len(created) == 1
    assert created[0].poll() is not None
