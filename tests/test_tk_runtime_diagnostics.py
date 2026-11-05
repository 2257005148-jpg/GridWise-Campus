"""Black-box check for the bounded, isolated Tk diagnostic entry point."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gui


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "diagnose_tk_runtime.py"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Tk runtime probe")
def test_tk_diagnostic_records_runtime_paths_and_focus(tmp_path):
    output = tmp_path / "probe.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "toplevel", "--count", "2",
         "--repeat", "1", "--output", str(output)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert (report["passed"], report["failed"]) == (1, 0)
    run = report["runs"][0]
    assert run["count_completed"] == 2
    assert run["exit_code"] == 0
    assert run["python"] and run["tk_version"] and run["tcl_version"]
    assert run["tcl_library"] and run["tk_library"] and run["auto_path"]
    for name in ("tk.tcl", "icons.tcl", "ttk/ttk.tcl", "ttk/scrollbar.tcl"):
        assert run["files_after"][name]["exists"]
        assert run["files_after"][name]["readable"]


def test_tk_diagnostic_rejects_unbounded_counts(tmp_path):
    output = tmp_path / "should-not-exist.json"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--count", "101", "--output", str(output)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
    )
    assert completed.returncode != 0
    assert "count must be 1..100" in completed.stderr
    assert not output.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Tk runtime probe")
def test_tcl_initialization_failure_produces_diagnostic_json(monkeypatch):
    import tkinter as tk

    module = runpy.run_path(str(SCRIPT))
    monkeypatch.setattr(tk, "Tcl", lambda: (_ for _ in ()).throw(
        OSError("synthetic Tcl startup failure")))
    result = module["probe"]("toplevel", 1)
    assert result["status"] == "FAIL"
    assert result["count_completed"] == 0
    assert "synthetic Tcl startup failure" in result["traceback"]
    assert result["files_before"]["icons.tcl"]["path"]
    assert result["files_after"]["ttk/scrollbar.tcl"]["path"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Tk runtime probe")
def test_focus_command_failure_preserves_traceback_in_json(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import ttk

    module = runpy.run_path(str(SCRIPT))

    class FakeInterpreter:
        def eval(self, command):
            return str(Path(sys.base_prefix) / "tcl" / "tk8.6") if command == "set tk_library" \
                else "synthetic Tcl path"

    class FakeRoot(FakeInterpreter):
        def __init__(self):
            self.tk = self

        def withdraw(self):
            pass

        def destroy(self):
            pass

    class FakeWindow:
        def update(self):
            pass

        def destroy(self):
            pass

    class FakeButton:
        def __init__(self, *_args, **_kwargs):
            pass

        def pack(self):
            pass

        def tk_focusNext(self):
            raise tk.TclError('invalid command name "tk_focusNext"')

    monkeypatch.setattr(tk, "Tcl", FakeInterpreter)
    monkeypatch.setattr(tk, "Tk", FakeRoot)
    monkeypatch.setattr(tk, "Toplevel", lambda _root: FakeWindow())
    monkeypatch.setattr(ttk, "Button", FakeButton)
    result = module["probe"]("toplevel", 1)
    output = tmp_path / "focus-failure.json"
    output.write_text(json.dumps(result), encoding="utf-8")
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["status"] == "FAIL"
    assert saved["count_completed"] == 0
    assert 'invalid command name "tk_focusNext"' in saved["traceback"]
    assert "diagnose_tk_runtime.py" in saved["traceback"]
    assert "_tkinter.TclError" in saved["traceback"]


def test_probe_child_output_is_bounded():
    module = runpy.run_path(str(SCRIPT))
    exit_code, stdout, stderr, failure = module["_run_child_bounded"](
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000)"])
    assert failure == "child output exceeded limit"
    assert len(stdout.encode("utf-8")) <= module["MAX_CHILD_JSON_BYTES"]
    assert len(stderr.encode("utf-8")) <= module["MAX_CHILD_STDERR_BYTES"]
    assert exit_code is not None
