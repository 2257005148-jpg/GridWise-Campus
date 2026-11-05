"""Verified HTML report generation and safe single-file publication."""

from datetime import datetime, timezone
from pathlib import Path
import re
import threading
import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.integration

from gridwise import dashboard, reports
from gridwise.data import import_energy_csv
from gridwise.dashboard import (ComparedRun, ComparisonMetrics,
                                HistoricalRunComparison)
from gridwise.device import DeviceParameters


SAMPLE = dashboard.ROOT / "examples" / "synthetic-day.csv"
GENERATED_AT = datetime(2026, 10, 27, 12, 30, tzinfo=timezone.utc)


def test_single_and_ab_html_reuse_verified_metrics_and_escape_input_source(tmp_path):
    source = tmp_path / "校园 & energy.csv"
    source.write_bytes(SAMPLE.read_bytes())
    imported = import_energy_csv(source, 1)
    runs_root = tmp_path / "runs"
    cost = dashboard.run_comparison(imported, runs_root=runs_root)
    capped = dashboard.run_comparison(imported, runs_root=runs_root, peak_cap_kw=55)

    single = reports.build_single_run_html(
        imported, cost.run_name, runs_root, generated_at=GENERATED_AT).decode("utf-8")
    comparison = reports.build_comparison_html(
        imported, cost.run_name, capped.run_name, runs_root,
        generated_at=GENERATED_AT).decode("utf-8")

    assert "<!doctype html>" in single.lower()
    assert "校园 &amp; energy.csv" in single
    assert "校园 & energy.csv" not in single
    assert "2026-10-27T12:30:00+00:00" in single
    assert "131.01" in single and "162.50" in single
    assert "247.02" in single and "240.00" in single
    assert "90.00" in single and "60.00" in single
    assert "80.00 kWh" in single
    assert "VERIFIED" in single
    assert "grid_kw × price_cny_per_kwh × step_hours" in single
    assert "不能证明输入真实性" in single
    assert single.count('class="timeseries"') == 3
    single_svgs = re.findall(r"<svg\b.*?</svg>", single, flags=re.DOTALL)
    assert len(single_svgs) == 3
    assert all(ET.fromstring(svg).tag == "svg" for svg in single_svgs)
    assert "购电功率时序 / Grid power timeline" in single
    assert "累计电费时序 / Cumulative cost timeline" in single
    assert "电池 SOC 时序 / Battery SOC timeline" in single
    assert "优化 / Optimized" in single and "基线 / Baseline" in single
    optimized_grid = cost.chart["series"]["optimized_grid_kw"]
    assert (f"{len(optimized_grid)} points; min {min(optimized_grid):.2f} kW; "
            f"max {max(optimized_grid):.2f} kW") in single
    assert "<script" not in single.lower()
    assert "http://" not in single.lower() and "https://" not in single.lower()

    assert "A/B 公平对比报告" in comparison
    assert "MATCHED" in comparison
    assert "55.00 kW" in comparison
    assert "+25.89 CNY" in comparison
    assert "-3.12 kWh" in comparison
    assert "-35.00 kW" in comparison
    assert comparison.count("80.00 kWh") >= 2
    assert comparison.count('class="timeseries"') == 3
    comparison_svgs = re.findall(r"<svg\b.*?</svg>", comparison, flags=re.DOTALL)
    assert len(comparison_svgs) == 3
    assert all(ET.fromstring(svg).tag == "svg" for svg in comparison_svgs)
    assert "运行 A / Run A" in comparison and "运行 B / Run B" in comparison
    assert "购电功率 A/B / Grid power A/B" in comparison
    assert "累计电费 A/B / Cumulative cost A/B" in comparison
    assert "电池 SOC A/B / Battery SOC A/B" in comparison
    capped_grid = capped.chart["series"]["optimized_grid_kw"]
    assert (f"{len(capped_grid)} points; min {min(capped_grid):.2f} kW; "
            f"max {max(capped_grid):.2f} kW") in comparison


@pytest.mark.parametrize("invalid", ["empty", "nan", "infinity"])
def test_report_rejects_empty_or_nonfinite_timeseries_before_publication(
        tmp_path, monkeypatch, invalid):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "1" * 32
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    timestamps = [record.timestamp for record in imported.records]
    count = len(timestamps)
    series = {
        "optimized_grid_kw": [10.0] * count,
        "baseline_grid_kw": [12.0] * count,
        "optimized_cumulative_cost_cny": [float(index + 1) for index in range(count)],
        "baseline_cumulative_cost_cny": [float(index + 2) for index in range(count)],
        "optimized_soc_kwh": [30.0] * count,
    }
    if invalid == "empty":
        timestamps = []
        series = {key: [] for key in series}
    elif invalid == "nan":
        series["optimized_grid_kw"][1] = float("nan")
    else:
        series["optimized_soc_kwh"][1] = float("inf")
    chart = {
        "timestamps": timestamps,
        "series": series,
        "metrics": {
            "optimized": {"cost_cny": 10, "grid_energy_kwh": 20, "peak_grid_kw": 10},
            "baseline": {"cost_cny": 12, "grid_energy_kwh": 24, "peak_grid_kw": 12},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": -2, "grid_energy_kwh": -4, "peak_grid_kw": -2},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))
    target = tmp_path / f"{invalid}.html"

    with pytest.raises(reports.ReportError, match="time series"):
        reports.export_single_run_report(
            target, imported, run_name, tmp_path, generated_at=GENERATED_AT)
    assert not target.exists()


def test_report_svg_compacts_extreme_axis_labels_and_preserves_narrow_readability(
        tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "2" * 32
    devices = DeviceParameters(1e12, 80, 40, 40, 30, 30, 0.95, 0.95)
    timestamps = [record.timestamp for record in imported.records]
    count = len(timestamps)
    chart = {
        "timestamps": timestamps,
        "series": {
            "optimized_grid_kw": [1e12] * count,
            "baseline_grid_kw": [5e11] * count,
            "optimized_cumulative_cost_cny": [5e-8] * count,
            "baseline_cumulative_cost_cny": [1e-7] * count,
            "optimized_soc_kwh": [30.0] * count,
        },
        "metrics": {
            "optimized": {"cost_cny": 1, "grid_energy_kwh": 1, "peak_grid_kw": 1e12},
            "baseline": {"cost_cny": 1, "grid_energy_kwh": 1, "peak_grid_kw": 5e11},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": 0, "grid_energy_kwh": 0, "peak_grid_kw": 5e11},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))

    html = reports.build_single_run_html(
        imported, run_name, tmp_path, generated_at=GENERATED_AT).decode("utf-8")

    assert '>1.00e+12</text>' in html
    assert '>1.00e-7</text>' in html
    assert '<div class="chart-viewport"><svg class="timeseries"' in html
    assert (".chart-viewport {width:100%;max-width:100%;min-width:0;"
            "overflow-x:auto}") in html
    assert ".timeseries {display:block;width:100%;min-width:680px" in html


def test_report_svg_separates_legend_unit_and_highest_axis_tick(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "3" * 32
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    timestamps = [record.timestamp for record in imported.records]
    count = len(timestamps)
    chart = {
        "timestamps": timestamps,
        "series": {
            "optimized_grid_kw": [90.0] * count,
            "baseline_grid_kw": [60.0] * count,
            "optimized_cumulative_cost_cny": [131.01] * count,
            "baseline_cumulative_cost_cny": [162.5] * count,
            "optimized_soc_kwh": [30.0] * count,
        },
        "metrics": {
            "optimized": {"cost_cny": 131.01, "grid_energy_kwh": 247.02,
                          "peak_grid_kw": 90},
            "baseline": {"cost_cny": 162.5, "grid_energy_kwh": 240,
                         "peak_grid_kw": 60},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": -31.49, "grid_energy_kwh": 7.02, "peak_grid_kw": 30},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))

    html = reports.build_single_run_html(
        imported, run_name, tmp_path, generated_at=GENERATED_AT).decode("utf-8")
    svg = ET.fromstring(re.findall(r"<svg\b.*?</svg>", html, flags=re.DOTALL)[0])
    legend_y = float(next(node for node in svg.findall("text")
                          if node.attrib.get("class") == "legend-label").attrib["y"])
    unit_y = float(next(node for node in svg.findall("text")
                        if node.attrib.get("class") == "axis-title").attrib["y"])
    highest_tick_y = min(float(node.attrib["y"]) for node in svg.findall("text")
                         if node.attrib.get("class") == "axis-label")

    assert unit_y - legend_y >= 20
    assert highest_tick_y - unit_y >= 18


def test_report_chart_scroll_is_contained_without_page_width_expansion(
        tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "4" * 32
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    timestamps = [record.timestamp for record in imported.records]
    count = len(timestamps)
    chart = {
        "timestamps": timestamps,
        "series": {
            "optimized_grid_kw": [10.0] * count,
            "baseline_grid_kw": [12.0] * count,
            "optimized_cumulative_cost_cny": [20.0] * count,
            "baseline_cumulative_cost_cny": [22.0] * count,
            "optimized_soc_kwh": [30.0] * count,
        },
        "metrics": {
            "optimized": {"cost_cny": 20, "grid_energy_kwh": 60,
                          "peak_grid_kw": 10},
            "baseline": {"cost_cny": 22, "grid_energy_kwh": 72,
                         "peak_grid_kw": 12},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": -2, "grid_energy_kwh": -12, "peak_grid_kw": -2},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))

    html = reports.build_single_run_html(
        imported, run_name, tmp_path, generated_at=GENERATED_AT).decode("utf-8")

    assert ".charts {display:grid;min-width:0;gap:16px}" in html
    assert ".chart {min-width:0;margin:0;" in html
    assert (".chart-viewport {width:100%;max-width:100%;min-width:0;"
            "overflow-x:auto}") in html
    assert ".timeseries {display:block;width:100%;min-width:680px" in html


def test_html_report_publication_is_atomic_and_refuses_existing_or_directory(tmp_path):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"

    assert reports.publish_html_report(target, payload) == target
    assert target.read_bytes() == payload
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []

    original = target.read_bytes()
    with pytest.raises(reports.ReportError, match="already exists"):
        reports.publish_html_report(
            target, b"<!doctype html><html><body>replacement</body></html>")
    assert target.read_bytes() == original

    directory = tmp_path / "directory.html"
    directory.mkdir()
    with pytest.raises(reports.ReportError, match="regular file"):
        reports.publish_html_report(directory, payload, overwrite=True)


def test_report_overwrite_still_uses_atomic_replace(tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    target.write_bytes(b"original")
    payload = b"<!doctype html><html><body>replacement</body></html>"

    def unexpected_link(*_args, **_kwargs):
        raise AssertionError("overwrite=True must not use hard-link publication")

    monkeypatch.setattr(reports.os, "link", unexpected_link)

    assert reports.publish_html_report(target, payload, overwrite=True) == target
    assert target.read_bytes() == payload
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []


def test_no_clobber_publish_rejects_target_created_at_final_operation(
        tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"
    competitor = b"competitor"
    real_link = reports.os.link
    real_replace = reports.os.replace

    def racing_link(source, destination, *args, **kwargs):
        target.write_bytes(competitor)
        return real_link(source, destination, *args, **kwargs)

    def racing_replace(source, destination, *args, **kwargs):
        target.write_bytes(competitor)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(reports.os, "link", racing_link)
    monkeypatch.setattr(reports.os, "replace", racing_replace)

    with pytest.raises(reports.ReportError, match="already exists"):
        reports.publish_html_report(target, payload)

    assert target.read_bytes() == competitor
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []


def test_no_clobber_fails_closed_when_hard_links_are_unavailable(
        tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"

    def deny_link(*_args, **_kwargs):
        raise OSError("synthetic hard-link unsupported")

    def forbidden_replace(*_args, **_kwargs):
        raise AssertionError("no-clobber publication must not fall back to replace")

    monkeypatch.setattr(reports.os, "link", deny_link)
    monkeypatch.setattr(reports.os, "replace", forbidden_replace)

    with pytest.raises(reports.ReportError, match="synthetic hard-link unsupported") as caught:
        reports.publish_html_report(target, payload)

    assert isinstance(caught.value.__cause__, OSError)
    assert not target.exists()
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []


def test_no_clobber_publish_and_cleanup_double_failure_preserves_both_errors(
        tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"
    real_unlink = Path.unlink

    monkeypatch.setattr(
        reports.os, "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic report link failure")))

    def deny_private_cleanup(path, *args, **kwargs):
        if path.name.startswith(".gridwise-report-"):
            raise PermissionError("synthetic no-clobber cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_private_cleanup)
    try:
        with pytest.raises(reports.ReportError) as caught:
            reports.publish_html_report(target, payload)
        residuals = list(tmp_path.glob(".gridwise-report-*.tmp"))
        assert len(residuals) == 1
        message = str(caught.value)
        assert "synthetic report link failure" in message
        assert "synthetic no-clobber cleanup denial" in message
        assert str(residuals[0].resolve()) in message
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert not target.exists()
    finally:
        monkeypatch.setattr(Path, "unlink", real_unlink)
        for residual in tmp_path.glob(".gridwise-report-*.tmp"):
            real_unlink(residual)


def test_no_clobber_published_report_reports_private_cleanup_failure(
        tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"
    cancellation = reports.ReportCancellation()
    real_unlink = Path.unlink

    def deny_private_cleanup(path, *args, **kwargs):
        if path.name.startswith(".gridwise-report-"):
            raise PermissionError("synthetic no-clobber cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_private_cleanup)
    try:
        with pytest.raises(reports.ReportError, match="was published") as caught:
            reports.publish_html_report(
                target, payload, cancel_event=cancellation)
        residuals = list(tmp_path.glob(".gridwise-report-*.tmp"))
        assert len(residuals) == 1
        message = str(caught.value)
        assert "synthetic no-clobber cleanup denial" in message
        assert str(residuals[0].resolve()) in message
        assert isinstance(caught.value.__cause__, PermissionError)
        assert target.read_bytes() == payload
        assert cancellation.set() is False
    finally:
        monkeypatch.setattr(Path, "unlink", real_unlink)
        for residual in tmp_path.glob(".gridwise-report-*.tmp"):
            real_unlink(residual)


def test_report_replace_and_cleanup_double_failure_preserves_original_error_and_path(
        tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    target.write_bytes(b"original")
    real_unlink = Path.unlink

    monkeypatch.setattr(
        reports.os, "replace",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic report replace failure")))

    def deny_private_cleanup(path, *args, **kwargs):
        if path.name.startswith(".gridwise-report-"):
            raise PermissionError("synthetic report cleanup denial")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_private_cleanup)
    try:
        with pytest.raises(reports.ReportError) as caught:
            reports.publish_html_report(
                target, b"<!doctype html><html><body>new report</body></html>",
                overwrite=True)
        residuals = list(tmp_path.glob(".gridwise-report-*.tmp"))
        assert len(residuals) == 1
        message = str(caught.value)
        assert "synthetic report replace failure" in message
        assert "synthetic report cleanup denial" in message
        assert str(residuals[0].resolve()) in message
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert target.read_bytes() == b"original"
    finally:
        monkeypatch.setattr(Path, "unlink", real_unlink)
        for residual in tmp_path.glob(".gridwise-report-*.tmp"):
            real_unlink(residual)


def test_export_refuses_damaged_run_and_unmatched_comparison_without_output(
        tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    runs_root = tmp_path / "runs"
    cost = dashboard.run_comparison(imported, runs_root=runs_root)
    capped = dashboard.run_comparison(imported, runs_root=runs_root, peak_cap_kw=55)
    damaged = runs_root / cost.run_name / "optimized.csv"
    damaged.write_bytes(damaged.read_bytes() + b"damage")
    single_target = tmp_path / "single.html"

    with pytest.raises(reports.ReportError, match="single-run report unavailable"):
        reports.export_single_run_report(
            single_target, imported, cost.run_name, runs_root,
            generated_at=GENERATED_AT)
    assert not single_target.exists()

    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    unmatched = HistoricalRunComparison(
        ComparedRun(cost.run_name, "cost", None, ComparisonMetrics(1, 2, 3), devices,
                    "cost-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparedRun(capped.run_name, "peak_cap", 55, ComparisonMetrics(2, 2, 2), devices,
                    "peak-cap-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparisonMetrics(1, 0, -1),
        {"cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"},
        "sha256:" + "2" * 64,
        fairness_status="FAIL",
    )
    monkeypatch.setattr(reports, "compare_verified_runs", lambda *_args, **_kwargs: unmatched)
    comparison_target = tmp_path / "comparison.html"
    with pytest.raises(reports.ReportError, match="VERIFIED and MATCHED"):
        reports.export_comparison_report(
            comparison_target, imported, cost.run_name, capped.run_name, runs_root,
            generated_at=GENERATED_AT)
    assert not comparison_target.exists()


@pytest.mark.parametrize("invalid_metric", [float("nan"), float("inf"), float("-inf")])
def test_report_rejects_nonfinite_metric_before_publication(
        tmp_path, monkeypatch, invalid_metric):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "1" * 32
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    chart = {
        "metrics": {
            "optimized": {"cost_cny": invalid_metric, "grid_energy_kwh": 1, "peak_grid_kw": 1},
            "baseline": {"cost_cny": 1, "grid_energy_kwh": 1, "peak_grid_kw": 1},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": 0, "grid_energy_kwh": 0, "peak_grid_kw": 0},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))
    target = tmp_path / "invalid.html"

    with pytest.raises(reports.ReportError, match="expected finite number"):
        reports.export_single_run_report(
            target, imported, run_name, tmp_path, generated_at=GENERATED_AT)
    assert not target.exists()


def test_report_rejects_missing_metric_before_publication(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    run_name = "run-" + "1" * 32
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    chart = {
        "metrics": {
            "optimized": {"cost_cny": 1, "grid_energy_kwh": 1},
            "baseline": {"cost_cny": 1, "grid_energy_kwh": 1, "peak_grid_kw": 1},
        },
        "delta_optimized_minus_baseline": {
            "cost_cny": 0, "grid_energy_kwh": 0, "peak_grid_kw": 0},
    }
    monkeypatch.setattr(
        reports, "load_verified_comparison",
        lambda *_args, **_kwargs: dashboard.DashboardResult(
            chart, "VERIFIED", run_name, "cost", None, None, devices))
    target = tmp_path / "missing.html"

    with pytest.raises(reports.ReportError, match="missing metric"):
        reports.export_single_run_report(
            target, imported, run_name, tmp_path, generated_at=GENERATED_AT)
    assert not target.exists()


def test_report_write_failure_leaves_no_final_or_private_file(tmp_path, monkeypatch):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"
    real_open = Path.open

    def deny_private_write(path, *args, **kwargs):
        if path.name.startswith(".gridwise-report-"):
            raise PermissionError("synthetic report write denial")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_private_write)

    with pytest.raises(reports.ReportError, match="synthetic report write denial") as caught:
        reports.publish_html_report(target, payload)
    assert isinstance(caught.value.__cause__, PermissionError)
    assert not target.exists()
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []


@pytest.mark.parametrize("overwrite", [False, True])
def test_cancel_before_final_report_commit_preserves_target(
        tmp_path, monkeypatch, overwrite):
    target = tmp_path / "report.html"
    original = b"<!doctype html><html><body>original</body></html>"
    if overwrite:
        target.write_bytes(original)
    payload = b"<!doctype html><html><body>replacement</body></html>"
    cancellation = reports.ReportCancellation()
    staged = threading.Event()
    release = threading.Event()
    real_fsync = reports.os.fsync
    outcome = []

    def pause_after_sync(file_descriptor):
        real_fsync(file_descriptor)
        staged.set()
        assert release.wait(2), "test did not release staged report"

    monkeypatch.setattr(reports.os, "fsync", pause_after_sync)

    def worker():
        try:
            reports.publish_html_report(
                target, payload, overwrite=overwrite,
                cancel_event=cancellation)
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert staged.wait(2)
    assert cancellation.set() is True
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], reports.ReportError)
    assert "cancelled" in str(outcome[0])
    if overwrite:
        assert target.read_bytes() == original
    else:
        assert not target.exists()
    assert list(tmp_path.glob(".gridwise-report-*.tmp")) == []


def test_committed_report_rejects_late_cancellation(tmp_path):
    target = tmp_path / "report.html"
    payload = b"<!doctype html><html><body>verified</body></html>"
    cancellation = reports.ReportCancellation()

    assert reports.publish_html_report(
        target, payload, cancel_event=cancellation) == target
    assert cancellation.set() is False
    assert target.read_bytes() == payload


def test_cancel_after_render_prevents_report_publication(tmp_path, monkeypatch):
    imported = import_energy_csv(SAMPLE, 1)
    target = tmp_path / "cancelled.html"
    cancel = threading.Event()
    started = threading.Event()
    release = threading.Event()
    outcome = []

    def delayed_build(*_args, **_kwargs):
        started.set()
        release.wait(2)
        return b"<!doctype html><html><body>complete</body></html>"

    monkeypatch.setattr(reports, "build_single_run_html", delayed_build)

    def worker():
        try:
            reports.export_single_run_report(
                target, imported, "run-" + "1" * 32, tmp_path,
                cancel_event=cancel)
        except Exception as exc:  # Capture the public worker outcome.
            outcome.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(2)
    cancel.set()
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert len(outcome) == 1
    assert isinstance(outcome[0], reports.ReportError)
    assert "cancelled" in str(outcome[0])
    assert not target.exists()


def test_report_rejects_symlink_target_when_platform_allows_links(tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_bytes(b"original")
    target = tmp_path / "report.html"
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    payload = b"<!doctype html><html><body>verified</body></html>"

    with pytest.raises(reports.ReportError, match="link or junction"):
        reports.publish_html_report(target, payload, overwrite=True)
    assert outside.read_bytes() == b"original"
