"""Visible Windows Tk smoke test using only the bundled synthetic CSV."""

import hashlib
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

from gridwise.desktop import EnergyDesktop, SAMPLE


def wait(root, predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.02)
    raise TimeoutError("GUI state timeout")


def verify_restart(config, run_name):
    root = tk.Tk()
    app = EnergyDesktop(root, config_path=config)
    try:
        root.deiconify()
        wait(root, lambda: app._active_kind is None)
        assert app._history_entries[run_name].status == "VALID"
        app._check()
        wait(root, lambda: app.imported is not None)
        app.selected_run.set(run_name + " [VALID]")
        app._load_history()
        wait(root, lambda: app._active_kind is None)
        assert app.chart is not None
        print("RESTART_HISTORY_OK")
    finally:
        app._on_close()
        wait(root, lambda: app._closed)


def main():
    before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="gridwise-v038-gui-") as temporary:
        config = Path(temporary) / "settings" / "desktop.json"
        runs_root = Path(temporary) / "runs"
        runs_root.mkdir()
        root = tk.Tk()
        app = EnergyDesktop(root, runs_root=runs_root, config_path=config)
        try:
            root.deiconify()
            root.update()
            app._save_runs_root()
            assert config.exists()
            app._check()
            wait(root, lambda: app._active_kind is None)
            assert app.imported is not None and app.imported.report.ready
            app._run()
            wait(root, lambda: app._active_kind is None)
            assert app.chart is not None, app.status.get()
            run_name = app.selected_run.get().split(" [", 1)[0]
            assert (Path(temporary) / "runs" / run_name / "COMPLETE").exists()
            print("VALID", run_name, app.chart["metrics"])

            app._refresh_history()
            wait(root, lambda: app._active_kind is None)
            assert app._history_entries[run_name].status == "VALID"
            app.selected_run.set(run_name + " [VALID]")
            app._load_history()
            wait(root, lambda: app._active_kind is None)
            assert app.chart is not None
            print("HISTORY_OK")

            bad = Path(temporary) / "bad.csv"
            bad.write_text("wrong,header\n1,2\n", encoding="utf-8")
            app.path.set(str(bad))
            app._check()
            wait(root, lambda: app._active_kind is None)
            assert app.imported is None and app.chart is None
            assert app.run_button.instate(["disabled"])
            print("INVALID_BLOCKED")
            assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == before
            print("INPUT_SHA256_UNCHANGED", before)
        finally:
            app._on_close()
            wait(root, lambda: app._closed)
        restarted = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--verify-restart",
             str(config), run_name], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30, check=False)
        assert restarted.returncode == 0, restarted.stderr + restarted.stdout
        assert "RESTART_HISTORY_OK" in restarted.stdout
        print("RESTART_HISTORY_OK")
    print("GUI_SMOKE_PASSED")


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify-restart":
        verify_restart(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("usage: smoke_desktop_verified.py")
