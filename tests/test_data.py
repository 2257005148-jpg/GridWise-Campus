"""Synthetic CSV quality and chart-preparation behavior."""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridwise.cli import run
from gridwise.data import DataError, import_energy_csv, prepare_visualization_data


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "examples" / "synthetic-day.csv"
SAMPLE_JSON = ROOT / "examples" / "synthetic-day.json"
HEADER = "timestamp,load_kw,pv_kw,price_cny_per_kwh\n"


def write_csv(path, rows, header=HEADER):
    path.write_bytes((header + "".join(rows)).encode("utf-8"))
    return path


def issue_codes(result):
    return {issue.code for issue in result.report.issues}


def test_sample_csv_matches_synthetic_json():
    result = import_energy_csv(SAMPLE_CSV, 1)
    scenario = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
    assert result.report.ready and result.report.row_count == 6
    assert result.report.missing_counts == {name: 0 for name in
           ("timestamp", "load_kw", "pv_kw", "price_cny_per_kwh")}
    for actual, expected in zip(result.records, scenario["timeseries"], strict=True):
        assert actual.timestamp == expected["timestamp"]
        assert float(actual.load_kw) == expected["load_kw"]
        assert float(actual.pv_kw) == expected["pv_kw"]
        assert float(actual.price_cny_per_kwh) == expected["price_cny_per_kwh"]


@pytest.mark.parametrize("minutes", [15, 30, 60])
def test_valid_step_intervals(tmp_path, minutes):
    first = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    second = first + timedelta(minutes=minutes)
    rows = [f"{first.isoformat()},1,0,0.5\n", f"{second.isoformat()},2,1,0.9\n"]
    result = import_energy_csv(write_csv(tmp_path / "valid.csv", rows), minutes / 60)
    assert result.report.ready and len(result.records) == 2


def test_missing_values_are_counted_and_no_partial_series_is_exposed(tmp_path):
    rows = ["2026-01-01T00:00:00+00:00,,0,0.5\n",
            "2026-01-01T01:00:00+00:00,2,,\n"]
    result = import_energy_csv(write_csv(tmp_path / "missing.csv", rows), 1)
    assert not result.report.ready and result.records == ()
    assert result.report.row_count == 2
    assert result.report.missing_counts["load_kw"] == 1
    assert result.report.missing_counts["pv_kw"] == 1
    assert result.report.missing_counts["price_cny_per_kwh"] == 1
    assert "missing" in issue_codes(result)


@pytest.mark.parametrize("header,code", [
    ("timestamp,load_w,pv_kw,price_cny_per_kwh\n", "header"),
    ("load_kw,timestamp,pv_kw,price_cny_per_kwh\n", "header"),
    ("timestamp,load_kw,pv_kw,pv_kw\n", "duplicate_header"),
    ("timestamp,load_kw,pv_kw,price_cny_per_kwh,extra\n", "header"),
])
def test_invalid_header_or_units_fail(tmp_path, header, code):
    result = import_energy_csv(write_csv(tmp_path / "bad.csv", [], header), 1)
    assert not result.report.ready and result.records == ()
    assert code in issue_codes(result)


@pytest.mark.parametrize("timestamps,code", [
    (["2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"], "duplicate_timestamp"),
    (["2026-01-01T01:00:00+00:00", "2026-01-01T00:00:00+00:00"], "out_of_order"),
    (["2026-01-01T00:00:00+00:00", "2026-01-01T02:00:00+00:00"], "time_gap"),
    (["2026-01-01T00:00:00", "2026-01-01T01:00:00"], "timestamp"),
])
def test_timestamp_anomalies_fail(tmp_path, timestamps, code):
    rows = [f"{stamp},1,0,0.5\n" for stamp in timestamps]
    result = import_energy_csv(write_csv(tmp_path / "time.csv", rows), 1)
    assert not result.report.ready and result.records == ()
    assert code in issue_codes(result)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1" * 129, "1e309"])
def test_invalid_numeric_values_fail(tmp_path, value):
    result = import_energy_csv(write_csv(tmp_path / "number.csv",
                               [f"2026-01-01T00:00:00+00:00,{value},0,0.5\n"]), 1)
    assert not result.report.ready and result.records == ()
    assert "number" in issue_codes(result)


@pytest.mark.parametrize("payload,expected", [
    (b"", "empty_file"),
    (HEADER.encode() + b'"2026-01-01T00:00:00+00:00,1,0,0.5\n', "csv_error"),
    (HEADER.encode() + b"\xff", "encoding"),
    (HEADER.encode(), "empty_series"),
    (HEADER.encode() + b"\n2026-01-01T00:00:00+00:00,1,0,0.5\n", "blank_line"),
])
def test_malformed_or_empty_csv_has_quality_issue(tmp_path, payload, expected):
    path = tmp_path / "bad.csv"
    path.write_bytes(payload)
    result = import_energy_csv(path, 1)
    assert not result.report.ready and result.records == ()
    assert expected in issue_codes(result)


def test_invalid_step_and_missing_file_are_reported(tmp_path):
    path = tmp_path / "missing.csv"
    assert "invalid_step" in issue_codes(import_energy_csv(path, 0))
    assert "read_error" in issue_codes(import_energy_csv(path, 1))


def test_cli_reports_quality_and_exit_status(tmp_path):
    good = subprocess.run([sys.executable, "-m", "gridwise.data", str(SAMPLE_CSV),
                           "--step-hours", "1"], capture_output=True, text=True, check=False)
    assert good.returncode == 0
    assert json.loads(good.stdout)["ready"] is True
    bad_path = write_csv(tmp_path / "bad.csv", ["2026-01-01T00:00:00+00:00,,0,0.5\n"])
    bad = subprocess.run([sys.executable, "-m", "gridwise.data", str(bad_path),
                          "--step-hours", "1"], capture_output=True, text=True, check=False)
    assert bad.returncode == 2
    report = json.loads(bad.stdout)
    assert report["missing_counts"]["load_kw"] == 1
    assert report["issues"][0]["line"] == 2


@pytest.fixture
def chart_case(tmp_path):
    imported = import_energy_csv(SAMPLE_CSV, 1)
    summary = run(SAMPLE_JSON, tmp_path / "exports")
    return imported, summary


def test_visualization_data_keeps_cost_energy_and_peak_distinct(chart_case):
    imported, summary = chart_case
    chart = prepare_visualization_data(imported, summary)
    assert len(chart["timestamps"]) == 6
    assert chart["series"]["load_kw"] == [60, 65, 75, 90, 70, 55]
    assert chart["series"]["pv_kw"] == [0, 20, 60, 70, 25, 0]
    assert chart["series"]["optimized_soc_kwh"][-1] == 40
    assert chart["metrics"]["optimized"]["cost_cny"] == pytest.approx(131.0110803324)
    assert chart["metrics"]["baseline"]["cost_cny"] == pytest.approx(162.5)
    assert chart["delta_optimized_minus_baseline"]["cost_cny"] < 0
    assert chart["delta_optimized_minus_baseline"]["grid_energy_kwh"] > 0
    assert chart["delta_optimized_minus_baseline"]["peak_grid_kw"] == 30
    assert chart["series"]["optimized_cumulative_cost_cny"][-1] == pytest.approx(131.0110803324)


@pytest.mark.parametrize("change,diagnostic", [
    (lambda s: s["optimized"]["rows"][0].__setitem__("load_kw", 61), "load_kw"),
    (lambda s: s["baseline"]["rows"][0].__setitem__("timestamp", "bad"), "timestamp"),
    (lambda s: s["optimized"].__setitem__("total_cost_cny", 1), "total_cost_cny"),
])
def test_visualization_data_rejects_misaligned_or_changed_results(chart_case, change, diagnostic):
    imported, summary = chart_case
    change(summary)
    with pytest.raises(DataError, match=diagnostic):
        prepare_visualization_data(imported, summary)


def test_visualization_data_rejects_bad_import(chart_case):
    imported, summary = chart_case
    bad = import_energy_csv(SAMPLE_CSV, 0)
    with pytest.raises(DataError, match="not ready"):
        prepare_visualization_data(bad, summary)
