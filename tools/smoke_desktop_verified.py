"""Visible Windows Tk smoke test using only the bundled synthetic CSV."""

import hashlib
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


def main():
    before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="gridwise-v038-gui-") as temporary:
        root = tk.Tk()
        app = EnergyDesktop(root, runs_root=Path(temporary) / "runs")
        try:
            root.deiconify()
            root.update()
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
    print("GUI_SMOKE_PASSED")


if __name__ == "__main__":
    main()
