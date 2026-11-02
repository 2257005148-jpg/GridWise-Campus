"""Windows Tk behavior at the user interaction boundary, using synthetic data."""

import sys
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("Tk desktop interactions are verified on Windows", allow_module_level=True)

import tkinter as tk
from tkinter import ttk

import gridwise.desktop as desktop
from gridwise.dashboard import (ComparedRun, ComparisonMetrics, DashboardError,
                                DashboardResult, HistoricalRunComparison,
                                RunHistoryEntry, RunHistoryPreview)
from gridwise.device import DeviceParameters
from gridwise.diagnostics import (FailureCategory, FailureStage, RunDiagnostic,
                                  diagnose_failure)
from gridwise.run_artifacts import RunArtifactError
from gridwise.scenario_presets import (OptimizationPolicy, ScenarioPreset,
                                       ScenarioPresetError, load_preset_exchange,
                                       serialize_preset_exchange)


_interpreter = None


def pump(root, condition, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("Tk did not reach the expected state")


def new_desktop(*, runs_root=None, config_path=None, preset_path=None):
    global _interpreter
    if _interpreter is None:
        _interpreter = tk.Tk()
        _interpreter.withdraw()
    root = tk.Toplevel(_interpreter)
    root.withdraw()
    app = desktop.EnergyDesktop(
        root, runs_root=runs_root, config_path=config_path, preset_path=preset_path)
    app._check()
    pump(root, lambda: app.imported is not None)
    return root, app


def close_desktop(root, app):
    """Exercise the production close path so scheduled callbacks are cancelled."""
    if root.winfo_exists():
        app._on_close()
        pump(root, lambda: root.winfo_exists() == 0)


def history_table_rows(app):
    return tuple(app.history_table.item(item, "values")
                 for item in app.history_table.get_children())


def invoke_history_heading(app, column):
    app.history_table.tk.call(app.history_table.heading(column, "command"))


def test_close_removes_scheduled_tk_poll_callback():
    root, app = new_desktop()
    poll_id = app._poll_id
    assert poll_id in root.tk.call("after", "info")
    close_desktop(root, app)
    assert poll_id not in root.tk.call("after", "info")


def test_quality_panel_shows_validated_input_summary_and_equipment_scope():
    root, app = new_desktop()
    try:
        message = app.quality.get("1.0", "end")
        scope = app.import_scope_label.cget("text")
        assert app.input_summary["start_timestamp"] == "2026-01-01T08:00:00+08:00"
        assert app.input_summary["end_timestamp"] == "2026-01-01T13:00:00+08:00"
        assert app.input_summary["load_kw"] == {"min": 55.0, "max": 90.0}
        assert app.input_summary["pv_kw"] == {"min": 0.0, "max": 70.0}
        assert app.input_summary["load_energy_kwh"] == pytest.approx(415)
        assert app.input_summary["pv_available_energy_kwh"] == pytest.approx(175)
        assert "数据摘要 / Input summary" in message
        assert "415.00 kWh" in message
        assert "175.00 kWh" in message
        assert "负荷、光伏、电价和时间序列" in scope
        assert "synthetic equipment scenario" in scope
        assert "96" in scope
    finally:
        close_desktop(root, app)


def test_device_defaults_change_invalidation_and_controller_handoff(monkeypatch):
    root, app = new_desktop()
    captured = []

    def capture(_imported, **kwargs):
        captured.append(kwargs["device_parameters"])
        raise DashboardError("synthetic stop after device capture")

    monkeypatch.setattr(desktop, "run_comparison", capture)
    try:
        assert app.device_values["grid_max_kw"].get() == "120"
        assert app.device_values["capacity_kwh"].get() == "80"
        assert "默认" in app.device_source.get() or "Default" in app.device_source.get()
        imported = app.imported
        app.chart = {"stale": True}

        app.device_values["capacity_kwh"].set("60")
        root.update()

        assert app.imported is imported
        assert app.chart is None
        assert app.run_button.instate(["!disabled"])
        assert "自定义" in app.device_source.get() or "Custom" in app.device_source.get()

        app._run()
        pump(root, lambda: app._active_kind is None)
        assert len(captured) == 1
        assert captured[0].capacity_kwh == 60
        assert captured[0].grid_max_kw == 120
        assert app.chart is None
    finally:
        close_desktop(root, app)


def test_invalid_device_parameter_blocks_controller_and_success(monkeypatch):
    root, app = new_desktop()
    calls = []
    monkeypatch.setattr(desktop, "run_comparison", lambda *_args, **_kwargs: calls.append(True))
    try:
        app.device_values["charge_efficiency"].set("0")
        app._run()
        root.update()

        assert calls == []
        assert app.chart is None
        assert app._active_kind is None
        assert "设备参数无效" in app.status.get() or "Invalid device" in app.status.get()
        assert "charge_efficiency" in app.quality.get("1.0", "end")
        assert "INPUT_INVALID" in app.quality.get("1.0", "end")
    finally:
        close_desktop(root, app)


def test_scenario_preset_crud_round_trip_and_delete_confirmation(monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    names = iter(("默认设备 / Default equipment", "削峰方案 / Peak plan"))
    monkeypatch.setattr(desktop.simpledialog, "askstring", lambda *_args, **_kwargs: next(names))
    confirmations = iter((False, True))
    monkeypatch.setattr(
        desktop.messagebox, "askyesno", lambda *_args, **_kwargs: next(confirmations))
    try:
        assert app.preset_combo.cget("values") in ("", ())

        app._save_preset_as()
        assert app.preset_combo.cget("values") == ("默认设备 / Default equipment",)

        app.device_values["capacity_kwh"].set("60")
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("50")
        root.update()
        app._save_preset_as()
        assert app.preset_combo.cget("values") == (
            "默认设备 / Default equipment", "削峰方案 / Peak plan")

        app.device_values["grid_max_kw"].set("100")
        root.update()
        app._update_selected_preset()
        stored = desktop.load_presets(preset_path)
        peak = next(item for item in stored if item.name == "削峰方案 / Peak plan")
        assert peak.device_parameters.grid_max_kw == 100
        assert peak.device_parameters.capacity_kwh == 60
        assert peak.optimization.mode == "peak_cap"
        assert peak.optimization.peak_cap_kw == 50

        app._delete_selected_preset()
        assert len(desktop.load_presets(preset_path)) == 2
        app._delete_selected_preset()
        assert tuple(item.name for item in desktop.load_presets(preset_path)) == (
            "默认设备 / Default equipment",)
    finally:
        close_desktop(root, app)


def test_preset_exchange_controls_import_without_changing_active_context(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    exchange_path = tmp_path / "外部教学楼预设.json"
    source = ScenarioPreset(
        "external-private-id", "教学楼午间削峰",
        DeviceParameters(100, 60, 24, 24, 20, 18, 0.91, 0.89),
        OptimizationPolicy("peak_cap", 55))
    exchange_path.write_bytes(serialize_preset_exchange(source))
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    monkeypatch.setattr(
        desktop.filedialog, "askopenfilename", lambda **_kwargs: str(exchange_path))
    solver_calls = []
    monkeypatch.setattr(
        desktop, "run_comparison", lambda *_args, **_kwargs: solver_calls.append(True))
    try:
        assert "Import" in app.import_preset_button.cget("text")
        assert "Export" in app.export_preset_button.cget("text")
        assert app.export_preset_button.instate(["disabled"])
        imported_input = app.imported
        devices = {field: value.get() for field, value in app.device_values.items()}
        policy = (app.optimization_mode.get(), app.peak_cap.get())
        app.chart = {"existing": True}
        app.history_preview = object()
        app.history_comparison = object()
        app.selected_run.set("existing-run")
        app.export_run_button.configure(state="normal")
        app.export_comparison_button.configure(state="normal")
        generation = app._generation

        app._import_preset_exchange()
        root.update()

        stored = desktop.load_presets(preset_path)
        assert len(stored) == 1
        assert stored[0].preset_id != source.preset_id
        assert stored[0].name == "教学楼午间削峰"
        assert stored[0].device_parameters == source.device_parameters
        assert stored[0].optimization == source.optimization
        assert app.selected_preset.get() == "教学楼午间削峰"
        assert app.export_preset_button.instate(["!disabled"])
        assert app.imported is imported_input
        assert {field: value.get() for field, value in app.device_values.items()} == devices
        assert (app.optimization_mode.get(), app.peak_cap.get()) == policy
        assert app.chart == {"existing": True}
        assert app.history_preview is not None
        assert app.history_comparison is not None
        assert app.selected_run.get() == "existing-run"
        assert app.export_run_button.instate(["!disabled"])
        assert app.export_comparison_button.instate(["!disabled"])
        assert app._generation == generation
        assert solver_calls == []
    finally:
        close_desktop(root, app)


def test_preset_exchange_duplicate_cancel_and_rename_are_non_overwriting(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    existing = ScenarioPreset(
        "existing-id", "同名预设", DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95),
        OptimizationPolicy("cost"))
    desktop.save_presets(preset_path, (existing,))
    external = ScenarioPreset(
        "external-id", "同名预设", DeviceParameters(100, 60, 24, 24, 20, 18, 0.91, 0.89),
        OptimizationPolicy("peak_cap", 55))
    exchange_path = tmp_path / "duplicate.json"
    exchange_path.write_bytes(serialize_preset_exchange(external))
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    monkeypatch.setattr(
        desktop.filedialog, "askopenfilename", lambda **_kwargs: str(exchange_path))
    rename_choices = iter((False, True))
    monkeypatch.setattr(
        desktop.messagebox, "askyesno",
        lambda *_args, **_kwargs: next(rename_choices))
    monkeypatch.setattr(
        desktop.simpledialog, "askstring",
        lambda *_args, **_kwargs: "同名预设（导入）")
    try:
        before = preset_path.read_bytes()
        selected = app.selected_preset.get()
        generation = app._generation

        app._import_preset_exchange()
        root.update()

        assert preset_path.read_bytes() == before
        assert app.selected_preset.get() == selected
        assert app._generation == generation

        app._import_preset_exchange()
        root.update()

        stored = desktop.load_presets(preset_path)
        assert tuple(item.name for item in stored) == (
            "同名预设", "同名预设（导入）")
        imported = stored[1]
        assert imported.preset_id not in {existing.preset_id, external.preset_id}
        assert imported.device_parameters == external.device_parameters
        assert imported.optimization == external.optimization
        assert app.selected_preset.get() == imported.name
        assert app._generation == generation
    finally:
        close_desktop(root, app)


def test_preset_exchange_import_cancel_and_save_failure_keep_store_and_selection(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    existing = ScenarioPreset(
        "existing-id", "现有预设", DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95),
        OptimizationPolicy("cost"))
    desktop.save_presets(preset_path, (existing,))
    external = ScenarioPreset(
        "external-id", "外部预设", DeviceParameters(100, 60, 24, 24, 20, 18, 0.91, 0.89),
        OptimizationPolicy("peak_cap", 55))
    exchange_path = tmp_path / "external.json"
    exchange_path.write_bytes(serialize_preset_exchange(external))
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    try:
        before = preset_path.read_bytes()
        selected = app.selected_preset.get()
        status = app.status.get()
        monkeypatch.setattr(
            desktop.filedialog, "askopenfilename", lambda **_kwargs: "")

        app._import_preset_exchange()

        assert preset_path.read_bytes() == before
        assert app.selected_preset.get() == selected
        assert app.status.get() == status

        monkeypatch.setattr(
            desktop.filedialog, "askopenfilename", lambda **_kwargs: str(exchange_path))
        monkeypatch.setattr(
            desktop, "save_presets",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ScenarioPresetError("synthetic preset save failure")))

        app._import_preset_exchange()
        root.update()

        assert preset_path.read_bytes() == before
        assert app.selected_preset.get() == selected
        assert "导入失败" in app.status.get() or "import failed" in app.status.get()
    finally:
        close_desktop(root, app)


def test_preset_exchange_export_reloads_saved_id_and_preserves_existing_target(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    source = ScenarioPreset(
        "saved-id", "导出预设", DeviceParameters(100, 60, 24, 24, 20, 18, 0.91, 0.89),
        OptimizationPolicy("peak_cap", 55))
    desktop.save_presets(preset_path, (source,))
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    target = tmp_path / "exported.json"
    targets = iter(("", str(target), str(target)))
    monkeypatch.setattr(
        desktop.filedialog, "asksaveasfilename",
        lambda **_kwargs: next(targets))
    try:
        selected = app.selected_preset.get()
        status = app.status.get()

        app._export_selected_preset()
        assert not target.exists()
        assert app.selected_preset.get() == selected
        assert app.status.get() == status

        app._export_selected_preset()
        imported = load_preset_exchange(target)
        assert imported.name == source.name
        assert imported.device_parameters == source.device_parameters
        assert imported.optimization == source.optimization
        assert "exported.json" in app.status.get()

        before = target.read_bytes()
        app._export_selected_preset()
        assert target.read_bytes() == before
        assert app.selected_preset.get() == selected
        assert "导出失败" in app.status.get() or "export failed" in app.status.get()

        desktop.save_presets(preset_path, ())
        app._export_selected_preset()
        assert app.selected_preset.get() == selected
        assert app.export_preset_button.instate(["disabled"])
        assert "preset id not found" in app.quality.get("1.0", "end")
    finally:
        close_desktop(root, app)


def test_scenario_preset_list_restores_at_startup_and_cost_mode_loads(monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    config_path = tmp_path / "desktop.json"
    root, app = new_desktop(config_path=config_path, preset_path=preset_path)
    monkeypatch.setattr(desktop.simpledialog, "askstring", lambda *_args, **_kwargs: "Cost")
    try:
        app._save_preset_as()
    finally:
        close_desktop(root, app)

    root, app = new_desktop(config_path=config_path, preset_path=preset_path)
    try:
        assert app.preset_combo.cget("values") == ("Cost",)
        assert app.selected_preset.get() == "Cost"
        app.device_values["capacity_kwh"].set("60")
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("50")
        root.update()
        generation = app._generation

        app._load_selected_preset()
        root.update()

        assert app._generation == generation + 1
        assert app.optimization_mode.get() == "cost"
        assert app.device_values["capacity_kwh"].get() == "80"
    finally:
        close_desktop(root, app)


def test_invalid_current_configuration_is_rejected_before_preset_name_prompt(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    prompted = []
    monkeypatch.setattr(
        desktop.simpledialog, "askstring", lambda *_args, **_kwargs: prompted.append(True))
    try:
        app.device_values["charge_efficiency"].set("0")
        root.update()
        app._save_preset_as()

        assert prompted == []
        assert not preset_path.exists()
        assert "预设保存失败" in app.status.get() or "Preset save failed" in app.status.get()
    finally:
        close_desktop(root, app)


def test_loading_peak_preset_is_atomic_invalidates_once_and_keeps_checked_csv(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    monkeypatch.setattr(
        desktop.simpledialog, "askstring", lambda *_args, **_kwargs: "峰值 55 / Peak 55")
    solver_calls = []
    monkeypatch.setattr(
        desktop, "run_comparison", lambda *_args, **_kwargs: solver_calls.append(True))
    try:
        app.device_values["grid_max_kw"].set("100")
        app.device_values["capacity_kwh"].set("60")
        app.device_values["max_charge_kw"].set("20")
        app.device_values["max_discharge_kw"].set("18")
        app.device_values["initial_kwh"].set("24")
        app.device_values["terminal_kwh"].set("24")
        app.device_values["charge_efficiency"].set("0.91")
        app.device_values["discharge_efficiency"].set("0.89")
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("55")
        root.update()
        app._save_preset_as()

        imported = app.imported
        app.device_values["capacity_kwh"].set("80")
        app.optimization_mode.set("cost")
        root.update()
        app.chart = {"stale": True}
        app.history_preview = object()
        app.history_comparison = object()
        app.selected_run.set("stale-run")
        app.load_button.configure(state="normal")
        app.export_run_button.configure(state="normal")
        app.export_comparison_button.configure(state="normal")
        app._show_diagnostic(diagnose_failure(RuntimeError("stale diagnostic")))
        generation = app._generation

        app._load_selected_preset()
        root.update()

        assert app._generation == generation + 1
        assert app.imported is imported
        assert app.optimization_mode.get() == "peak_cap"
        assert app.peak_cap.get() == "55"
        assert {field: value.get() for field, value in app.device_values.items()} == {
            "grid_max_kw": "100",
            "capacity_kwh": "60",
            "max_charge_kw": "20",
            "max_discharge_kw": "18",
            "initial_kwh": "24",
            "terminal_kwh": "24",
            "charge_efficiency": "0.91",
            "discharge_efficiency": "0.89",
        }
        assert app.chart is None
        assert app.current_diagnostic is None
        assert app.history_preview is None
        assert app.history_comparison is None
        assert app.selected_run.get() == ""
        assert app.load_button.instate(["disabled"])
        assert app.export_run_button.instate(["disabled"])
        assert app.export_comparison_button.instate(["disabled"])
        assert solver_calls == []
    finally:
        close_desktop(root, app)


def test_preset_load_clears_real_history_tree_selection_and_late_event(
        monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    monkeypatch.setattr(desktop.simpledialog, "askstring", lambda *_args, **_kwargs: "Safe")
    entry = RunHistoryEntry(
        "run-" + "a" * 32, "VALID", "verified", "cost", None)
    try:
        app._save_preset_as()
        app._show_history((entry,))
        app.history_table.selection_set(entry.name)
        app.history_table.focus(entry.name)
        app._select_history()

        assert app.selected_run.get() == app._history_label(entry)
        assert app.load_button.instate(["!disabled"])
        assert app.preview_button.instate(["!disabled"])
        assert app.export_run_button.instate(["!disabled"])

        app._load_selected_preset()
        root.update()

        assert app.history_table.selection() == ()
        assert app.history_table.focus() == ""
        assert app.selected_run.get() == ""
        assert app.history_selection_detail.get() == (
            "选择运行查看状态与详情 / Select a run to view status and details")
        assert app.load_button.instate(["disabled"])
        assert app.preview_button.instate(["disabled"])
        assert app.export_run_button.instate(["disabled"])

        app.history_table.event_generate("<<TreeviewSelect>>")
        root.update()
        assert app.history_table.selection() == ()
        assert app.selected_run.get() == ""
        assert app.load_button.instate(["disabled"])
        assert app.preview_button.instate(["disabled"])
        assert app.export_run_button.instate(["disabled"])
    finally:
        close_desktop(root, app)


def test_preset_load_failure_keeps_all_configuration_unchanged(monkeypatch, tmp_path):
    preset_path = tmp_path / "scenario-presets.json"
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json", preset_path=preset_path)
    monkeypatch.setattr(desktop.simpledialog, "askstring", lambda *_args, **_kwargs: "Safe")
    try:
        app._save_preset_as()
        before_devices = {field: value.get() for field, value in app.device_values.items()}
        before_policy = (app.optimization_mode.get(), app.peak_cap.get())
        generation = app._generation
        preset_path.write_text('{"format_version":1,"presets":[', encoding="utf-8")

        app._load_selected_preset()
        root.update()

        assert {field: value.get() for field, value in app.device_values.items()} == before_devices
        assert (app.optimization_mode.get(), app.peak_cap.get()) == before_policy
        assert app._generation == generation
        assert "预设加载失败" in app.status.get() or "Preset load failed" in app.status.get()
    finally:
        close_desktop(root, app)


def test_preset_controls_are_disabled_while_a_task_is_active(monkeypatch, tmp_path):
    root, app = new_desktop(
        config_path=tmp_path / "desktop.json",
        preset_path=tmp_path / "scenario-presets.json")
    monkeypatch.setattr(desktop.simpledialog, "askstring", lambda *_args, **_kwargs: "Busy")
    try:
        app._save_preset_as()
        app._active_kind = "run"
        app._sync_policy_controls()
        assert app.preset_combo.instate(["disabled"])
        assert app.save_preset_button.instate(["disabled"])
        assert app.load_preset_button.instate(["disabled"])
        assert app.update_preset_button.instate(["disabled"])
        assert app.delete_preset_button.instate(["disabled"])
        assert app.import_preset_button.instate(["disabled"])
        assert app.export_preset_button.instate(["disabled"])

        app._active_kind = None
        app._sync_policy_controls()
        assert app.preset_combo.instate(["readonly"])
        assert app.save_preset_button.instate(["!disabled"])
        assert app.load_preset_button.instate(["!disabled"])
        assert app.import_preset_button.instate(["!disabled"])
        assert app.export_preset_button.instate(["!disabled"])
    finally:
        close_desktop(root, app)


def test_run_keeps_tk_responsive_and_rejects_duplicate_clicks(monkeypatch):
    root, app = new_desktop()
    release = threading.Event()
    calls = []

    def slow_run(_imported, **_kwargs):
        calls.append(True)
        release.wait(2)
        raise DashboardError("synthetic solver failure")

    monkeypatch.setattr(desktop, "run_comparison", slow_run)
    try:
        app._run()
        app._run()
        pump(root, lambda: bool(calls))
        assert len(calls) == 1
        assert app.progress.winfo_manager() == "pack"
        fired = []
        root.after(10, lambda: fired.append(True))
        pump(root, lambda: bool(fired), seconds=0.5)
        assert "计算" in app.status.get() or "Running" in app.status.get()
        release.set()
        pump(root, lambda: "失败" in app.status.get() or "failed" in app.status.get())
        assert app.chart is None
        assert app.progress.winfo_manager() == ""
    finally:
        release.set()
        close_desktop(root, app)


def test_peak_cap_mode_rejects_invalid_input_and_passes_valid_cap(monkeypatch):
    root, app = new_desktop()
    calls = []

    def capture(_imported, **kwargs):
        calls.append(kwargs.get("peak_cap_kw"))
        raise DashboardError("synthetic stop after capture")

    monkeypatch.setattr(desktop, "run_comparison", capture)
    try:
        assert app.optimization_mode.get() == "cost"
        assert app.peak_cap_entry.instate(["disabled"])
        app.optimization_mode.set("peak_cap")
        root.update()
        assert app.peak_cap_entry.instate(["!disabled"])

        for invalid in ("", "not-a-number", "NaN", "Infinity", "-1", "1e13"):
            app.peak_cap.set(invalid)
            app._run()
            root.update()
            assert calls == []
            assert app.chart is None
            assert "上限" in app.status.get() or "cap" in app.status.get()
            assert "INPUT_INVALID" in app.quality.get("1.0", "end")

        app.peak_cap.set("55")
        app._run()
        pump(root, lambda: app._active_kind is None)
        assert calls == [55.0]
        assert app.chart is None
        assert "失败" in app.status.get() or "failed" in app.status.get()
    finally:
        close_desktop(root, app)


def test_desktop_peak_cap_run_shows_verified_metrics_and_cost_reference(tmp_path):
    runs_root = tmp_path / "runs"
    root, app = new_desktop(runs_root=runs_root)
    source_before = desktop.SAMPLE.read_bytes()
    try:
        app._run()
        pump(root, lambda: app._active_kind is None, seconds=25)
        assert app.chart["metrics"]["optimized"]["cost_cny"] == pytest.approx(131.0110803324)

        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("55")
        app._run()
        pump(root, lambda: app._active_kind is None, seconds=25)
        assert app.chart["metrics"]["optimized"]["cost_cny"] == pytest.approx(156.9)
        assert app.chart["metrics"]["optimized"]["grid_energy_kwh"] == pytest.approx(243.9)
        assert app.chart["metrics"]["optimized"]["peak_grid_kw"] == pytest.approx(55)
        assert app.chart["metrics"]["baseline"]["peak_grid_kw"] == pytest.approx(60)
        text = app.policy_result.get()
        assert "55.00 kW" in text
        assert "PASS" in text
        assert "+25.89 CNY" in text
        assert len(app._history_entries) == 2
        assert {entry.optimization_mode for entry in app._history_entries.values()} == {
            "cost", "peak_cap"}
        assert desktop.SAMPLE.read_bytes() == source_before
    finally:
        close_desktop(root, app)


def test_infeasible_peak_cap_never_shows_success(tmp_path):
    runs_root = tmp_path / "runs"
    root, app = new_desktop(runs_root=runs_root)
    try:
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("0")
        app._run()
        pump(root, lambda: app._active_kind is None, seconds=25)
        assert app.chart is None
        assert "失败" in app.status.get() or "failed" in app.status.get()
        assert "AUDIT PASSED" not in app.status.get()
        assert all(not (path / "COMPLETE").exists() for path in runs_root.glob("run-*"))
    finally:
        close_desktop(root, app)


def test_input_change_discards_in_flight_success(monkeypatch, tmp_path):
    root, app = new_desktop()
    release = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    def delayed_success(_imported, **_kwargs):
        started.set()
        release.wait(2)
        finished.set()
        return DashboardResult({"timestamps": []}, "AUDIT PASSED")

    monkeypatch.setattr(desktop, "run_comparison", delayed_success)
    try:
        app._run()
        pump(root, started.is_set)
        app.path.set(str(tmp_path / "another.csv"))
        release.set()
        pump(root, finished.is_set)
        for _ in range(8):
            root.update()
            time.sleep(0.01)
        assert app.chart is None
        assert app.input_summary is None
        assert all(label.cget("text") == "—" for pair in app.metric_labels.values()
                   for label in pair)
        assert "AUDIT PASSED" not in app.status.get()
    finally:
        release.set()
        close_desktop(root, app)


def test_incomplete_chart_never_shows_success(monkeypatch):
    root, app = new_desktop()
    monkeypatch.setattr(desktop, "run_comparison", lambda _imported, **_kwargs:
                        DashboardResult({"timestamps": ["2026-01-01T00:00:00+00:00"],
                                         "series": {}, "metrics": {}}, "AUDIT PASSED"))
    try:
        app._run()
        pump(root, lambda: not app._job_running())
        assert app.chart is None
        assert "AUDIT PASSED" not in app.status.get()
        assert "图表" in app.quality.get("1.0", "end") or "chart" in app.quality.get("1.0", "end")
    finally:
        close_desktop(root, app)


def test_close_cancels_running_job(monkeypatch):
    root, app = new_desktop()
    cancelled = threading.Event()

    def waiting(_imported, **kwargs):
        event = kwargs["cancel_event"]
        event.wait(2)
        if event.is_set():
            cancelled.set()
        raise DashboardError("cancelled")

    monkeypatch.setattr(desktop, "run_comparison", waiting)
    app._run()
    pump(root, app._job_running)
    app._on_close()
    assert cancelled.wait(1)
    pump(root, lambda: root.winfo_exists() == 0)


@pytest.mark.parametrize("payload", [
    b"",
    b"timestamp,load_kw,pv_kw,price_cny_per_kwh\n2026-01-01T00:00:00+00:00,,0,0.5\n",
    b"timestamp,load_w,pv_kw,price_cny_per_kwh\n",
    b"timestamp,load_kw,pv_kw,price_cny_per_kwh\nnot-a-date,1,0,0.5\n",
])
def test_invalid_csv_disables_run_and_clears_results(tmp_path, payload):
    root, app = new_desktop()
    path = tmp_path / "bad.csv"
    path.write_bytes(payload)
    try:
        app.path.set(str(path))
        app._check()
        pump(root, lambda: "未通过" in app.status.get() or "Invalid" in app.status.get())
        assert app.run_button.instate(["disabled"])
        assert app.chart is None
        assert app.input_summary is None
        assert "数据摘要 / Input summary" not in app.quality.get("1.0", "end")
        assert path.read_bytes() == payload
    finally:
        close_desktop(root, app)


def test_quality_summary_remains_visible_but_run_is_blocked_above_desktop_limit(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    path = tmp_path / "too-many-periods.csv"
    path.write_text(
        "timestamp,load_kw,pv_kw,price_cny_per_kwh\n" + "".join(
            f"{(start + timedelta(hours=index)).isoformat()},10,0,0.5\n"
            for index in range(desktop.MAX_INTERACTIVE_PERIODS + 1)),
        encoding="utf-8",
        newline="",
    )
    root, app = new_desktop()
    try:
        app.path.set(str(path))
        app._check()
        pump(root, lambda: app._active_kind is None)
        assert app.imported is not None and app.imported.report.ready
        assert app.input_summary["row_count"] == desktop.MAX_INTERACTIVE_PERIODS + 1
        assert "数据摘要 / Input summary" in app.quality.get("1.0", "end")
        assert app.run_button.instate(["disabled"])
        assert "最多 96" in app.status.get() or "limit 96" in app.status.get()
    finally:
        close_desktop(root, app)


@pytest.mark.parametrize("factor", [1.0, 1.25, 1.5, 2.0])
def test_minimum_window_keeps_charts_and_metric_cards_usable_at_dpi_scales(factor):
    global _interpreter
    if _interpreter is None:
        _interpreter = tk.Tk()
        _interpreter.withdraw()
    original = float(_interpreter.tk.call("tk", "scaling"))
    root = None
    try:
        _interpreter.tk.call("tk", "scaling", original * factor)
        root, app = new_desktop()
        root.geometry("900x650")
        root.deiconify()
        root.update()
        assert app.notebook.winfo_height() >= 200
        assert app.notebook.winfo_reqwidth() <= app.notebook.winfo_width()
        for card in app._cards:
            assert card.winfo_width() >= card.winfo_reqwidth() - 2
        assert app.run_button.winfo_rootx() + app.run_button.winfo_width() <= (
            app._viewport.winfo_rootx() + app._viewport.winfo_width())
        assert app.cancel_button.winfo_rootx() + app.cancel_button.winfo_width() <= (
            app._viewport.winfo_rootx() + app._viewport.winfo_width())
        assert app._viewport.yview()[1] < 1
        app._viewport.yview_moveto(1)
        root.update()
        assert app.notebook.winfo_rooty() < (
            app._viewport.winfo_rooty() + app._viewport.winfo_height())
        assert app.notebook.winfo_rooty() + app.notebook.winfo_height() > (
            app._viewport.winfo_rooty())
    finally:
        if root is not None:
            close_desktop(root, app)
        _interpreter.tk.call("tk", "scaling", original)


def test_very_long_invalid_path_is_reported_without_crashing(tmp_path):
    root, app = new_desktop()
    long_path = str(tmp_path / ("x" * 270 + ".csv"))
    try:
        app.path.set(long_path)
        app._check()
        pump(root, lambda: "未通过" in app.status.get() or "失败" in app.status.get())
        assert app.chart is None
        assert app.run_button.instate(["disabled"])
        assert long_path in app.quality.get("1.0", "end")
    finally:
        close_desktop(root, app)


def test_chart_time_labels_remain_separate_at_double_scaling():
    global _interpreter
    if _interpreter is None:
        _interpreter = tk.Tk()
        _interpreter.withdraw()
    original = float(_interpreter.tk.call("tk", "scaling"))
    root = None
    try:
        _interpreter.tk.call("tk", "scaling", original * 2)
        root, app = new_desktop()
        root.geometry("900x650")
        root.deiconify()
        root.update()
        app._show_result(desktop.run_comparison(app.imported))
        root.update()
        canvas = app.chart_canvases[0][0]
        texts = [(canvas.itemcget(item, "text"), canvas.bbox(item))
                 for item in canvas.find_all() if canvas.type(item) == "text"]
        labels = [box for value, box in texts if value.startswith("01-01")]
        assert len(labels) == 3
        labels.sort(key=lambda box: box[0])
        assert all(first[2] < second[0] for first, second in zip(labels, labels[1:]))
        assert all(box[3] < canvas.winfo_height() - 20 for box in labels)
    finally:
        if root is not None:
            close_desktop(root, app)
        _interpreter.tk.call("tk", "scaling", original)


def test_main_sets_dpi_awareness_before_creating_tk():
    code = """
import ctypes
import gridwise.desktop as desktop
u = ctypes.WinDLL('user32', use_last_error=True)
u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
u.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
before = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
class Root:
    def mainloop(self):
        after = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
        print(before, after)
desktop.tk.Tk = Root
desktop.EnergyDesktop = lambda root: None
desktop.main()
"""
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=10, check=False)
    assert completed.returncode == 0, completed.stderr
    before, after = map(int, completed.stdout.strip().split())
    if before == 0:
        assert after == 2  # per-monitor awareness v1/v2
    else:
        assert after == before  # do not override an existing process setting


def test_existing_dpi_awareness_is_preserved():
    code = """
import ctypes
from gridwise.desktop import _enable_windows_dpi_awareness
u = ctypes.WinDLL('user32', use_last_error=True)
u.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
u.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
u.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
assert u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))
before = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
_enable_windows_dpi_awareness()
after = u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext())
print(before, after)
"""
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=10, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "1 1"


def test_read_only_log_is_keyboard_reachable_and_page_can_scroll():
    root, app = new_desktop()
    try:
        root.geometry("900x650")
        root.deiconify()
        root.update()
        widget = app.check_button
        seen = set()
        for _ in range(20):
            widget = widget.tk_focusNext()
            seen.add(str(widget))
        assert str(app.quality) in seen
        assert str(app.run_button) in seen
        assert str(app.history_search_entry) in seen
        assert str(app.history_status_combo) in seen
        assert str(app.history_mode_combo) in seen
        assert str(app.clear_history_filters_button) in seen
        assert str(app.history_table) in seen
        assert str(app.compare_a_combo) in seen
        assert str(app.compare_b_combo) in seen
        assert str(app.comparison_table) in seen
        assert app.quality.tk_focusPrev() == app.comparison_table
        app.check_button.focus_set()
        root.update()
        before = app._viewport.yview()[0]
        app.check_button.event_generate("<Next>")
        root.update()
        assert app._viewport.yview()[0] > before
    finally:
        close_desktop(root, app)


def test_verified_desktop_run_history_and_damaged_result(tmp_path):
    root, app = new_desktop(runs_root=tmp_path / "runs")
    try:
        app._run()
        pump(root, lambda: app.chart is not None or app._active_kind is None, seconds=25)
        assert app.chart is not None, app.status.get() + app.quality.get("1.0", "end")
        name = app.selected_run.get().split(" [", 1)[0]
        assert name.startswith("run-")
        assert "VERIFIED" in app.status.get()
        assert (tmp_path / "runs" / name / "COMPLETE").exists()

        app._refresh_history()
        pump(root, lambda: app._active_kind is None, seconds=10)
        assert name in app._history_entries
        assert app._history_entries[name].status == "VALID"
        app.selected_run.set(f"{name} [VALID]")
        app._load_history()
        pump(root, lambda: app._active_kind is None, seconds=10)
        assert app.chart is not None

        target = tmp_path / "runs" / name / "optimized.csv"
        target.write_bytes(target.read_bytes() + b"damage")
        app._refresh_history()
        pump(root, lambda: app._active_kind is None, seconds=10)
        assert app._history_entries[name].status == "DAMAGED"
        app.selected_run.set(f"{name} [DAMAGED]")
        app._load_history()
        pump(root, lambda: app._active_kind is None, seconds=10)
        assert app.chart is None
        assert "失败" in app.status.get()
    finally:
        close_desktop(root, app)


def test_history_search_and_filters_use_memory_and_clear_restores_all(monkeypatch):
    root, app = new_desktop()
    entries = [
        RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost"),
        RunHistoryEntry("run-" + "b" * 32, "VALID", "verified", "peak_cap", 55),
        RunHistoryEntry("run-" + "c" * 32, "INCOMPLETE", "missing marker"),
        RunHistoryEntry("run-" + "d" * 32, "DAMAGED", "hash mismatch"),
    ]
    monkeypatch.setattr(
        desktop, "list_run_history",
        lambda *_args, **_kwargs: pytest.fail("in-memory filtering must not rescan disk"))
    try:
        app._show_history(entries)
        all_names = tuple(entry.name for entry in entries)
        assert tuple(row[0] for row in history_table_rows(app)) == all_names

        app.history_search.set("  RUN-" + "B" * 8)
        assert tuple(row[0] for row in history_table_rows(app)) == (entries[1].name,)

        app.history_search.set("")
        app.history_status_filter.set("DAMAGED")
        assert tuple(row[0] for row in history_table_rows(app)) == (entries[3].name,)

        app.history_status_filter.set(desktop.HISTORY_FILTER_ALL)
        app.history_mode_filter.set(desktop.HISTORY_MODE_COST)
        assert tuple(row[0] for row in history_table_rows(app)) == (entries[0].name,)

        app.history_search.set("not-present")
        assert history_table_rows(app) == ()
        assert "没有匹配运行" in app.history_filter_notice.get()
        assert app.load_button.instate(["disabled"])
        assert app.export_run_button.instate(["disabled"])

        app._clear_history_filters()
        assert app.history_search.get() == ""
        assert app.history_status_filter.get() == desktop.HISTORY_FILTER_ALL
        assert app.history_mode_filter.get() == desktop.HISTORY_FILTER_ALL
        assert tuple(row[0] for row in history_table_rows(app)) == all_names
        assert "显示 4 / 4" in app.history_filter_notice.get()
    finally:
        close_desktop(root, app)


def test_history_filter_combination_keeps_invalid_status_semantics():
    root, app = new_desktop()
    incomplete = RunHistoryEntry(
        "run-" + "e" * 32, "INCOMPLETE", "missing COMPLETE")
    damaged = RunHistoryEntry(
        "run-" + "f" * 32, "DAMAGED", "manifest digest mismatch")
    peak = RunHistoryEntry(
        "run-" + "e" * 16 + "f" * 16, "VALID", "verified", "peak_cap", 55)
    try:
        app._show_history([incomplete, damaged, peak])
        app.history_search.set("RUN-" + "E" * 8)
        app.history_status_filter.set("INCOMPLETE")
        assert tuple(row[0] for row in history_table_rows(app)) == (incomplete.name,)
        assert app._history_entries[incomplete.name].status == "INCOMPLETE"

        app.history_status_filter.set(desktop.HISTORY_FILTER_ALL)
        app.history_mode_filter.set(desktop.HISTORY_MODE_PEAK_CAP)
        assert tuple(row[0] for row in history_table_rows(app)) == (peak.name,)
        app.history_table.selection_set(peak.name)
        app._select_history()
        assert app.load_button.instate(["!disabled"])

        app.history_status_filter.set("DAMAGED")
        assert history_table_rows(app) == ()
        assert app.selected_run.get() == ""
    finally:
        close_desktop(root, app)


def test_history_table_shows_columns_and_invalid_detail_without_trusted_actions():
    root, app = new_desktop()
    cost = RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost")
    peak = RunHistoryEntry(
        "run-" + "b" * 32, "VALID", "verified", "peak_cap", 55)
    incomplete = RunHistoryEntry(
        "run-" + "c" * 32, "INCOMPLETE", "missing COMPLETE marker")
    try:
        app._show_history([cost, peak, incomplete])
        assert tuple(app.history_table.cget("columns")) == (
            "name", "status", "mode", "peak_cap")
        assert app.history_table.cget("yscrollcommand")
        assert history_table_rows(app) == (
            (cost.name, "VALID", desktop.HISTORY_MODE_COST, "—"),
            (peak.name, "VALID", desktop.HISTORY_MODE_PEAK_CAP, "55 kW"),
            (incomplete.name, "INCOMPLETE", "未知 / Unknown", "—"),
        )

        app.history_table.selection_set(peak.name)
        app._select_history()
        assert app._selected_history_name(app.selected_run.get()) == peak.name
        assert app.load_button.instate(["!disabled"])

        app.history_table.selection_set(incomplete.name)
        app._select_history()
        assert app.load_button.instate(["disabled"])
        assert app.export_run_button.instate(["disabled"])
        assert "missing COMPLETE marker" in app.history_selection_detail.get()
    finally:
        close_desktop(root, app)


def test_history_table_heading_sort_toggles_and_combines_with_filters(monkeypatch):
    root, app = new_desktop()
    missing = RunHistoryEntry("run-" + "d" * 32, "DAMAGED", "hash mismatch")
    cap_60 = RunHistoryEntry(
        "run-" + "c" * 32, "VALID", "verified", "peak_cap", 60)
    cost = RunHistoryEntry("run-" + "b" * 32, "VALID", "verified", "cost")
    cap_40 = RunHistoryEntry(
        "run-" + "a" * 32, "VALID", "verified", "peak_cap", 40)
    monkeypatch.setattr(
        desktop, "list_run_history",
        lambda *_args, **_kwargs: pytest.fail("sorting must not rescan disk"))
    monkeypatch.setattr(
        desktop, "preview_verified_run",
        lambda *_args, **_kwargs: pytest.fail("sorting and filtering must not preview"))
    try:
        app._show_history([missing, cap_60, cost, cap_40])
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_40.name, cost.name, cap_60.name, missing.name)

        invoke_history_heading(app, "peak_cap")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_40.name, cap_60.name, missing.name, cost.name)
        assert "▲" in app.history_table.heading("peak_cap", "text")

        invoke_history_heading(app, "peak_cap")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_60.name, cap_40.name, missing.name, cost.name)
        assert "▼" in app.history_table.heading("peak_cap", "text")

        app.history_mode_filter.set(desktop.HISTORY_MODE_PEAK_CAP)
        app.history_search.set("RUN-")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_60.name, cap_40.name)

        invoke_history_heading(app, "status")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_60.name, cap_40.name)
        invoke_history_heading(app, "status")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_60.name, cap_40.name)

        app._clear_history_filters()
        invoke_history_heading(app, "mode")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cost.name, cap_60.name, cap_40.name, missing.name)

        invoke_history_heading(app, "name")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            cap_40.name, cost.name, cap_60.name, missing.name)
        invoke_history_heading(app, "name")
        assert tuple(row[0] for row in history_table_rows(app)) == (
            missing.name, cap_60.name, cost.name, cap_40.name)
    finally:
        close_desktop(root, app)


def test_history_preview_button_shows_verified_metrics_mode_and_cap(monkeypatch):
    root, app = new_desktop()
    cost_name = "run-" + "a" * 32
    peak_name = "run-" + "b" * 32
    entries = [
        RunHistoryEntry(cost_name, "VALID", "verified", "cost"),
        RunHistoryEntry(peak_name, "VALID", "verified", "peak_cap", 55),
    ]
    previews = {
        cost_name: RunHistoryPreview(
            cost_name, "cost", None, ComparisonMetrics(131.01, 247.02, 90)),
        peak_name: RunHistoryPreview(
            peak_name, "peak_cap", 55, ComparisonMetrics(156.90, 243.90, 55)),
    }
    calls = []

    def preview(imported, run_name, runs_root, *, cancel_event):
        calls.append((imported, run_name, runs_root, cancel_event))
        return previews[run_name]

    monkeypatch.setattr(desktop, "preview_verified_run", preview)
    try:
        app._show_history(entries)
        app.history_table.selection_set(peak_name)
        app._select_history()
        assert app.preview_button.instate(["!disabled"])
        app._preview_history()
        pump(root, lambda: app._active_kind is None)

        assert len(calls) == 1
        assert calls[0][1:3] == (peak_name, Path(app.runs_root.get()))
        assert app.history_preview == previews[peak_name]
        assert app.preview_table.item("cost_cny", "values") == (
            "电费 / Cost", "156.90 CNY")
        assert app.preview_table.item("grid_energy_kwh", "values") == (
            "购电量 / Grid energy", "243.90 kWh")
        assert app.preview_table.item("peak_grid_kw", "values") == (
            "峰值购电功率 / Grid peak", "55.00 kW")
        assert "峰值上限 / Peak cap" in app.history_preview_context.get()
        assert "55" in app.history_preview_context.get()
        assert "VERIFIED" in app.history_preview_status.get()

        app.history_table.selection_set(cost_name)
        app._select_history()
        assert app.history_preview is None
        app._preview_history()
        pump(root, lambda: app._active_kind is None)
        assert len(calls) == 2
        assert "纯电费 / Cost-only" in app.history_preview_context.get()
        assert "—" in app.history_preview_context.get()
    finally:
        close_desktop(root, app)


def test_invalid_history_selection_clears_preview_and_never_invokes_controller(monkeypatch):
    root, app = new_desktop()
    valid = RunHistoryEntry("run-" + "a" * 32, "VALID", "verified", "cost")
    damaged = RunHistoryEntry(
        "run-" + "b" * 32, "DAMAGED", "manifest digest mismatch")
    preview = RunHistoryPreview(
        valid.name, "cost", None, ComparisonMetrics(1, 2, 3))
    monkeypatch.setattr(
        desktop, "preview_verified_run",
        lambda *_args, **_kwargs: pytest.fail("invalid selection must not preview"))
    try:
        app._show_history([valid, damaged])
        app.history_table.selection_set(valid.name)
        app._select_history()
        app._show_history_preview(preview)
        assert app.history_preview is preview

        app.history_table.selection_set(damaged.name)
        app._select_history()
        assert app.history_preview is None
        assert app.preview_button.instate(["disabled"])
        assert "manifest digest mismatch" in app.history_selection_detail.get()
        assert app.preview_table.item("cost_cny", "values")[1] == "—"

        app.history_search.set("RUN-")
        invoke_history_heading(app, "status")
        assert app.history_preview is None
    finally:
        close_desktop(root, app)


def test_late_history_preview_cannot_replace_new_selection_or_generation(monkeypatch, tmp_path):
    root, app = new_desktop()
    name_a = "run-" + "a" * 32
    name_b = "run-" + "b" * 32
    entries = [
        RunHistoryEntry(name_a, "VALID", "verified", "cost"),
        RunHistoryEntry(name_b, "VALID", "verified", "peak_cap", 55),
    ]
    started = threading.Event()
    release = threading.Event()

    def delayed(_imported, run_name, _runs_root, *, cancel_event):
        started.set()
        release.wait(2)
        return RunHistoryPreview(
            run_name, "cost", None, ComparisonMetrics(1, 2, 3))

    monkeypatch.setattr(desktop, "preview_verified_run", delayed)
    try:
        app._show_history(entries)
        app.history_table.selection_set(name_a)
        app._select_history()
        app._preview_history()
        pump(root, started.is_set)

        app.history_table.selection_set(name_b)
        app._select_history()
        assert app.history_preview is None
        release.set()
        pump(root, lambda: app._active_kind is None)
        assert app.history_preview is None
        assert "VERIFIED" not in app.history_preview_status.get()

        started.clear()
        release.clear()
        app.history_table.selection_set(name_a)
        app._select_history()
        app._preview_history()
        pump(root, started.is_set)
        app.path.set(str(tmp_path / "changed.csv"))
        release.set()
        pump(root, lambda: not app._pending_jobs)
        root.update()
        assert app.history_preview is None
        assert app.imported is None
    finally:
        release.set()
        close_desktop(root, app)


def test_history_preview_failure_and_cancellation_clear_old_metrics(monkeypatch):
    root, app = new_desktop()
    name = "run-" + "a" * 32
    entry = RunHistoryEntry(name, "VALID", "verified", "cost")
    old_preview = RunHistoryPreview(
        name, "cost", None, ComparisonMetrics(131.01, 247.02, 90))
    cancelled = threading.Event()

    try:
        app._show_history([entry])
        app.history_table.selection_set(name)
        app._select_history()
        app._show_history_preview(old_preview)

        def fail_preview(*_args, **_kwargs):
            raise DashboardError("synthetic preview failure")

        monkeypatch.setattr(desktop, "preview_verified_run", fail_preview)
        app._preview_history()
        pump(root, lambda: app._active_kind is None)
        assert app.history_preview is None
        assert app.preview_table.item("cost_cny", "values")[1] == "—"
        assert "预览失败" in app.status.get()

        def wait_for_cancel(_imported, _name, _root, *, cancel_event):
            assert cancel_event.wait(2)
            cancelled.set()
            raise DashboardError("synthetic cancelled preview")

        monkeypatch.setattr(desktop, "preview_verified_run", wait_for_cancel)
        app._preview_history()
        pump(root, lambda: app._active_kind == "history_preview")
        app._cancel_job()
        pump(root, lambda: app._active_kind is None)
        assert cancelled.is_set()
        assert app.history_preview is None
        assert app.preview_table.item("cost_cny", "values")[1] == "—"
        assert app.status.get() == "已取消 / Cancelled"
    finally:
        close_desktop(root, app)


def test_verified_desktop_two_runs_and_input_change(tmp_path):
    root, app = new_desktop(runs_root=tmp_path / "runs")
    try:
        names = []
        for _ in range(2):
            app._run()
            pump(root, lambda: app._active_kind is None, seconds=25)
            assert app.chart is not None, app.quality.get("1.0", "end")
            names.append(app.selected_run.get().split(" [", 1)[0])
        assert names[0] != names[1]
        assert len(list((tmp_path / "runs").glob("run-*"))) == 2
        app.path.set(str(tmp_path / "other.csv"))
        assert app.chart is None
        assert app.imported is None
        assert app.load_button.instate(["disabled"])
    finally:
        close_desktop(root, app)


def test_history_comparison_controls_call_controller_and_show_verified_table(monkeypatch):
    root, app = new_desktop()
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    name_a = "run-" + "a" * 32
    name_b = "run-" + "b" * 32
    entries = [
        RunHistoryEntry(name_a, "VALID", "verified", "cost", None),
        RunHistoryEntry(name_b, "VALID", "verified", "peak_cap", 55),
    ]
    result = HistoricalRunComparison(
        ComparedRun(name_a, "cost", None,
                    ComparisonMetrics(131.01108033241, 247.02216066482, 90),
                    devices, "cost-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparedRun(name_b, "peak_cap", 55,
                    ComparisonMetrics(156.9, 243.9, 55),
                    devices, "peak-cap-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparisonMetrics(25.88891966759, 243.9 - 247.02216066482, -35),
        {"cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"},
        "sha256:" + "2" * 64,
    )
    captured = []

    def compare(imported, run_a, run_b, runs_root, *, cancel_event):
        captured.append((imported, run_a, run_b, runs_root, cancel_event))
        return result

    monkeypatch.setattr(desktop, "compare_verified_runs", compare)
    try:
        app._show_history(entries)
        app.selected_compare_a.set(app._history_label(entries[0]))
        app.selected_compare_b.set(app._history_label(entries[1]))
        app._select_comparison()
        assert app.compare_button.instate(["!disabled"])

        app._compare_history()
        pump(root, lambda: app._active_kind is None)

        assert len(captured) == 1
        assert captured[0][1:3] == (name_a, name_b)
        assert app.history_comparison == result
        assert app.comparison_table.item("cost_cny", "values") == (
            "购电费用 / Cost", "131.01 CNY", "156.90 CNY", "+25.89 CNY")
        assert app.comparison_table.item("grid_energy_kwh", "values")[3].endswith("kWh")
        assert app.comparison_table.item("peak_grid_kw", "values")[3] == "-35.00 kW"
        assert "VERIFIED" in app.comparison_status.get()
        assert "MATCHED" in app.comparison_status.get()
        assert "55" in app.comparison_context.get()
        assert "80" in app.comparison_context.get()
    finally:
        close_desktop(root, app)


def test_history_comparison_is_invalidated_by_device_and_runs_root_changes():
    root, app = new_desktop()
    try:
        app.history_comparison = object()
        app.comparison_status.set("VERIFIED")
        app.comparison_table.item(
            "cost_cny", values=("购电费用 / Cost", "1.00 CNY", "2.00 CNY", "+1.00 CNY"))

        app.device_values["capacity_kwh"].set("60")
        root.update()

        assert app.history_comparison is None
        assert app.comparison_table.item("cost_cny", "values")[1:] == ("—", "—", "—")
        assert "No verified comparison" in app.comparison_status.get()

        app.history_comparison = object()
        app.selected_compare_a.set("run-a [VALID]")
        app.selected_compare_b.set("run-b [VALID]")
        app.runs_root.set(str(Path(app.runs_root.get()) / "different-root"))
        root.update()

        assert app.history_comparison is None
        assert app.selected_compare_a.get() == ""
        assert app.selected_compare_b.get() == ""
        assert app.compare_a_combo.cget("values") in ("", ())
        assert app.compare_b_combo.cget("values") in ("", ())
    finally:
        close_desktop(root, app)


def test_late_history_comparison_result_cannot_replace_invalidated_input(monkeypatch, tmp_path):
    root, app = new_desktop()
    name_a = "run-" + "a" * 32
    name_b = "run-" + "b" * 32
    entries = [
        RunHistoryEntry(name_a, "VALID", "verified", "cost", None),
        RunHistoryEntry(name_b, "VALID", "verified", "peak_cap", 55),
    ]
    started = threading.Event()
    release = threading.Event()

    def delayed_compare(*_args, **_kwargs):
        started.set()
        release.wait(2)
        devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
        return HistoricalRunComparison(
            ComparedRun(name_a, "cost", None, ComparisonMetrics(1, 2, 3), devices,
                        "cost-mip-v1+sha256." + "1" * 64, "appsi_highs"),
            ComparedRun(name_b, "peak_cap", 55, ComparisonMetrics(2, 2, 2), devices,
                        "peak-cap-mip-v1+sha256." + "1" * 64, "appsi_highs"),
            ComparisonMetrics(1, 0, -1),
            {"cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"},
            "sha256:" + "2" * 64,
        )

    monkeypatch.setattr(desktop, "compare_verified_runs", delayed_compare)
    try:
        app._show_history(entries)
        app.selected_compare_a.set(app._history_label(entries[0]))
        app.selected_compare_b.set(app._history_label(entries[1]))
        app._select_comparison()
        app._compare_history()
        pump(root, started.is_set)

        app.path.set(str(tmp_path / "different.csv"))
        assert app.history_comparison is None
        release.set()
        pump(root, lambda: not app._pending_jobs)
        root.update()

        assert app.history_comparison is None
        assert "VERIFIED" not in app.comparison_status.get()
        assert app.imported is None
    finally:
        release.set()
        close_desktop(root, app)


def test_loading_history_with_different_devices_immediately_invalidates_comparison(tmp_path):
    runs_root = tmp_path / "runs"
    root, app = new_desktop(runs_root=runs_root)
    custom_devices = DeviceParameters(120, 60, 30, 30, 20, 20, 0.95, 0.95)
    try:
        cost = desktop.run_comparison(app.imported, runs_root=runs_root)
        capped = desktop.run_comparison(app.imported, runs_root=runs_root, peak_cap_kw=55)
        custom = desktop.run_comparison(
            app.imported, runs_root=runs_root, device_parameters=custom_devices)
        entries = desktop.list_run_history(runs_root)
        app._show_history(entries)

        app.selected_compare_a.set(app._history_label(app._history_entries[cost.run_name]))
        app.selected_compare_b.set(app._history_label(app._history_entries[capped.run_name]))
        app._select_comparison()
        app._compare_history()
        pump(root, lambda: app._active_kind is None)
        assert app.history_comparison is not None
        assert "VERIFIED" in app.comparison_status.get()

        app.selected_run.set(app._history_label(app._history_entries[custom.run_name]))
        app._load_history()

        assert app.history_comparison is None
        assert "VERIFIED" not in app.comparison_status.get()
        pump(root, lambda: app._active_kind is None)
        assert app.device_values["capacity_kwh"].get() == "60"
        assert app.chart is not None
    finally:
        close_desktop(root, app)


def test_report_export_controls_use_verified_background_workflows(monkeypatch, tmp_path):
    root, app = new_desktop()
    devices = DeviceParameters(120, 80, 40, 40, 30, 30, 0.95, 0.95)
    name_a = "run-" + "a" * 32
    name_b = "run-" + "b" * 32
    entries = [
        RunHistoryEntry(name_a, "VALID", "verified", "cost", None),
        RunHistoryEntry(name_b, "VALID", "verified", "peak_cap", 55),
    ]
    comparison = HistoricalRunComparison(
        ComparedRun(name_a, "cost", None, ComparisonMetrics(131, 247, 90), devices,
                    "cost-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparedRun(name_b, "peak_cap", 55, ComparisonMetrics(157, 244, 55), devices,
                    "peak-cap-mip-v1+sha256." + "1" * 64, "appsi_highs"),
        ComparisonMetrics(26, -3, -35),
        {"cost_cny": "CNY", "grid_energy_kwh": "kWh", "peak_grid_kw": "kW"},
        "sha256:" + "2" * 64,
    )
    single_target = tmp_path / "single.html"
    comparison_target = tmp_path / "comparison.html"
    selected_targets = iter((single_target, comparison_target))
    calls = []

    monkeypatch.setattr(
        desktop.filedialog, "asksaveasfilename",
        lambda **_kwargs: str(next(selected_targets)))

    def export_single(target, imported, run_name, runs_root, *, cancel_event):
        calls.append(("single", target, imported, run_name, runs_root, cancel_event))
        return target

    def export_comparison(target, imported, run_a, run_b, runs_root, *, cancel_event):
        calls.append(("comparison", target, imported, run_a, run_b, runs_root, cancel_event))
        return target

    monkeypatch.setattr(desktop, "export_single_run_report", export_single)
    monkeypatch.setattr(desktop, "export_comparison_report", export_comparison)
    try:
        app._show_history(entries)
        app.selected_run.set(app._history_label(entries[0]))
        app._select_history()
        assert app.export_run_button.instate(["!disabled"])
        app._export_selected_run()
        pump(root, lambda: app._active_kind is None)
        assert calls[0][0:4] == ("single", single_target, app.imported, name_a)
        assert "single.html" in app.status.get()

        app.selected_compare_a.set(app._history_label(entries[0]))
        app.selected_compare_b.set(app._history_label(entries[1]))
        app._show_history_comparison(comparison)
        assert app.export_comparison_button.instate(["!disabled"])
        app._export_history_comparison()
        pump(root, lambda: app._active_kind is None)
        assert calls[1][0:5] == (
            "comparison", comparison_target, app.imported, name_a, name_b)
        assert "comparison.html" in app.status.get()

        app.device_values["capacity_kwh"].set("60")
        root.update()
        assert app.export_run_button.instate(["disabled"])
        assert app.export_comparison_button.instate(["disabled"])
    finally:
        close_desktop(root, app)


def test_late_report_export_result_cannot_override_changed_device_state(monkeypatch, tmp_path):
    root, app = new_desktop()
    name = "run-" + "a" * 32
    entry = RunHistoryEntry(name, "VALID", "verified", "cost", None)
    started = threading.Event()
    release = threading.Event()
    target = tmp_path / "late.html"

    monkeypatch.setattr(
        desktop.filedialog, "asksaveasfilename", lambda **_kwargs: str(target))

    def delayed_export(_target, _imported, _name, _root, *, cancel_event):
        started.set()
        cancel_event.wait(2)
        release.wait(2)
        return target

    monkeypatch.setattr(desktop, "export_single_run_report", delayed_export)
    try:
        app._show_history([entry])
        app.selected_run.set(app._history_label(entry))
        app._select_history()
        app._export_selected_run()
        pump(root, started.is_set)

        app.device_values["capacity_kwh"].set("60")
        root.update()
        release.set()
        pump(root, lambda: not app._pending_jobs)
        root.update()

        assert "Report exported" not in app.status.get()
        assert "设备参数已改变" in app.status.get() or "Device parameters changed" in app.status.get()
        assert app.export_run_button.instate(["disabled"])
    finally:
        release.set()
        close_desktop(root, app)


def test_report_cancel_after_commit_keeps_success_status(monkeypatch, tmp_path):
    root, app = new_desktop()
    name = "run-" + "a" * 32
    entry = RunHistoryEntry(name, "VALID", "verified", "cost", None)
    target = tmp_path / "committed.html"
    committed = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(
        desktop.filedialog, "asksaveasfilename", lambda **_kwargs: str(target))

    def committed_export(_target, _imported, _name, _root, *, cancel_event):
        cancel_event.commit(committed.set)
        assert release.wait(2)
        return target

    monkeypatch.setattr(desktop, "export_single_run_report", committed_export)
    try:
        app._show_history([entry])
        app.selected_run.set(app._history_label(entry))
        app._select_history()
        app._export_selected_run()
        pump(root, committed.is_set)

        app._cancel_job()

        assert not app._cancel_requested
        assert "already committed" in app.status.get()
        release.set()
        pump(root, lambda: app._active_kind is None)
        assert "Report exported" in app.status.get()
    finally:
        release.set()
        close_desktop(root, app)


def test_desktop_audit_failure_never_displays_success(tmp_path, monkeypatch):
    from gridwise import run_artifacts

    root, app = new_desktop(runs_root=tmp_path / "runs")
    monkeypatch.setattr(run_artifacts, "AUDITOR", tmp_path / "missing-auditor.py")
    try:
        app._run()
        pump(root, lambda: app._active_kind is None, seconds=25)
        assert app.chart is None
        assert "失败" in app.status.get()
        assert "AUDIT PASSED" not in app.status.get()
        runs = list((tmp_path / "runs").glob("run-*"))
        assert len(runs) == 1
        assert not (runs[0] / "COMPLETE").exists()
    finally:
        close_desktop(root, app)


def test_saved_runs_root_restores_history_after_desktop_restart(tmp_path):
    global _interpreter
    runs_root = tmp_path / "persistent-runs"
    runs_root.mkdir()
    config = tmp_path / "settings" / "desktop.json"
    first, app = new_desktop(runs_root=runs_root, config_path=config)
    try:
        app._save_runs_root()
        assert config.exists()
        app._run()
        pump(first, lambda: app._active_kind is None, seconds=25)
        assert app.chart is not None
        name = app.selected_run.get().split(" [", 1)[0]
    finally:
        close_desktop(first, app)

    second = tk.Toplevel(_interpreter)
    second.withdraw()
    reopened = desktop.EnergyDesktop(second, config_path=config)
    try:
        assert Path(reopened.runs_root.get()) == runs_root
        pump(second, lambda: reopened._active_kind is None, seconds=10)
        assert reopened._history_entries[name].status == "VALID"
        reopened._check()
        pump(second, lambda: reopened.imported is not None, seconds=10)
        reopened.selected_run.set(f"{name} [VALID]")
        reopened._load_history()
        pump(second, lambda: reopened._active_kind is None, seconds=10)
        assert reopened.chart is not None
        assert reopened.chart["metrics"]["optimized"]["cost_cny"] == pytest.approx(131.01108)
    finally:
        close_desktop(second, reopened)


def test_saved_peak_cap_history_restores_mode_after_restart(tmp_path):
    global _interpreter
    runs_root = tmp_path / "persistent-runs"
    runs_root.mkdir()
    config = tmp_path / "settings" / "desktop.json"
    first, app = new_desktop(runs_root=runs_root, config_path=config)
    try:
        app._save_runs_root()
        app.device_values["capacity_kwh"].set("60")
        app.device_values["initial_kwh"].set("30")
        app.device_values["terminal_kwh"].set("30")
        app.device_values["max_charge_kw"].set("20")
        app.device_values["max_discharge_kw"].set("20")
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("55")
        app._run()
        pump(first, lambda: app._active_kind is None, seconds=25)
        assert app.chart is not None
        name = app.selected_run.get().split(" [", 1)[0]
    finally:
        close_desktop(first, app)

    second = tk.Toplevel(_interpreter)
    second.withdraw()
    reopened = desktop.EnergyDesktop(second, config_path=config)
    try:
        pump(second, lambda: reopened._active_kind is None, seconds=10)
        assert reopened._history_entries[name].optimization_mode == "peak_cap"
        assert reopened._history_entries[name].peak_cap_kw == 55
        reopened._check()
        pump(second, lambda: reopened.imported is not None, seconds=10)
        reopened.selected_run.set(reopened._history_label(reopened._history_entries[name]))
        reopened._load_history()
        pump(second, lambda: reopened._active_kind is None, seconds=10)
        assert reopened.chart["metrics"]["optimized"]["peak_grid_kw"] == pytest.approx(55)
        assert reopened.optimization_mode.get() == "peak_cap"
        assert reopened.peak_cap.get() == "55"
        assert reopened.device_values["capacity_kwh"].get() == "60"
        assert reopened.device_values["initial_kwh"].get() == "30"
        assert reopened.device_values["terminal_kwh"].get() == "30"
        assert reopened.device_values["max_charge_kw"].get() == "20"
        assert reopened.device_values["max_discharge_kw"].get() == "20"
        assert "自定义" in reopened.device_source.get() or "Custom" in reopened.device_source.get()
        assert "55.00 kW" in reopened.policy_result.get()
    finally:
        close_desktop(second, reopened)


def test_all_quality_issues_remain_keyboard_readable():
    root, app = new_desktop()
    issues = [SimpleNamespace(line=index + 2, field="load_kw", code="missing",
                              detail=f"issue-{index:02d}") for index in range(15)]
    report = SimpleNamespace(ready=False, row_count=15, step_hours=1,
                             missing_counts={"load_kw": 15}, source="synthetic.csv",
                             issues=issues)
    try:
        root.deiconify()
        app._show_quality(SimpleNamespace(report=report))
        app.quality.focus_set()
        root.update()
        assert root.focus_get() == app.quality
        text = app.quality.get("1.0", "end")
        assert "issue-00" in text and "issue-14" in text
        assert app.run_button.instate(["disabled"])
        app.quality.mark_set("insert", "1.0")
        app.quality.event_generate("<Control-End>")
        root.update()
        assert app.quality.index("insert") != "1.0"
    finally:
        close_desktop(root, app)


def test_duplicate_quality_clicks_start_one_job(monkeypatch):
    root, app = new_desktop()
    release = threading.Event()
    started = threading.Event()
    calls = []
    real_import = desktop.import_energy_csv

    def slow_import(*args):
        calls.append(True)
        started.set()
        release.wait(2)
        return real_import(*args)

    monkeypatch.setattr(desktop, "import_energy_csv", slow_import)
    try:
        app._check()
        pump(root, started.is_set)
        app._check()
        assert len(calls) == 1
        release.set()
        pump(root, lambda: app._active_kind is None)
        assert app.check_button.instate(["!disabled"])
    finally:
        release.set()
        close_desktop(root, app)


def test_keyboard_browse_check_run_and_chart_tabs(monkeypatch, tmp_path):
    root, app = new_desktop()
    source = tmp_path / "keyboard.csv"
    source.write_bytes(desktop.SAMPLE.read_bytes())
    monkeypatch.setattr(desktop.filedialog, "askopenfilename", lambda **_kwargs: str(source))

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    browse = next(widget for widget in descendants(root)
                  if isinstance(widget, ttk.Button) and "Browse" in widget.cget("text"))

    def press_space(widget):
        widget.focus_set()
        root.update()
        assert root.focus_get() == widget
        widget.event_generate("<KeyPress-space>")
        widget.event_generate("<KeyRelease-space>")
        root.update()

    try:
        root.deiconify()
        root.update()
        press_space(browse)
        assert app.path.get() == str(source)
        assert app.imported is None
        press_space(app.check_button)
        pump(root, lambda: app.imported is not None)
        press_space(app.run_button)
        pump(root, lambda: app._active_kind is None, seconds=15)
        assert app.chart is not None
        assert "AUDIT PASSED" in app.status.get()
        app.notebook.focus_set()
        root.update()
        app.notebook.event_generate("<Right>")
        root.update()
        assert app.notebook.index("current") == 1
        app.quality.focus_set()
        root.update()
        assert root.focus_get() == app.quality
    finally:
        close_desktop(root, app)


def test_escape_cancels_run_and_allows_keyboard_retry(monkeypatch):
    root, app = new_desktop()
    cancelled = threading.Event()
    calls = []

    def cancellable(_imported, **kwargs):
        calls.append(True)
        event = kwargs["cancel_event"]
        if len(calls) == 1:
            event.wait(2)
            if event.is_set():
                cancelled.set()
            raise DashboardError("run cancelled")
        raise DashboardError("synthetic retry failure")

    monkeypatch.setattr(desktop, "run_comparison", cancellable)
    try:
        root.deiconify()
        root.update()
        app._run()
        pump(root, lambda: len(calls) == 1)
        assert app.check_button.instate(["disabled"])
        assert app.run_button.instate(["disabled"])
        assert app.cancel_button.instate(["!disabled"])
        app.cancel_button.focus_set()
        root.update()
        assert root.focus_get() == app.cancel_button
        app.cancel_button.event_generate("<Escape>")
        pump(root, cancelled.is_set)
        pump(root, lambda: app._active_kind is None)
        assert "取消" in app.status.get() or "Cancelled" in app.status.get()
        assert "AUDIT PASSED" not in app.status.get()
        assert app.chart is None
        assert app.cancel_button.instate(["disabled"])
        assert app.check_button.instate(["!disabled"])
        assert app.run_button.instate(["!disabled"])
        app._run()
        pump(root, lambda: app._active_kind is None)
        assert len(calls) == 2
        assert "失败" in app.status.get() or "failed" in app.status.get()
    finally:
        close_desktop(root, app)


def test_completed_queued_run_rejects_late_cancel_and_displays_success(monkeypatch):
    root, app = new_desktop()
    completed = threading.Event()
    result = DashboardResult(
        {"timestamps": ["2026-01-01T00:00:00+00:00"],
         "series": {key: [0.0] for _, definitions, _ in desktop.CHARTS
                    for key, _, _ in definitions},
         "metrics": {
             "optimized": {"cost_cny": 1.0, "grid_energy_kwh": 2.0,
                           "peak_grid_kw": 3.0},
             "baseline": {"cost_cny": 2.0, "grid_energy_kwh": 3.0,
                          "peak_grid_kw": 4.0}}},
        "AUDIT PASSED · VERIFIED")

    def immediate_success(_imported, **_kwargs):
        completed.set()
        return result

    monkeypatch.setattr(desktop, "run_comparison", immediate_success)
    try:
        app._run()
        assert completed.wait(1)
        deadline = time.monotonic() + 1
        while app._events.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not app._events.empty()

        app._cancel_job()

        assert not app._cancel_requested
        assert "already completed" in app.status.get()
        pump(root, lambda: app._active_kind is None)
        assert "AUDIT PASSED" in app.status.get()
        assert app.chart is not None
        displayed = app.status.get()
        app._cancel_job()
        assert app.status.get() == displayed
    finally:
        close_desktop(root, app)


def test_run_failure_shows_diagnostic_without_leaving_old_results(monkeypatch):
    root, app = new_desktop()
    error = RuntimeError("synthetic backend failure")
    diagnostic = diagnose_failure(error, stage_hint=FailureStage.SOLVE)

    def fail(_imported, **_kwargs):
        raise DashboardError("comparison unavailable", diagnostic=diagnostic)

    monkeypatch.setattr(desktop, "run_comparison", fail)
    try:
        app.chart = {"stale": True}
        app._run()
        pump(root, lambda: app._active_kind is None)

        log = app.quality.get("1.0", "end")
        assert app.chart is None
        assert app.export_run_button.instate(["disabled"])
        assert "UNKNOWN" in log
        assert "synthetic backend failure" in log
        assert app.current_diagnostic is diagnostic
        assert "UNKNOWN" in app.diagnostic_category.get()
        assert "证据不足" in app.diagnostic_summary.get()
        assert app.diagnostic_details_button.instate(["!disabled"])
        assert app.copy_diagnostic_button.instate(["!disabled"])
    finally:
        close_desktop(root, app)


@pytest.mark.parametrize(("category", "chinese"), [
    (FailureCategory.INPUT_INVALID, "输入无效"),
    (FailureCategory.INFEASIBLE, "模型不可行"),
    (FailureCategory.SOLVER_UNAVAILABLE, "求解器不可用"),
    (FailureCategory.SOLVER_TIMEOUT, "求解超时"),
    (FailureCategory.SOLVER_ERROR, "求解失败"),
    (FailureCategory.CANCELLED, "任务取消"),
    (FailureCategory.AUDIT_FAILED, "审计失败"),
    (FailureCategory.ARTIFACT_INVALID, "产物无效"),
    (FailureCategory.UNKNOWN, "未知原因"),
])
def test_diagnostic_panel_maps_every_category(category, chinese):
    root, app = new_desktop()
    error = RuntimeError(f"synthetic {category.value}")
    diagnostic = RunDiagnostic(
        category, FailureStage.CONTROL, f"summary {category.value}",
        ("evidence=present",), ("人工检查 / Check manually",),
        (f"RuntimeError: synthetic {category.value}",), error)
    try:
        app._show_diagnostic(diagnostic)

        assert category.value in app.diagnostic_category.get()
        assert chinese in app.diagnostic_category.get()
        assert "control" in app.diagnostic_stage.get()
        assert f"summary {category.value}" in app.diagnostic_summary.get()
        if category is FailureCategory.UNKNOWN:
            assert "证据不足，无法确定根因" in app.diagnostic_summary.get()
    finally:
        close_desktop(root, app)


def test_diagnostic_details_preserve_solver_evidence_exception_chain_and_copy(monkeypatch):
    root, app = new_desktop()
    underlying = ValueError("synthetic input origin")
    error = RunArtifactError(
        "command exited 2", stage="solve", reason="command_failed", returncode=2,
        stderr="gridwise: no optimal solution: error, infeasible\n")
    error.__cause__ = underlying
    diagnostic = diagnose_failure(error)
    try:
        app._show_diagnostic(diagnostic)
        app._toggle_diagnostic_details()
        details = app.diagnostic_details.get("1.0", "end")

        assert diagnostic.category is FailureCategory.INFEASIBLE
        assert "solver_status=error" in app.diagnostic_evidence.get()
        assert "termination_condition=infeasible" in app.diagnostic_evidence.get()
        assert "termination_condition=infeasible" in details
        assert "solver_status=error" in details
        assert "RunArtifactError: command exited 2" in details
        assert "ValueError: synthetic input origin" in details
        assert "人工检查建议 / Suggested checks" in details
        assert app.diagnostic_details.winfo_manager() == "pack"

        app._copy_diagnostic_details()
        assert root.clipboard_get() == details.rstrip("\n")
        assert app.current_diagnostic is diagnostic

        def clipboard_denied():
            raise tk.TclError("synthetic clipboard denial")

        monkeypatch.setattr(root, "clipboard_clear", clipboard_denied)
        app._copy_diagnostic_details()
        assert app.current_diagnostic is diagnostic
        assert "复制诊断失败" in app.status.get() or "Copy diagnosis failed" in app.status.get()
        assert "synthetic clipboard denial" in app.quality.get("1.0", "end")
    finally:
        close_desktop(root, app)


def test_diagnostic_clears_when_input_policy_device_or_root_changes():
    root, app = new_desktop()
    diagnostic = diagnose_failure(RuntimeError("synthetic diagnostic"))

    def show_then_change(change):
        app._show_diagnostic(diagnostic)
        assert app.current_diagnostic is diagnostic
        change()
        root.update()
        assert app.current_diagnostic is None
        assert app.diagnostic_details_button.instate(["disabled"])

    try:
        show_then_change(lambda: app.path.set(app.path.get() + ".changed"))
        show_then_change(lambda: app.step.set("0.5"))
        show_then_change(lambda: app.device_values["capacity_kwh"].set("61"))
        show_then_change(lambda: app.optimization_mode.set("peak_cap"))
        show_then_change(lambda: app.peak_cap.set("54"))
        show_then_change(lambda: app.runs_root.set(app.runs_root.get() + "-changed"))
    finally:
        close_desktop(root, app)


def test_new_job_and_success_clear_diagnostic(monkeypatch):
    root, app = new_desktop()
    diagnostic = diagnose_failure(RuntimeError("old failure"))
    result = DashboardResult(
        {"timestamps": ["2026-01-01T00:00:00+00:00"],
         "series": {key: [0.0] for _, definitions, _ in desktop.CHARTS
                    for key, _, _ in definitions},
         "metrics": {
             "optimized": {"cost_cny": 1.0, "grid_energy_kwh": 2.0,
                           "peak_grid_kw": 3.0},
             "baseline": {"cost_cny": 2.0, "grid_energy_kwh": 3.0,
                          "peak_grid_kw": 4.0}}},
        "AUDIT PASSED · VERIFIED")
    monkeypatch.setattr(desktop, "run_comparison", lambda *_args, **_kwargs: result)
    try:
        app._show_diagnostic(diagnostic)
        app._run()
        assert app.current_diagnostic is None
        pump(root, lambda: app._active_kind is None)
        assert app.current_diagnostic is None
        assert app.chart is not None
        assert app.diagnostic_details_button.instate(["disabled"])
    finally:
        close_desktop(root, app)


def test_cancel_and_late_failure_do_not_create_or_restore_diagnostic(monkeypatch, tmp_path):
    root, app = new_desktop()
    started = threading.Event()
    release = threading.Event()

    def delayed_failure(_imported, **kwargs):
        started.set()
        release.wait(2)
        if kwargs["cancel_event"].is_set():
            raise DashboardError("run cancelled")
        raise DashboardError("late synthetic failure")

    monkeypatch.setattr(desktop, "run_comparison", delayed_failure)
    try:
        app._run()
        pump(root, started.is_set)
        app._cancel_job()
        release.set()
        pump(root, lambda: app._active_kind is None)
        assert app.current_diagnostic is None
        assert "取消" in app.status.get() or "Cancelled" in app.status.get()

        release.clear()
        started.clear()
        app._run()
        pump(root, started.is_set)
        app.path.set(str(tmp_path / "new-input.csv"))
        release.set()
        pump(root, lambda: not app._pending_jobs)
        assert app.current_diagnostic is None
        assert "late synthetic failure" not in app.diagnostic_summary.get()
    finally:
        release.set()
        close_desktop(root, app)


def test_diagnostic_details_are_keyboard_focusable_scrollable_and_do_not_change_devices():
    root, app = new_desktop()
    values_before = {name: value.get() for name, value in app.device_values.items()}
    diagnostic = RunDiagnostic(
        FailureCategory.UNKNOWN, FailureStage.UNKNOWN, "synthetic unknown",
        tuple(f"evidence-{index:02d}" for index in range(30)),
        ("保留日志 / Keep logs",),
        ("RuntimeError: synthetic unknown",), RuntimeError("synthetic unknown"))
    try:
        root.deiconify()
        app._show_diagnostic(diagnostic)
        app._toggle_diagnostic_details()
        app.diagnostic_details.focus_set()
        root.update()
        assert root.focus_get() == app.diagnostic_details
        app.diagnostic_details.mark_set("insert", "1.0")
        app.diagnostic_details.event_generate("<Control-End>")
        root.update()
        assert app.diagnostic_details.index("insert") != "1.0"
        assert values_before == {name: value.get() for name, value in app.device_values.items()}
    finally:
        close_desktop(root, app)


def test_cancelled_quality_check_does_not_accept_late_result(monkeypatch):
    root, app = new_desktop()
    release = threading.Event()
    started = threading.Event()
    real_import = desktop.import_energy_csv

    def delayed_import(*args):
        started.set()
        release.wait(2)
        return real_import(*args)

    monkeypatch.setattr(desktop, "import_energy_csv", delayed_import)
    try:
        root.deiconify()
        root.update()
        app._check()
        pump(root, started.is_set)
        app.cancel_button.invoke()
        assert app.cancel_button.instate(["disabled"])
        release.set()
        pump(root, lambda: app._active_kind is None)
        assert app.imported is None
        assert app.run_button.instate(["disabled"])
        assert app.check_button.instate(["!disabled"])
        assert "取消" in app.status.get() or "Cancelled" in app.status.get()
        assert "AUDIT PASSED" not in app.status.get()
    finally:
        release.set()
        close_desktop(root, app)


def test_window_close_waits_for_background_temp_cleanup(monkeypatch, tmp_path):
    root, app = new_desktop()
    started = threading.Event()
    release_cleanup = threading.Event()
    finished = threading.Event()

    def delayed_cleanup(_imported, **kwargs):
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-", dir=tmp_path):
            started.set()
            kwargs["cancel_event"].wait(2)
            release_cleanup.wait(2)
        finished.set()
        raise DashboardError("cancelled")

    monkeypatch.setattr(desktop, "run_comparison", delayed_cleanup)
    try:
        app._run()
        pump(root, started.is_set)
        app._on_close()
        assert root.winfo_exists() == 1
        assert not finished.is_set()
        root.event_generate("<Escape>")
        root.update()
        assert "关闭" in app.status.get() or "Closing" in app.status.get()
        release_cleanup.set()
        pump(root, lambda: root.winfo_exists() == 0)
        assert finished.is_set()
        assert list(Path(tmp_path).glob("gridwise-ui-*")) == []
    finally:
        release_cleanup.set()
        if root.winfo_exists():
            close_desktop(root, app)


def test_window_close_waits_for_stale_job_after_input_change(monkeypatch, tmp_path):
    root, app = new_desktop()
    started = threading.Event()
    release_cleanup = threading.Event()

    def delayed_cleanup(_imported, **kwargs):
        with tempfile.TemporaryDirectory(prefix="gridwise-ui-", dir=tmp_path):
            started.set()
            kwargs["cancel_event"].wait(2)
            release_cleanup.wait(2)
        raise DashboardError("cancelled")

    monkeypatch.setattr(desktop, "run_comparison", delayed_cleanup)
    try:
        app._run()
        pump(root, started.is_set)
        app.path.set(str(tmp_path / "different.csv"))
        assert app._active_kind is None
        app._on_close()
        assert root.winfo_exists() == 1
        release_cleanup.set()
        pump(root, lambda: root.winfo_exists() == 0)
        assert list(Path(tmp_path).glob("gridwise-ui-*")) == []
    finally:
        release_cleanup.set()
        if root.winfo_exists():
            close_desktop(root, app)
