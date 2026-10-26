"""Public peak-cap solve, export and independent-audit behavior."""

import csv
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from gridwise.cli import run
from gridwise.core import InputError, SolverError, parse_scenario, solve, validate_result
from gridwise.run_artifacts import RunArtifactError, publish_run, read_verified_run


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "synthetic-day.json"
AUDITOR = ROOT / "tools" / "audit_exports.py"


def audit(directory, scenario=None):
    return subprocess.run(
        [sys.executable, str(AUDITOR), str(scenario or directory / "scenario.json"),
         str(directory / "optimized.csv"), str(directory / "baseline.csv"),
         str(directory / "summary.json")], capture_output=True, text=True)


def test_peak_cap_cli_exports_traceable_auditable_comparison(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_bytes(SOURCE.read_bytes())
    output = tmp_path / "output"
    completed = subprocess.run(
        [sys.executable, "-m", "gridwise.cli", str(scenario), str(output),
         "--peak-cap-kw", "55"], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["run"]["optimization_mode"] == "peak_cap"
    assert summary["run"]["peak_cap_kw"] == 55
    assert summary["run"]["model_version"].startswith("peak-cap-mip-v1+sha256.")
    assert summary["run"]["status"] == "optimal"
    assert summary["baseline"]["total_cost_cny"] == pytest.approx(162.5)
    assert summary["baseline"]["peak_grid_kw"] == pytest.approx(60)
    with (output / "optimized.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    step = Decimal("1")
    prices = [Decimal(row["price_cny_per_kwh"]) for row in rows]
    grids = [Decimal(row["grid_kw"]) for row in rows]
    assert max(grids) <= Decimal("55.00001")
    assert abs(sum(g * p * step for g, p in zip(grids, prices)) -
               Decimal(str(summary["optimized"]["total_cost_cny"]))) <= Decimal("0.00001")
    assert abs(sum(g * step for g in grids) -
               Decimal(str(summary["optimized"]["grid_energy_kwh"]))) <= Decimal("0.00001")
    checked = audit(output, scenario)
    assert checked.returncode == 0, checked.stderr


def test_peak_cap_infeasible_fails_without_exports(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_bytes(SOURCE.read_bytes())
    output = tmp_path / "output"
    completed = subprocess.run(
        [sys.executable, "-m", "gridwise.cli", str(scenario), str(output),
         "--peak-cap-kw", "0"], capture_output=True, text=True)
    assert completed.returncode == 2
    assert "no optimal solution" in completed.stderr
    assert not output.exists()


def test_peak_cap_audit_rejects_falsified_cap(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_bytes(SOURCE.read_bytes())
    run(scenario, tmp_path, peak_cap_kw=55)
    summary_path = tmp_path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["run"]["peak_cap_kw"] = 1
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    checked = audit(tmp_path)
    assert checked.returncode == 2
    assert "peak cap" in checked.stderr
    assert "AUDIT PASSED" not in checked.stdout


@pytest.mark.parametrize("change,location", [
    (lambda run: run.update(optimization_mode="cost"), "summary.run.optimization_mode"),
    (lambda run: run.pop("peak_cap_kw"), "summary.run: missing field peak_cap_kw"),
    (lambda run: run.update(model_version="cost-mip-v1"), "summary.run.model_version"),
    (lambda run: run.update(status="infeasible"), "summary.run.status"),
])
def test_peak_cap_audit_rejects_falsified_policy_or_status(tmp_path, change, location):
    scenario = tmp_path / "scenario.json"
    scenario.write_bytes(SOURCE.read_bytes())
    run(scenario, tmp_path, peak_cap_kw=55)
    summary_path = tmp_path / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    change(summary["run"])
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    checked = audit(tmp_path)
    assert checked.returncode == 2
    assert location in checked.stderr
    assert "AUDIT PASSED" not in checked.stdout


@pytest.mark.parametrize("cap", [-1, float("nan"), 121, True])
def test_peak_cap_rejects_invalid_parameters(cap):
    scenario = parse_scenario(json.loads(SOURCE.read_text(encoding="utf-8")))
    with pytest.raises(InputError, match="peak_cap_kw"):
        solve(scenario, peak_cap_kw=cap)


def test_independent_result_validation_rejects_claimed_cap_below_grid():
    scenario = parse_scenario(json.loads(SOURCE.read_text(encoding="utf-8")))
    answer = solve(scenario, peak_cap_kw=55)
    with pytest.raises(ValueError, match="peak cap failed"):
        validate_result(scenario, answer, peak_cap_kw=1)


def test_peak_cap_nonoptimal_status_is_rejected(monkeypatch):
    import pyomo.environ as pyo
    from types import SimpleNamespace

    class NonoptimalSolver:
        def available(self, exception_flag=False):
            return True

        def solve(self, model, load_solutions=False):
            return SimpleNamespace(solver=SimpleNamespace(
                status=pyo.SolverStatus.ok,
                termination_condition=pyo.TerminationCondition.infeasible))

    monkeypatch.setattr(pyo, "SolverFactory", lambda name: NonoptimalSolver())
    scenario = parse_scenario(json.loads(SOURCE.read_text(encoding="utf-8")))
    with pytest.raises(SolverError, match="no optimal solution"):
        solve(scenario, peak_cap_kw=55)


def test_peak_cap_immutable_run_records_and_verifies_policy(tmp_path):
    published = publish_run(SOURCE, tmp_path, peak_cap_kw=55)
    verified = read_verified_run(published)
    summary = json.loads(verified.files["summary.json"])
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert summary["run"]["optimization_mode"] == "peak_cap"
    assert summary["run"]["peak_cap_kw"] == 55
    assert manifest["format_version"] == 2
    assert manifest["metadata"]["optimization_mode"] == "peak_cap"
    assert manifest["metadata"]["peak_cap_kw"] == 55
    assert summary["optimized"]["peak_grid_kw"] <= 55 + 1e-5
    assert summary["baseline"]["peak_grid_kw"] == pytest.approx(60)
    assert audit(published).returncode == 0


def test_peak_cap_verified_reader_rejects_changed_summary(tmp_path):
    published = publish_run(SOURCE, tmp_path, peak_cap_kw=55)
    summary_path = published / "summary.json"
    payload = summary_path.read_bytes()
    summary_path.write_bytes(payload + b" ")
    with pytest.raises(RunArtifactError, match="size or SHA-256 mismatch"):
        read_verified_run(published)


def test_default_immutable_run_retains_v1_manifest_and_audit(tmp_path):
    published = publish_run(SOURCE, tmp_path)
    verified = read_verified_run(published)
    summary = json.loads(verified.files["summary.json"])
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format_version"] == 1
    assert "optimization_mode" not in summary["run"]
    assert "peak_cap_kw" not in summary["run"]
    assert summary["optimized"]["total_cost_cny"] == pytest.approx(131.01108033241)
    assert summary["optimized"]["peak_grid_kw"] == pytest.approx(90)
    assert audit(published).returncode == 0
