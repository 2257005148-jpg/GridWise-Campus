"""Bounded, read-only Tk startup/focus probe; run with the project's Python."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from pathlib import Path


def _files(directory: Path) -> dict:
    result = {}
    for relative in ("tk.tcl", "icons.tcl", "ttk/ttk.tcl", "ttk/scrollbar.tcl"):
        path = directory / relative
        try:
            with path.open("rb") as stream:
                stream.read(1)
            readable = True
            error = None
        except OSError as exc:
            readable = False
            error = f"{type(exc).__name__}: {exc}"
        result[relative] = {"path": str(path), "exists": path.exists(),
                            "readable": readable, "error": error}
    return result


def probe(mode: str, count: int) -> dict:
    import tkinter as tk
    from tkinter import ttk

    interpreter = tk.Tcl()
    report = {"python": sys.version, "executable": sys.executable,
              "tk_version": str(tk.TkVersion), "tcl_version": str(tk.TclVersion),
              "tcl_library": interpreter.eval("info library"),
              "auto_path": interpreter.eval("set auto_path"),
              "mode": mode, "count_requested": count, "count_completed": 0,
              "status": "FAIL"}
    candidate = Path(sys.base_prefix) / "tcl" / "tk8.6"
    report["files_before"] = _files(candidate)
    try:
        if mode == "toplevel":
            root = tk.Tk()
            root.withdraw()
            try:
                report["tk_library"] = root.tk.eval("set tk_library")
                for _ in range(count):
                    window = tk.Toplevel(root)
                    try:
                        first = ttk.Button(window, text="First")
                        second = ttk.Button(window, text="Second")
                        first.pack()
                        second.pack()
                        window.update()
                        assert first.tk_focusNext() == second
                        assert root.tk.call("info", "commands", "tk_focusNext")
                        report["count_completed"] += 1
                    finally:
                        window.destroy()
            finally:
                root.destroy()
        else:
            for _ in range(count):
                root = tk.Tk()
                try:
                    report["tk_library"] = root.tk.eval("set tk_library")
                    first = ttk.Button(root, text="First")
                    second = ttk.Button(root, text="Second")
                    first.pack()
                    second.pack()
                    root.update()
                    assert first.tk_focusNext() == second
                    assert root.tk.call("info", "commands", "tk_focusNext")
                    report["count_completed"] += 1
                finally:
                    root.destroy()
        report["status"] = "PASS"
    except Exception:
        report["traceback"] = traceback.format_exc()
    report["files_after"] = _files(Path(report.get("tk_library", candidate)))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Tk runtime files and focus traversal")
    parser.add_argument("--mode", choices=("toplevel", "root"), default="toplevel")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--repeat", type=int, default=1,
                        help="independent child processes, maximum 20")
    parser.add_argument("--output", type=Path,
                        help="create a new JSON evidence file; never overwrite")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.count <= 100 or not 1 <= args.repeat <= 20:
        parser.error("count must be 1..100 and repeat must be 1..20")
    if args.child:
        result = probe(args.mode, args.count)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "PASS" else 1

    runs = []
    for index in range(args.repeat):
        command = [sys.executable, str(Path(__file__).resolve()), "--child", "--mode",
                   args.mode, "--count", str(args.count)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=45)
        except subprocess.TimeoutExpired as exc:
            child = {"status": "FAIL", "exit_code": None,
                     "traceback": f"probe timeout: {exc}",
                     "stdout": str(exc.stdout), "stderr": str(exc.stderr)}
        else:
            try:
                child = json.loads(completed.stdout)
            except json.JSONDecodeError as exc:
                child = {"status": "FAIL", "traceback": f"invalid probe output: {exc}",
                         "stdout": completed.stdout}
            child["exit_code"] = completed.returncode
            child["stderr"] = completed.stderr
            if completed.returncode and child["status"] == "PASS":
                child["status"] = "FAIL"
        child["run"] = index + 1
        runs.append(child)
    output = {"runs": runs, "passed": sum(run["status"] == "PASS" for run in runs),
              "failed": sum(run["status"] != "PASS" for run in runs)}
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    return 0 if output["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
