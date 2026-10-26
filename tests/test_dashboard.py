"""Offline dashboard controller and chart selection tests."""

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridwise import dashboard, run_artifacts
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
    assert result.run_name is not None
    verified = run_artifacts.read_verified_run(tmp_path / "gridwise-campus-runs" / result.run_name)
    assert verified.files["scenario.json"]
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


def test_peak_cap_comparison_publishes_verified_policy_and_metrics(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    result = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    assert result.optimization_mode == "peak_cap"
    assert result.peak_cap_kw == 55
    assert result.chart["metrics"]["optimized"]["peak_grid_kw"] <= 55 + 1e-5
    assert result.chart["metrics"]["baseline"]["peak_grid_kw"] == pytest.approx(60)
    verified = run_artifacts.read_verified_run(tmp_path / result.run_name)
    summary = json.loads(verified.files["summary.json"])
    assert summary["run"]["optimization_mode"] == "peak_cap"
    assert summary["run"]["peak_cap_kw"] == 55


def test_peak_cap_history_preserves_mode_and_parameter(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    created = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    entry = dashboard.list_run_history(tmp_path)[0]
    assert entry.name == created.run_name
    assert entry.optimization_mode == "peak_cap"
    assert entry.peak_cap_kw == 55
    loaded = dashboard.load_verified_comparison(imported, entry.name, tmp_path)
    assert loaded.optimization_mode == "peak_cap"
    assert loaded.peak_cap_kw == 55


def test_peak_cap_cost_delta_requires_verified_same_input_cost_run(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    without_reference = dashboard.run_comparison(
        imported, runs_root=tmp_path / "isolated", peak_cap_kw=55)
    assert without_reference.cost_mode_cost_cny is None

    cost = dashboard.run_comparison(imported, runs_root=tmp_path / "paired")
    peak = dashboard.run_comparison(imported, runs_root=tmp_path / "paired", peak_cap_kw=55)
    assert peak.cost_mode_cost_cny == pytest.approx(
        cost.chart["metrics"]["optimized"]["cost_cny"])
    assert (peak.chart["metrics"]["optimized"]["cost_cny"] - peak.cost_mode_cost_cny
            == pytest.approx(25.88891966759))
    loaded = dashboard.load_verified_comparison(
        imported, peak.run_name, tmp_path / "paired")
    assert loaded.cost_mode_cost_cny == pytest.approx(peak.cost_mode_cost_cny)


def test_peak_cap_above_scenario_grid_limit_fails_before_publication(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    monkeypatch.setattr(dashboard, "publish_run", lambda *_args, **_kwargs:
                        pytest.fail("invalid cap must not publish"))
    with pytest.raises(dashboard.DashboardError, match="exceeds scenario grid_max_kw"):
        dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=121)


def test_invalid_quality_blocks_solver_and_charts(tmp_path, monkeypatch):
    path = tmp_path / "bad.csv"
    path.write_text(HEADER + "2026-01-01T00:00:00+00:00,,0,0.5\n", encoding="utf-8")
    imported = import_energy_csv(path, 1)
    assert not imported.report.ready
    monkeypatch.setattr(dashboard, "publish_run", lambda *_args, **_kwargs:
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
    monkeypatch.setattr(dashboard, "publish_run", lambda *_args, **_kwargs:
                        pytest.fail("large solve must not run"))
    with pytest.raises(dashboard.DashboardError, match="at most 96"):
        dashboard.run_comparison(imported)


def test_audit_failure_blocks_chart_preparation(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    auditor = tmp_path / "bad-auditor.py"
    auditor.write_text("import sys\nsys.stderr.write('summary mismatch')\nsys.exit(2)\n",
                       encoding="utf-8")
    monkeypatch.setattr(run_artifacts, "AUDITOR", auditor)
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
    with pytest.raises(dashboard.DashboardError, match="no optimal solution"):
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
    with patch.object(run_artifacts.subprocess, "Popen", side_effect=capture):
        timer = threading.Timer(0.2, cancel.set)
        timer.start()
        try:
            with pytest.raises(run_artifacts.RunArtifactError, match="cancelled"):
                run_artifacts._execute([sys.executable, "-c", "import time; time.sleep(10)"],
                                       timeout=5, cancel_event=cancel)
        finally:
            timer.cancel()
    assert len(created) == 1
    assert created[0].poll() is not None


def test_history_classifies_and_rechecks_selected_run(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    name = first.run_name
    entries = dashboard.list_run_history(tmp_path)
    assert [(entry.name, entry.status) for entry in entries] == [(name, "VALID")]
    loaded = dashboard.load_verified_comparison(imported, name, tmp_path)
    assert loaded.chart == first.chart
    assert loaded.run_name == name
    target = tmp_path / name / "optimized.csv"
    target.write_bytes(target.read_bytes() + b"tampered")
    entries = dashboard.list_run_history(tmp_path)
    assert entries[0].status == "DAMAGED"
    with pytest.raises(dashboard.DashboardError, match="size or SHA-256 mismatch"):
        dashboard.load_verified_comparison(imported, name, tmp_path)


def test_history_rejects_incomplete_and_other_input(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    incomplete = tmp_path / ("run-" + "0" * 32)
    incomplete.mkdir()
    statuses = {entry.name: entry.status for entry in dashboard.list_run_history(tmp_path)}
    assert statuses[first.run_name] == "VALID"
    assert statuses[incomplete.name] == "INCOMPLETE"
    with pytest.raises(dashboard.DashboardError, match="Historical run unavailable"):
        dashboard.load_verified_comparison(imported, incomplete.name, tmp_path)
    different = tmp_path / "different.csv"
    different.write_text(HEADER +
                         "2026-01-01T00:00:00+00:00,1,0,0.5\n", encoding="utf-8")
    other = import_energy_csv(different, 1)
    assert other.report.ready
    with pytest.raises(dashboard.DashboardError, match="row count mismatch"):
        dashboard.load_verified_comparison(other, first.run_name, tmp_path)


def test_two_runs_keep_separate_verified_metrics(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    second = dashboard.run_comparison(imported, runs_root=tmp_path)
    assert first.run_name != second.run_name
    assert first.chart == second.chart
    assert len(list(tmp_path.glob("run-*"))) == 2
    assert dashboard.load_verified_comparison(imported, first.run_name, tmp_path).chart == first.chart
    assert dashboard.load_verified_comparison(imported, second.run_name, tmp_path).chart == second.chart


def test_failed_verified_read_never_prepares_chart(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)

    def fail_read(_directory):
        raise run_artifacts.RunArtifactError("synthetic manifest mismatch")

    monkeypatch.setattr(dashboard, "read_verified_run", fail_read)
    monkeypatch.setattr(dashboard, "prepare_visualization_data",
                        lambda *_args: pytest.fail("unverified charts must not be prepared"))
    with pytest.raises(dashboard.DashboardError, match="synthetic manifest mismatch"):
        dashboard.run_comparison(imported, runs_root=tmp_path)


def test_cancelled_history_load_never_reads_files(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(dashboard, "read_verified_run",
                        lambda _path: pytest.fail("cancelled history must not read"))
    with pytest.raises(dashboard.DashboardError, match="cancelled"):
        dashboard.load_verified_comparison(imported, "run-" + "0" * 32,
                                           tmp_path, cancel_event=cancelled)


def test_history_missing_root_is_empty_but_unreadable_root_fails(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    assert dashboard.list_run_history(root) == []
    root.mkdir()
    original = Path.iterdir

    def deny(path):
        if path == root:
            raise PermissionError("synthetic read denial")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", deny)
    with pytest.raises(dashboard.DashboardError, match="Cannot list runs root.*synthetic read denial"):
        dashboard.list_run_history(root)


def test_unwritable_runs_root_blocks_publication_and_chart(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    root = tmp_path / "unwritable"
    original = Path.mkdir

    def deny(path, *args, **kwargs):
        if path == root:
            raise PermissionError("synthetic write denial")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny)
    with pytest.raises(dashboard.DashboardError, match="synthetic write denial"):
        dashboard.run_comparison(imported, runs_root=root)
    assert not root.exists()
