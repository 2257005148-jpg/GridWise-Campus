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
        assert app.optimization_mode.get() == "peak_cap"
        assert app.peak_cap.get() == "55"
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
            cost_run_name = app.selected_run.get().split(" [", 1)[0]
            assert (Path(temporary) / "runs" / cost_run_name / "COMPLETE").exists()
            print("COST_VALID", cost_run_name, app.chart["metrics"])

            app.optimization_mode.set("peak_cap")
            app.peak_cap.set("55")
            app._run()
            wait(root, lambda: app._active_kind is None)
            assert app.chart["metrics"]["optimized"]["peak_grid_kw"] <= 55 + 1e-5
            assert "+25.89 CNY" in app.policy_result.get()
            run_name = app.selected_run.get().split(" [", 1)[0]
            assert (Path(temporary) / "runs" / run_name / "COMPLETE").exists()
            print("PEAK_VALID", run_name, app.chart["metrics"], app.policy_result.get())

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


def show_peak_result():
    """Open a real audited cost/peak comparison for manual visual inspection."""
    with tempfile.TemporaryDirectory(prefix="gridwise-v0312-preview-") as temporary:
        root = tk.Tk()
        app = EnergyDesktop(root, runs_root=Path(temporary) / "runs",
                            config_path=Path(temporary) / "desktop.json")
        root.geometry("1180x940")
        app._check()
        wait(root, lambda: app._active_kind is None)
        app._run()
        wait(root, lambda: app._active_kind is None)
        app.optimization_mode.set("peak_cap")
        app.peak_cap.set("55")
        app._run()
        wait(root, lambda: app._active_kind is None)
        assert app.chart is not None
        assert "+25.89 CNY" in app.policy_result.get()
        root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify-restart":
        verify_restart(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 2 and sys.argv[1] == "--show-peak":
        show_peak_result()
    elif len(sys.argv) == 1:
        main()
    else:
        raise SystemExit("usage: smoke_desktop_verified.py [--show-peak]")
