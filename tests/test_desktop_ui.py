"""Windows Tk behavior at the user interaction boundary, using synthetic data."""

import sys
import threading
import time

import pytest

if sys.platform != "win32":
    pytest.skip("Tk desktop interactions are verified on Windows", allow_module_level=True)

import tkinter as tk

import gridwise.desktop as desktop
from gridwise.dashboard import DashboardError, DashboardResult


_interpreter = None


def pump(root, condition, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("Tk did not reach the expected state")


def new_desktop():
    global _interpreter
    if _interpreter is None:
        _interpreter = tk.Tk()
        _interpreter.withdraw()
    root = tk.Toplevel(_interpreter)
    root.withdraw()
    app = desktop.EnergyDesktop(root)
    app._check()
    pump(root, lambda: app.imported is not None)
    return root, app


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
        fired = []
        root.after(10, lambda: fired.append(True))
        pump(root, lambda: bool(fired), seconds=0.5)
        assert "计算" in app.status.get() or "Running" in app.status.get()
        release.set()
        pump(root, lambda: "失败" in app.status.get() or "failed" in app.status.get())
        assert app.chart is None
    finally:
        release.set()
        root.destroy()


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
        assert all(label.cget("text") == "—" for pair in app.metric_labels.values()
                   for label in pair)
        assert "AUDIT PASSED" not in app.status.get()
    finally:
        release.set()
        root.destroy()


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
        root.destroy()


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
        assert path.read_bytes() == payload
    finally:
        root.destroy()
