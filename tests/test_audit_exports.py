"""Black-box tests for the standard-library export auditor."""

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from gridwise.cli import run


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "synthetic-day.json"
AUDITOR = ROOT / "tools" / "audit_exports.py"


@pytest.fixture
def exports(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_bytes(SOURCE.read_bytes())
    run(scenario, tmp_path)
    return tmp_path


def check(directory):
    return subprocess.run(
        [sys.executable, str(AUDITOR), str(directory / "scenario.json"),
         str(directory / "optimized.csv"), str(directory / "baseline.csv"),
         str(directory / "summary.json")], capture_output=True, text=True, check=False)


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys(), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_audit_accepts_same_input_comparison(exports):
    result = check(exports)
    assert result.returncode == 0, result.stderr
    assert "AUDIT PASSED" in result.stdout
    assert "total_cost_cny=131.01" in result.stdout
    assert "total_cost_cny=162.50" in result.stdout


@pytest.mark.parametrize("field,value,diagnostic", [
    ("grid_kw", "999", "summary.optimized row 0"),
    ("soc_kwh", "999", "summary.optimized row 0"),
    ("pv_curtailed_kw", "5", "summary.optimized row 0"),
    ("load_kw", "999", "summary.optimized row 0"),
    ("price_cny_per_kwh", "999", "summary.optimized row 0"),
    ("grid_kw", "nan", "non-finite"),
    ("timestamp", "2026-01-01T09:00:00+08:00", "timestamp"),
])
def test_audit_rejects_single_csv_cell_corruption(exports, field, value, diagnostic):
    path = exports / "optimized.csv"
    rows = read_csv(path)
    rows[0][field] = value
    write_csv(path, rows)
    result = check(exports)
    assert result.returncode == 2
    assert "optimized row 0" in result.stderr
    assert diagnostic in result.stderr


@pytest.mark.parametrize("field,value,diagnostic", [
    ("grid_kw", 999, "power_balance_kw"),
    ("soc_kwh", 999, "soc_flow_kwh"),
    ("pv_curtailed_kw", 5, "pv_split_kw"),
    ("load_kw", 999, "input echo"),
    ("price_cny_per_kwh", 999, "input echo"),
])
def test_audit_rejects_matching_csv_and_json_corruption(exports, field, value, diagnostic):
    csv_path = exports / "optimized.csv"
    rows = read_csv(csv_path)
    rows[0][field] = str(value)
    write_csv(csv_path, rows)
    json_path = exports / "summary.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["optimized"]["rows"][0][field] = value
    json_path.write_text(json.dumps(data), encoding="utf-8")
    result = check(exports)
    assert result.returncode == 2
    assert diagnostic in result.stderr


@pytest.mark.parametrize("kind,field", [
    ("optimized", "total_cost_cny"),
    ("optimized", "grid_energy_kwh"),
    ("baseline", "peak_grid_kw"),
    ("baseline", "pv_curtailed_kwh"),
])
def test_audit_rejects_single_summary_metric_corruption(exports, kind, field):
    path = exports / "summary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[kind][field] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    result = check(exports)
    assert result.returncode == 2
    assert f"summary.{kind}.{field}" in result.stderr


def test_audit_rejects_single_summary_row_corruption(exports):
    path = exports / "summary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["baseline"]["rows"][0]["grid_kw"] += 1
    path.write_text(json.dumps(data), encoding="utf-8")
    result = check(exports)
    assert result.returncode == 2
    assert "summary.baseline row 0" in result.stderr


@pytest.mark.parametrize("mutate,diagnostic", [
    (lambda rows: rows.pop(), "row count mismatch"),
    (lambda rows: rows[0].pop("grid_kw"), "missing CSV field grid_kw"),
])
def test_audit_rejects_missing_csv_data(exports, mutate, diagnostic):
    path = exports / "baseline.csv"
    rows = read_csv(path)
    mutate(rows)
    write_csv(path, rows)
    result = check(exports)
    assert result.returncode == 2
    assert diagnostic in result.stderr


@pytest.mark.parametrize("mutate,diagnostic", [
    (lambda d: d["timeseries"][0].pop("load_kw"), "missing field load_kw"),
    (lambda d: d["timeseries"][1].update(timestamp=d["timeseries"][0]["timestamp"]), "duplicate timestamp"),
    (lambda d: d["timeseries"][1].update(timestamp="2026-01-01T07:00:00+08:00"), "out-of-order timestamp"),
    (lambda d: d["timeseries"][0].update(load_kw=float("nan")), "non-finite"),
    (lambda d: d.update(power_unit="MW"), "power_unit must be kW"),
    (lambda d: d["timeseries"][0].update(load_kw=61), "input echo"),
])
def test_audit_rejects_bad_or_tampered_input(exports, mutate, diagnostic):
    path = exports / "scenario.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")
    result = check(exports)
    assert result.returncode == 2
    assert diagnostic in result.stderr


@pytest.mark.parametrize("step", [0.25, 0.5, 1.0])
def test_audit_accepts_quarter_half_and_one_hour_exports(tmp_path, step):
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    data["step_hours"] = step
    data["timeseries"] = data["timeseries"][:2]
    first = datetime.fromisoformat(data["timeseries"][0]["timestamp"])
    data["timeseries"][1]["timestamp"] = (first + timedelta(hours=step)).isoformat()
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    run(path, tmp_path)
    result = check(tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutate,diagnostic", [
    (lambda d: d["optimized"].pop("total_cost_cny"), "missing field total_cost_cny"),
    (lambda d: d["baseline"].update(peak_grid_kw=float("inf")), "non-finite"),
    (lambda d: d["optimized"]["rows"].pop(), "row count mismatch"),
])
def test_audit_rejects_missing_nonfinite_or_short_summary(exports, mutate, diagnostic):
    path = exports / "summary.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, allow_nan=True), encoding="utf-8")
    result = check(exports)
    assert result.returncode == 2
    assert diagnostic in result.stderr
