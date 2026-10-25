"""Black-box check for the bounded, isolated Tk diagnostic entry point."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


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
