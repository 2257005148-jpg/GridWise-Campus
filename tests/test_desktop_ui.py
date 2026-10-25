"""Windows Tk behavior at the user interaction boundary, using synthetic data."""

import sys
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("Tk desktop interactions are verified on Windows", allow_module_level=True)

import tkinter as tk
from tkinter import ttk

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


def close_desktop(root, app):
    """Exercise the production close path so scheduled callbacks are cancelled."""
    if root.winfo_exists():
        app._on_close()
        pump(root, lambda: root.winfo_exists() == 0)


def test_close_removes_scheduled_tk_poll_callback():
    root, app = new_desktop()
    poll_id = app._poll_id
    assert poll_id in root.tk.call("after", "info")
    close_desktop(root, app)
    assert poll_id not in root.tk.call("after", "info")


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
        assert path.read_bytes() == payload
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
        for _ in range(12):
            widget = widget.tk_focusNext()
            seen.add(str(widget))
        assert str(app.quality) in seen
        assert app.quality.tk_focusPrev() == app.run_button
        app.check_button.focus_set()
        root.update()
        before = app._viewport.yview()[0]
        app.check_button.event_generate("<Next>")
        root.update()
        assert app._viewport.yview()[0] > before
    finally:
        close_desktop(root, app)


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
