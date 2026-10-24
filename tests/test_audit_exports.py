"""Black-box tests for the standard-library export auditor."""

import csv
import hashlib
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


def rejected(result, location):
    assert result.returncode == 2
    assert location in result.stderr
    assert "AUDIT PASSED" not in result.stdout


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


def test_summary_records_exact_source_fingerprint_and_run_metadata(exports):
    source = (exports / "scenario.json").read_bytes()
    summary = json.loads((exports / "summary.json").read_text(encoding="utf-8"))
    assert summary["run"] == {
        "model_version": "cost-mip-v1",
        "scenario_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
        "solver_name": "appsi_highs",
        "status": "optimal",
    }
    assert check(exports).returncode == 0


def test_same_source_bytes_at_new_path_keep_fingerprint_valid(exports):
    copy = exports / "same scenario.json"
    copy.write_bytes((exports / "scenario.json").read_bytes())
    result = subprocess.run(
        [sys.executable, str(AUDITOR), str(copy), str(exports / "optimized.csv"),
         str(exports / "baseline.csv"), str(exports / "summary.json")],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", [
    lambda data: data.replace(b'"step_hours": 1', b'"step_hours": 1.0', 1),
    lambda data: data.replace(b'"load_kw": 60', b'"load_kw": 61', 1),
    lambda data: data.replace(b'"price_cny_per_kwh": 0.5', b'"price_cny_per_kwh": 0.6', 1),
    lambda data: data.replace(b'+08:00', b'+09:00'),
])
def test_old_exports_rejected_after_source_bytes_change(exports, change):
    path = exports / "scenario.json"
    before = path.read_bytes()
    after = change(before)
    assert after != before
    path.write_bytes(after)
    result = check(exports)
    rejected(result, "summary.run.scenario_sha256")


@pytest.mark.parametrize("field,value", [
    ("model_version", "cost-mip-v2"),
    ("scenario_sha256", "sha256:" + "0" * 64),
    ("solver_name", "other_solver"),
    ("status", "infeasible"),
])
def test_audit_rejects_changed_run_metadata(exports, field, value):
    path = exports / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["run"][field] = value
    path.write_text(json.dumps(summary), encoding="utf-8")
    rejected(check(exports), f"summary.run.{field}")


def test_audit_rejects_missing_run_metadata(exports):
    path = exports / "summary.json"
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary.pop("run")
    path.write_text(json.dumps(summary), encoding="utf-8")
    rejected(check(exports), "summary: missing field run")


@pytest.mark.parametrize("bom,crlf", [(False, False), (False, True), (True, False), (True, True)])
def test_export_and_audit_accept_utf8_bom_and_newlines(tmp_path, bom, crlf):
    source = SOURCE.read_bytes()
    if crlf:
        source = source.replace(b"\n", b"\r\n")
    if bom:
        source = b"\xef\xbb\xbf" + source
    path = tmp_path / "scenario.json"
    path.write_bytes(source)
    run(path, tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["run"]["scenario_sha256"] == "sha256:" + hashlib.sha256(source).hexdigest()
    assert check(tmp_path).returncode == 0


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
    if diagnostic == "input echo":
        summary_path = exports / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["run"]["scenario_sha256"] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
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


@pytest.mark.parametrize("name,contents,diagnostic", [
    ("scenario.json", b"", "cannot read JSON"),
    ("summary.json", b'{"optimized":', "cannot read JSON"),
    ("optimized.csv", b"", "missing CSV header"),
    ("baseline.csv", b'timestamp,"unterminated\n', "cannot read CSV"),
    ("scenario.json", b"\xff", "cannot read JSON"),
    ("baseline.csv", b"\xff", "cannot read CSV"),
])
def test_audit_fails_closed_on_empty_truncated_malformed_or_bad_encoding(exports, name, contents, diagnostic):
    path = exports / name
    path.write_bytes(contents)
    rejected(check(exports), diagnostic)


@pytest.mark.parametrize("change,diagnostic", [
    (lambda h: h.__setitem__(1, h[0]), "duplicate CSV header"),
    (lambda h: h.append("unexpected"), "unexpected CSV field"),
    (lambda h: h.reverse(), "unexpected CSV column order"),
    (lambda h: h.pop(), "missing CSV field"),
])
def test_audit_rejects_bad_csv_header(exports, change, diagnostic):
    path = exports / "optimized.csv"
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split(",")
    change(header)
    lines[0] = ",".join(header)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rejected(check(exports), diagnostic)


def test_audit_rejects_extra_csv_value(exports):
    path = exports / "baseline.csv"
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    lines[1] += ",extra"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rejected(check(exports), "extra CSV column")


@pytest.mark.parametrize("target,diagnostic", [
    ("scenario", "scenario: unexpected field"),
    ("battery", "scenario.battery: unexpected field"),
    ("period", "scenario.timeseries[0]: unexpected field"),
    ("summary", "summary: unexpected field"),
    ("result", "summary.optimized: unexpected field"),
    ("result_row", "summary.optimized row 0"),
])
def test_audit_rejects_extra_json_fields(exports, target, diagnostic):
    path = exports / ("summary.json" if target in {"summary", "result", "result_row"} else "scenario.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    location = {"scenario": data, "battery": data.get("battery"),
                "period": data.get("timeseries", [{}])[0], "summary": data,
                "result": data.get("optimized"),
                "result_row": data.get("optimized", {}).get("rows", [{}])[0]}[target]
    location["unexpected"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    rejected(check(exports), diagnostic)


def test_audit_rejects_duplicate_json_key(exports):
    path = exports / "scenario.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"power_unit": "kW",', '"power_unit": "kW", "power_unit": "kW",', 1), encoding="utf-8")
    rejected(check(exports), "duplicate JSON key power_unit")


@pytest.mark.parametrize("name,value,diagnostic", [
    ("grid_kw", "Infinity", "non-finite"),
    ("grid_kw", "9" * 129, "numeric token exceeds 128"),
    ("grid_kw", "1e309", "outside finite Python float range"),
    ("grid_kw", "1e13", "absolute value exceeds 1000000000000"),
])
def test_audit_rejects_nonfinite_or_oversized_numeric_csv(exports, name, value, diagnostic):
    path = exports / "optimized.csv"
    rows = read_csv(path)
    rows[0][name] = value
    write_csv(path, rows)
    rejected(check(exports), diagnostic)


def test_audit_rejects_oversized_numeric_json(exports):
    path = exports / "scenario.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["grid_max_kw"] = int("9" * 129)
    path.write_text(json.dumps(data), encoding="utf-8")
    rejected(check(exports), "numeric token exceeds 128")


def test_audit_rejects_file_larger_than_32_mib(exports):
    path = exports / "scenario.json"
    with path.open("wb") as stream:
        stream.seek(32 * 1024 * 1024)
        stream.write(b"x")
    rejected(check(exports), "exceeds 33554432 bytes")


def test_audit_rejects_overlong_csv_line(exports):
    path = exports / "baseline.csv"
    path.write_bytes(b"a" * (64 * 1024 + 1) + b"\n")
    rejected(check(exports), "line 1: exceeds 65536 bytes")


def test_audit_rejects_blank_csv_line(exports):
    path = exports / "baseline.csv"
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join((lines[0], b"", *lines[1:])) + b"\n")
    rejected(check(exports), "line 2: blank CSV line")


def test_audit_rejects_more_than_50000_periods(exports):
    path = exports / "scenario.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["timeseries"] = [data["timeseries"][0]] * 50_001
    path.write_text(json.dumps(data), encoding="utf-8")
    rejected(check(exports), "row count exceeds 50000")


def test_audit_rejects_step_longer_than_24_hours(exports):
    path = exports / "scenario.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["step_hours"] = 25
    path.write_text(json.dumps(data), encoding="utf-8")
    rejected(check(exports), "step_hours: exceeds 24 hours")


def test_audit_reports_missing_input_file(exports):
    result = subprocess.run(
        [sys.executable, str(AUDITOR), str(exports / "absent.json"),
         str(exports / "optimized.csv"), str(exports / "baseline.csv"),
         str(exports / "summary.json")], capture_output=True, text=True, check=False)
    rejected(result, "absent.json: cannot read JSON")


def test_audit_reports_unreadable_output_path(exports):
    result = subprocess.run(
        [sys.executable, str(AUDITOR), str(exports / "scenario.json"),
         str(exports), str(exports / "baseline.csv"), str(exports / "summary.json")],
        capture_output=True, text=True, check=False)
    rejected(result, "cannot read CSV")


def test_audit_reports_simulated_permission_error(exports):
    wrapper = (
        "import pathlib,runpy,sys; target=pathlib.Path(sys.argv[1]); original=pathlib.Path.open; "
        "def_open=lambda self,*a,**k: (_ for _ in ()).throw(PermissionError(13,'test denied',str(self))) "
        "if self==target else original(self,*a,**k); pathlib.Path.open=def_open; "
        "sys.argv=[sys.argv[2],*sys.argv[3:]]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", wrapper, str(exports / "baseline.csv"), str(AUDITOR),
         str(exports / "scenario.json"), str(exports / "optimized.csv"),
         str(exports / "baseline.csv"), str(exports / "summary.json")],
        capture_output=True, text=True, check=False)
    rejected(result, "baseline.csv: cannot read CSV")


def test_audit_accepts_lf_and_unicode_path(exports):
    nested = exports / "校园 audit"
    nested.mkdir()
    for name in ("scenario.json", "optimized.csv", "baseline.csv", "summary.json"):
        data = (exports / name).read_bytes().replace(b"\r\n", b"\n")
        (nested / name).write_bytes(data)
    result = check(nested)
    assert result.returncode == 0, result.stderr
    assert "AUDIT PASSED" in result.stdout
