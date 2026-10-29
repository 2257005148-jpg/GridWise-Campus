"""Offline dashboard controller and chart selection tests."""

import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridwise import dashboard, run_artifacts
from gridwise.data import import_energy_csv
from gridwise.desktop import CHARTS, chart_values
from gridwise.device import DeviceParameters
from gridwise.diagnostics import FailureCategory


ROOT = dashboard.ROOT
SAMPLE = ROOT / "examples" / "synthetic-day.csv"
HEADER = "timestamp,load_kw,pv_kw,price_cny_per_kwh\n"


def write_energy_csv(path, rows):
    path.write_text(HEADER + "".join(rows), encoding="utf-8", newline="")
    return path


def test_build_scenario_uses_validated_custom_device_parameters():
    imported = import_energy_csv(SAMPLE, 1)
    devices = DeviceParameters(75, 40, 10, 12, 8, 9, 0.9, 0.85)

    scenario = dashboard.build_scenario(imported, device_parameters=devices)

    assert scenario["grid_max_kw"] == 75
    assert scenario["battery"] == {
        "capacity_kwh": 40,
        "initial_kwh": 10,
        "terminal_kwh": 12,
        "max_charge_kw": 8,
        "max_discharge_kw": 9,
        "charge_efficiency": 0.9,
        "discharge_efficiency": 0.85,
    }
    assert scenario["timeseries"][0]["load_kw"] == 60


def test_default_device_serialization_preserves_template_number_representation():
    imported = import_energy_csv(SAMPLE, 1)
    template = json.loads(dashboard.SYNTHETIC_TEMPLATE.read_text(encoding="utf-8"))

    scenario = dashboard.build_scenario(imported)

    assert scenario["grid_max_kw"] == template["grid_max_kw"]
    assert scenario["battery"] == template["battery"]
    assert type(scenario["grid_max_kw"]) is type(template["grid_max_kw"])
    for field, value in template["battery"].items():
        assert type(scenario["battery"][field]) is type(value)


def test_custom_devices_flow_through_both_modes_verified_artifacts_and_history(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    devices = DeviceParameters(100, 60, 30, 30, 20, 20, 0.9, 0.9)
    runs_root = tmp_path / "runs"

    cost = dashboard.run_comparison(
        imported, runs_root=runs_root, device_parameters=devices)
    capped = dashboard.run_comparison(
        imported, runs_root=runs_root, peak_cap_kw=55,
        device_parameters=devices)

    assert cost.device_parameters == devices
    assert capped.device_parameters == devices
    assert capped.chart["metrics"]["optimized"]["peak_grid_kw"] <= 55 + 1e-5
    assert capped.cost_mode_cost_cny == pytest.approx(
        cost.chart["metrics"]["optimized"]["cost_cny"])
    verified = run_artifacts.read_verified_run(runs_root / capped.run_name)
    scenario = json.loads(verified.files["scenario.json"])
    assert DeviceParameters.from_scenario(scenario) == devices
    assert scenario["battery"]["terminal_kwh"] == 30
    summary = json.loads(verified.files["summary.json"])
    assert summary["optimized"]["rows"][-1]["soc_kwh"] == pytest.approx(30)

    restored = dashboard.load_verified_comparison(imported, capped.run_name, runs_root)
    assert restored.device_parameters == devices
    assert restored.optimization_mode == "peak_cap"


def test_valid_synthetic_csv_runs_audited_comparison(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    monkeypatch.setattr(dashboard.tempfile, "tempdir", str(tmp_path))
    result = dashboard.run_comparison(imported)
    chart = result.chart
    assert result.audit_message.startswith("AUDIT PASSED")
    assert result.run_name is not None
    assert result.device_parameters == dashboard.default_device_parameters()
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


@pytest.mark.parametrize("suffix", ["Z", "+0000", "+00:00"])
def test_custom_timestamp_spellings_complete_verified_dashboard_flow(tmp_path, suffix):
    path = write_energy_csv(tmp_path / "custom.csv", [
        f"2026-03-01T00:00:00{suffix},20,0,0.4\n",
        f"2026-03-01T00:30:00{suffix},25,5,0.8\n",
    ])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    imported = import_energy_csv(path, 0.5)

    result = dashboard.run_comparison(imported, runs_root=tmp_path / "runs")

    assert result.audit_message.startswith("AUDIT PASSED")
    assert result.chart["timestamps"] == [
        "2026-03-01T00:00:00+00:00",
        "2026-03-01T00:30:00+00:00",
    ]
    verified = run_artifacts.read_verified_run(tmp_path / "runs" / result.run_name)
    scenario = json.loads(verified.files["scenario.json"])
    assert [row["timestamp"] for row in scenario["timeseries"]] == result.chart["timestamps"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_custom_half_hour_csv_runs_cost_and_peak_cap_modes(tmp_path):
    path = write_energy_csv(tmp_path / "custom-modes.csv", [
        "2026-03-01T08:00:00+08:00,45,0,0.3\n",
        "2026-03-01T08:30:00+08:00,50,10,0.4\n",
        "2026-03-01T09:00:00+08:00,60,20,1.0\n",
        "2026-03-01T09:30:00+08:00,55,5,0.8\n",
    ])
    imported = import_energy_csv(path, 0.5)
    runs_root = tmp_path / "runs"
    devices = DeviceParameters(90, 50, 25, 25, 15, 15, 0.92, 0.9)

    cost = dashboard.run_comparison(
        imported, runs_root=runs_root, device_parameters=devices)
    peak = dashboard.run_comparison(
        imported, runs_root=runs_root, peak_cap_kw=55,
        device_parameters=devices)

    assert cost.optimization_mode == "cost"
    assert peak.optimization_mode == "peak_cap"
    assert peak.peak_cap_kw == 55
    assert peak.chart["metrics"]["optimized"]["peak_grid_kw"] <= 55 + 1e-5
    assert peak.cost_mode_cost_cny == pytest.approx(cost.chart["metrics"]["optimized"]["cost_cny"])
    verified = run_artifacts.read_verified_run(runs_root / peak.run_name)
    assert DeviceParameters.from_scenario(json.loads(verified.files["scenario.json"])) == devices
    summary = json.loads(verified.files["summary.json"])
    assert all(row["charge_kw"] <= devices.max_charge_kw + 1e-5
               for row in summary["optimized"]["rows"])
    assert all(row["discharge_kw"] <= devices.max_discharge_kw + 1e-5
               for row in summary["optimized"]["rows"])
    assert all(row["soc_kwh"] <= devices.capacity_kwh + 1e-5
               for row in summary["optimized"]["rows"])
    assert summary["optimized"]["rows"][-1]["soc_kwh"] == pytest.approx(
        devices.terminal_kwh)


def test_valid_but_infeasible_custom_devices_fail_without_completed_run(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    devices = DeviceParameters(0, 80, 0, 0, 0, 0, 0.95, 0.95)
    runs_root = tmp_path / "runs"

    with pytest.raises(dashboard.DashboardError, match="no optimal solution"):
        dashboard.run_comparison(
            imported, runs_root=runs_root, device_parameters=devices)

    directories = list(runs_root.glob("run-*"))
    assert len(directories) == 1
    assert not (directories[0] / "COMPLETE").exists()


def test_infeasible_dashboard_error_exposes_evidence_backed_diagnostic(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    devices = DeviceParameters(0, 80, 0, 0, 0, 0, 0.95, 0.95)

    with pytest.raises(dashboard.DashboardError) as caught:
        dashboard.run_comparison(
            imported, runs_root=tmp_path, device_parameters=devices)

    diagnostic = caught.value.diagnostic
    assert diagnostic.category is FailureCategory.INFEASIBLE
    assert diagnostic.solver_status == "error"
    assert diagnostic.termination_condition == "infeasible"
    assert any("termination_condition=infeasible" in item for item in diagnostic.evidence)


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


def test_verified_history_runs_compare_metrics_and_signed_delta(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    cost = dashboard.run_comparison(imported, runs_root=tmp_path)
    capped = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)

    comparison = dashboard.compare_verified_runs(
        imported, cost.run_name, capped.run_name, tmp_path)

    assert comparison.integrity_status == "VERIFIED"
    assert comparison.fairness_status == "MATCHED"
    assert comparison.run_a.name == cost.run_name
    assert comparison.run_a.optimization_mode == "cost"
    assert comparison.run_b.name == capped.run_name
    assert comparison.run_b.optimization_mode == "peak_cap"
    assert comparison.run_b.peak_cap_kw == 55
    assert comparison.run_a.chart == cost.chart
    assert comparison.run_b.chart == capped.chart
    assert comparison.units == {
        "cost_cny": "CNY",
        "grid_energy_kwh": "kWh",
        "peak_grid_kw": "kW",
    }
    assert comparison.run_a.metrics.cost_cny == pytest.approx(131.01108033241)
    assert comparison.run_b.metrics.cost_cny == pytest.approx(156.9)
    assert comparison.delta_b_minus_a.cost_cny == pytest.approx(25.88891966759)
    assert comparison.delta_b_minus_a.grid_energy_kwh == pytest.approx(
        243.9 - 247.02216066482)
    assert comparison.delta_b_minus_a.peak_grid_kw == pytest.approx(-35)


def test_history_comparison_rejects_same_run_identifier(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    created = dashboard.run_comparison(imported, runs_root=tmp_path)

    with pytest.raises(dashboard.DashboardError, match="two distinct runs"):
        dashboard.compare_verified_runs(
            imported, created.run_name, created.run_name, tmp_path)


def test_history_comparison_rejects_different_scenario_and_devices(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    changed_devices = DeviceParameters(120, 60, 30, 30, 20, 20, 0.95, 0.95)
    second = dashboard.run_comparison(
        imported, runs_root=tmp_path, device_parameters=changed_devices)

    with pytest.raises(dashboard.DashboardError, match="Scenario SHA-256 differs"):
        dashboard.compare_verified_runs(
            imported, first.run_name, second.run_name, tmp_path)


@pytest.mark.parametrize(("field", "value", "message"), [
    ("solver_name", "synthetic-other-solver", "Solver differs"),
    ("model_version", "cost-mip-v1+sha256." + "0" * 64,
     "Model source digest differs"),
])
def test_history_comparison_rejects_solver_or_model_context(
        tmp_path, monkeypatch, field, value, message):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    second = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    real_read = dashboard.read_verified_run

    def changed_context(path):
        verified = real_read(path)
        if Path(path).name == second.run_name:
            metadata = dict(verified.metadata)
            metadata[field] = value
            return run_artifacts.VerifiedRun(verified.path, metadata, verified.files)
        return verified

    monkeypatch.setattr(dashboard, "read_verified_run", changed_context)
    with pytest.raises(dashboard.DashboardError, match=message):
        dashboard.compare_verified_runs(
            imported, first.run_name, second.run_name, tmp_path)


def test_history_comparison_rejects_baseline_metric_mismatch(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    second = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    real_prepare = dashboard.prepare_visualization_data

    def changed_baseline(source, summary):
        chart = real_prepare(source, summary)
        if summary["run"].get("optimization_mode") == "peak_cap":
            chart["metrics"]["baseline"]["cost_cny"] += 0.01
        return chart

    monkeypatch.setattr(dashboard, "prepare_visualization_data", changed_baseline)
    with pytest.raises(dashboard.DashboardError, match="Baseline metrics differ"):
        dashboard.compare_verified_runs(
            imported, first.run_name, second.run_name, tmp_path)


@pytest.mark.parametrize("incomplete", [False, True])
def test_history_comparison_rejects_damaged_or_incomplete_run(tmp_path, incomplete):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    if incomplete:
        second_name = "run-" + "0" * 32
        (tmp_path / second_name).mkdir()
    else:
        second = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
        second_name = second.run_name
        target = tmp_path / second_name / "optimized.csv"
        target.write_bytes(target.read_bytes() + b"damage")

    with pytest.raises(dashboard.DashboardError, match="Historical runs unavailable"):
        dashboard.compare_verified_runs(imported, first.run_name, second_name, tmp_path)


def test_history_comparison_rejects_current_csv_mismatch(tmp_path):
    imported = import_energy_csv(SAMPLE, 1)
    first = dashboard.run_comparison(imported, runs_root=tmp_path)
    second = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    different = write_energy_csv(tmp_path / "different.csv", [
        "2026-01-01T00:00:00+00:00,1,0,0.5\n",
    ])

    with pytest.raises(dashboard.DashboardError, match="row count mismatch"):
        dashboard.compare_verified_runs(
            import_energy_csv(different, 1), first.run_name, second.run_name, tmp_path)


def test_cancelled_history_comparison_never_reads_files(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(dashboard, "read_verified_run",
                        lambda _path: pytest.fail("cancelled comparison must not read"))

    with pytest.raises(dashboard.DashboardError, match="cancelled"):
        dashboard.compare_verified_runs(
            imported, "run-" + "0" * 32, "run-" + "1" * 32,
            tmp_path, cancel_event=cancelled)


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
    with pytest.raises(dashboard.DashboardError, match="timed out") as caught:
        dashboard.run_comparison(imported, solve_timeout_seconds=0.001)
    assert caught.value.diagnostic.category is FailureCategory.SOLVER_TIMEOUT
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


@pytest.mark.parametrize(("stage", "reason", "category"), [
    ("audit", "command_failed", FailureCategory.AUDIT_FAILED),
    ("artifact", "invalid", FailureCategory.ARTIFACT_INVALID),
    ("solve", "cancelled", FailureCategory.CANCELLED),
])
def test_dashboard_preserves_structured_pipeline_failure_category(
        tmp_path, monkeypatch, stage, reason, category):
    imported = import_energy_csv(SAMPLE, 1)

    def fail(*_args, **_kwargs):
        raise run_artifacts.RunArtifactError(
            "synthetic structured failure", stage=stage, reason=reason)

    monkeypatch.setattr(dashboard, "publish_run", fail)
    with pytest.raises(dashboard.DashboardError) as caught:
        dashboard.run_comparison(imported, runs_root=tmp_path)

    assert caught.value.diagnostic.category is category
    assert caught.value.__cause__.stage == stage


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


def test_preview_verified_run_uses_safe_reader_and_recalculated_metrics(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    result = dashboard.run_comparison(imported, runs_root=tmp_path, peak_cap_kw=55)
    real_read = dashboard.read_verified_run
    real_prepare = dashboard.prepare_visualization_data
    calls = []

    def read(directory):
        calls.append(("read", Path(directory).name))
        return real_read(directory)

    def prepare(checked_input, summary):
        calls.append(("prepare", len(checked_input.records)))
        return real_prepare(checked_input, summary)

    monkeypatch.setattr(dashboard, "read_verified_run", read)
    monkeypatch.setattr(dashboard, "prepare_visualization_data", prepare)
    monkeypatch.setattr(
        dashboard, "publish_run",
        lambda *_args, **_kwargs: pytest.fail("preview must not publish artifacts"))

    preview = dashboard.preview_verified_run(imported, result.run_name, tmp_path)

    assert calls == [("read", result.run_name), ("prepare", len(imported.records))]
    assert preview.run_name == result.run_name
    assert preview.optimization_mode == "peak_cap"
    assert preview.peak_cap_kw == 55
    assert preview.metrics == dashboard.ComparisonMetrics(
        result.chart["metrics"]["optimized"]["cost_cny"],
        result.chart["metrics"]["optimized"]["grid_energy_kwh"],
        result.chart["metrics"]["optimized"]["peak_grid_kw"],
    )
    assert dict(preview.units) == {
        "cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"}
    assert preview.integrity_status == "VERIFIED"
    with pytest.raises(FrozenInstanceError):
        preview.optimization_mode = "cost"


def test_preview_verified_run_fails_closed_for_mismatch_damage_and_cancellation(
        tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    result = dashboard.run_comparison(imported, runs_root=tmp_path)
    different = tmp_path / "different.csv"
    different.write_text(
        HEADER + "2026-01-01T00:00:00+00:00,1,0,0.5\n", encoding="utf-8")
    other = import_energy_csv(different, 1)

    with pytest.raises(dashboard.DashboardError, match="row count mismatch"):
        dashboard.preview_verified_run(other, result.run_name, tmp_path)

    summary = tmp_path / result.run_name / "summary.json"
    summary.write_bytes(summary.read_bytes() + b"tampered")
    with pytest.raises(dashboard.DashboardError, match="size or SHA-256 mismatch"):
        dashboard.preview_verified_run(imported, result.run_name, tmp_path)

    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        dashboard, "read_verified_run",
        lambda _path: pytest.fail("cancelled preview must not read files"))
    with pytest.raises(dashboard.DashboardError, match="cancelled"):
        dashboard.preview_verified_run(
            imported, "run-" + "0" * 32, tmp_path, cancel_event=cancelled)


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


def test_history_query_filters_identifier_case_and_whitespace_without_reordering():
    entries = [
        dashboard.RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost"),
        dashboard.RunHistoryEntry("run-" + "b" * 32, "VALID", "verified", "peak_cap", 55),
        dashboard.RunHistoryEntry("run-" + "c" * 32, "INCOMPLETE", "missing marker"),
    ]

    assert dashboard.filter_run_history(entries, query="   ") == entries
    assert dashboard.filter_run_history(entries, query="  RUN-" + "A" * 8) == entries[:1]
    assert dashboard.filter_run_history(entries, query="does-not-exist") == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (None, {"VALID", "INCOMPLETE", "DAMAGED"}),
        ("VALID", {"VALID"}),
        ("INCOMPLETE", {"INCOMPLETE"}),
        ("DAMAGED", {"DAMAGED"}),
    ],
)
def test_history_query_filters_all_supported_statuses(status, expected):
    entries = [
        dashboard.RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost"),
        dashboard.RunHistoryEntry("run-" + "b" * 32, "INCOMPLETE", "missing marker"),
        dashboard.RunHistoryEntry("run-" + "c" * 32, "DAMAGED", "hash mismatch"),
    ]

    filtered = dashboard.filter_run_history(entries, status=status)

    assert {entry.status for entry in filtered} == expected


@pytest.mark.parametrize(
    ("mode", "expected_names"),
    [
        (None, {"run-" + "a" * 32, "run-" + "b" * 32, "run-" + "c" * 32}),
        ("cost", {"run-" + "a" * 32}),
        ("peak_cap", {"run-" + "b" * 32}),
    ],
)
def test_history_query_filters_supported_optimization_modes(mode, expected_names):
    entries = [
        dashboard.RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost"),
        dashboard.RunHistoryEntry("run-" + "b" * 32, "VALID", "verified", "peak_cap", 55),
        dashboard.RunHistoryEntry("run-" + "c" * 32, "DAMAGED", "hash mismatch"),
    ]

    filtered = dashboard.filter_run_history(entries, optimization_mode=mode)

    assert {entry.name for entry in filtered} == expected_names


def test_history_query_combines_identifier_status_and_mode():
    matching = dashboard.RunHistoryEntry(
        "run-" + "ab" * 16, "VALID", "verified", "peak_cap", 55)
    entries = [
        dashboard.RunHistoryEntry("run-" + "aa" * 16, "VALID", "verified", "cost"),
        matching,
        dashboard.RunHistoryEntry("run-" + "ab" * 15 + "ac", "DAMAGED", "hash mismatch"),
    ]

    assert dashboard.filter_run_history(
        entries, query="ABAB", status="VALID", optimization_mode="peak_cap") == [matching]


def test_history_sort_supports_name_status_and_mode_with_stable_ties():
    cost_b = dashboard.RunHistoryEntry(
        "run-" + "b" * 32, "VALID", "verified", "cost")
    cost_a = dashboard.RunHistoryEntry(
        "run-" + "a" * 32, "VALID", "verified", "cost")
    peak = dashboard.RunHistoryEntry(
        "run-" + "c" * 32, "VALID", "verified", "peak_cap", 55)
    incomplete = dashboard.RunHistoryEntry(
        "run-" + "d" * 32, "INCOMPLETE", "missing marker")
    damaged = dashboard.RunHistoryEntry(
        "run-" + "e" * 32, "DAMAGED", "hash mismatch")
    entries = [cost_b, peak, incomplete, cost_a, damaged]

    assert dashboard.sort_run_history(entries, sort_key="name") == [
        cost_a, cost_b, peak, incomplete, damaged]
    assert dashboard.sort_run_history(entries, sort_key="name", descending=True) == [
        damaged, incomplete, peak, cost_b, cost_a]
    assert dashboard.sort_run_history(entries, sort_key="status") == [
        cost_b, peak, cost_a, incomplete, damaged]
    assert dashboard.sort_run_history(entries, sort_key="status", descending=True) == [
        damaged, incomplete, cost_b, peak, cost_a]
    assert dashboard.sort_run_history(entries, sort_key="optimization_mode") == [
        cost_b, cost_a, peak, incomplete, damaged]
    assert dashboard.sort_run_history(
        entries, sort_key="optimization_mode", descending=True) == [
            incomplete, damaged, peak, cost_b, cost_a]


def test_history_sort_peak_cap_is_numeric_and_missing_values_always_last():
    missing_cost = dashboard.RunHistoryEntry(
        "run-" + "a" * 32, "VALID", "verified", "cost")
    cap_60_first = dashboard.RunHistoryEntry(
        "run-" + "b" * 32, "VALID", "verified", "peak_cap", 60)
    cap_40 = dashboard.RunHistoryEntry(
        "run-" + "c" * 32, "VALID", "verified", "peak_cap", 40)
    cap_60_second = dashboard.RunHistoryEntry(
        "run-" + "d" * 32, "VALID", "verified", "peak_cap", 60)
    missing_damaged = dashboard.RunHistoryEntry(
        "run-" + "e" * 32, "DAMAGED", "hash mismatch")
    entries = [missing_cost, cap_60_first, cap_40, cap_60_second, missing_damaged]

    assert dashboard.sort_run_history(entries, sort_key="peak_cap_kw") == [
        cap_40, cap_60_first, cap_60_second, missing_cost, missing_damaged]
    assert dashboard.sort_run_history(
        entries, sort_key="peak_cap_kw", descending=True) == [
            cap_60_first, cap_60_second, cap_40, missing_cost, missing_damaged]


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
