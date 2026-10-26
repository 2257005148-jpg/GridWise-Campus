"""Bounded, read-only Tk startup/focus probe; run with the project's Python."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path


MAX_CHILD_JSON_BYTES = 64 * 1024
MAX_CHILD_STDERR_BYTES = 8 * 1024
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
CHILD_TIMEOUT_SECONDS = 45


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

    report = {"python": sys.version, "executable": sys.executable,
              "tk_version": str(tk.TkVersion), "tcl_version": str(tk.TclVersion),
              "tcl_library": None, "auto_path": None,
              "mode": mode, "count_requested": count, "count_completed": 0,
              "status": "FAIL"}
    candidate = Path(sys.base_prefix) / "tcl" / "tk8.6"
    report["files_before"] = _files(candidate)
    try:
        interpreter = tk.Tcl()
        report["tcl_library"] = interpreter.eval("info library")
        report["auto_path"] = interpreter.eval("set auto_path")
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


def _run_child_bounded(command: list[str]) -> tuple[int, str, str, str | None]:
    """Capture only bounded bytes from this probe's own child process."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = bytearray(), bytearray()
    exceeded = threading.Event()

    def drain(stream, buffer, limit):
        try:
            while chunk := stream.read(4096):
                available = limit - len(buffer)
                if len(chunk) > available:
                    buffer.extend(chunk[:available])
                    exceeded.set()
                    return
                buffer.extend(chunk)
        finally:
            stream.close()

    readers = (threading.Thread(target=drain, args=(process.stdout, stdout, MAX_CHILD_JSON_BYTES),
                                daemon=True),
               threading.Thread(target=drain, args=(process.stderr, stderr, MAX_CHILD_STDERR_BYTES),
                                daemon=True))
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
    timed_out = False
    try:
        while process.poll() is None and not exceeded.is_set():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.02)
        if process.poll() is None and (timed_out or exceeded.is_set()):
            process.kill()  # Only the child created by this diagnostic call.
        process.wait(timeout=5)
        for reader in readers:
            reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            exceeded.set()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    reason = "probe timeout" if timed_out else "child output exceeded limit" if exceeded.is_set() else None
    return (process.returncode, stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"), reason)


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
        rendered = json.dumps(result, ensure_ascii=False)
        if len(rendered.encode("utf-8")) > MAX_CHILD_JSON_BYTES:
            print(json.dumps({"status": "FAIL", "count_completed": result["count_completed"],
                              "traceback": "diagnostic JSON exceeded 64 KiB; evidence truncated"}))
            return 1
        print(rendered)
        return 0 if result["status"] == "PASS" else 1

    runs = []
    for index in range(args.repeat):
        command = [sys.executable, str(Path(__file__).resolve()), "--child", "--mode",
                   args.mode, "--count", str(args.count)]
        try:
            exit_code, stdout, stderr, failure = _run_child_bounded(command)
        except OSError as exc:
            child = {"status": "FAIL", "exit_code": None,
                     "traceback": f"child launch failed: {exc}", "stderr": ""}
        else:
            try:
                if failure:
                    raise ValueError(failure)
                child = json.loads(stdout)
            except (json.JSONDecodeError, ValueError) as exc:
                child = {"status": "FAIL", "traceback": f"invalid probe output: {exc}",
                         "stdout": stdout[:1024]}
            child["exit_code"] = exit_code
            child["stderr"] = stderr
            if exit_code and child["status"] == "PASS":
                child["status"] = "FAIL"
        child["run"] = index + 1
        runs.append(child)
    output = {"runs": runs, "passed": sum(run["status"] == "PASS" for run in runs),
              "failed": sum(run["status"] != "PASS" for run in runs)}
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if len(rendered.encode("utf-8")) > MAX_EVIDENCE_BYTES:
        parser.exit(2, "diagnostic evidence exceeds 2 MiB; no file written\n")
    if args.output:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
    print(rendered)
    return 0 if output["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
