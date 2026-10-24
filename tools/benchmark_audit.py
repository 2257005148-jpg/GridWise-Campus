"""Generate bounded synthetic exports and measure the public audit CLI.

Schedules are arithmetic fixtures, not solver outputs; run metadata is a
fixture value used only to exercise the current export schema.
Windows peak memory is the child process PeakWorkingSetSize reported by the OS.
On other systems peak_working_set_bytes is null; no substitute is claimed.
The requested output directory must not exist and is retained for inspection.
Every timing includes subprocess launch, audit, and output collection; process
startup is not separately measured. Repeated runs report min, median, and max.
"""

import argparse
import csv
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from audit_exports import git_provenance, model_version


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "tools" / "audit_exports.py"
PYTHON = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
FIELDS = ("timestamp", "load_kw", "pv_available_kw", "pv_used_kw", "pv_curtailed_kw",
          "grid_kw", "charge_kw", "discharge_kw", "soc_kwh", "price_cny_per_kwh")
FILE_LIMIT = 32 * 1024 * 1024


def peak_working_set(process):
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    api.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    api.restype = wintypes.BOOL
    if api(process._handle, ctypes.byref(counters), counters.cb):
        return counters.PeakWorkingSetSize
    return None


def make_valid(directory, periods, *, pad_summary_to=None):
    directory.mkdir()
    first = datetime.fromisoformat("2026-01-01T08:00:00+08:00")
    stamps = [(first + timedelta(minutes=15 * i)).isoformat() for i in range(periods)]
    scenario = {
        "power_unit": "kW", "energy_unit": "kWh", "price_unit": "CNY/kWh",
        "step_hours": 0.25, "grid_max_kw": 120,
        "battery": {"capacity_kwh": 80, "initial_kwh": 40, "terminal_kwh": 40,
                    "max_charge_kw": 30, "max_discharge_kw": 30,
                    "charge_efficiency": 0.95, "discharge_efficiency": 0.95},
        "timeseries": [{"timestamp": stamp, "load_kw": 0, "pv_kw": 0,
                        "price_cny_per_kwh": 0} for stamp in stamps],
    }
    source = json.dumps(scenario, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (directory / "scenario.json").write_bytes(source)
    rows = [{"timestamp": stamp, "load_kw": 0, "pv_available_kw": 0, "pv_used_kw": 0,
             "pv_curtailed_kw": 0, "grid_kw": 0, "charge_kw": 0, "discharge_kw": 0,
             "soc_kwh": 40, "price_cny_per_kwh": 0} for stamp in stamps]
    git_commit, source_state = git_provenance()
    summary = {"run": {"model_version": model_version(), "git_commit": git_commit,
                       "source_state": source_state,
                       "scenario_sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
                       "solver_name": "appsi_highs", "status": "optimal"}}
    for kind, enabled in (("optimized", True), ("baseline", False)):
        summary[kind] = {"status": "optimal", "with_battery": enabled, "rows": rows,
                         "total_cost_cny": 0, "grid_energy_kwh": 0,
                         "peak_grid_kw": 0, "pv_curtailed_kwh": 0}
        with (directory / f"{kind}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    summary_bytes = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if pad_summary_to is not None:
        if len(summary_bytes) > pad_summary_to:
            raise ValueError("synthetic summary already exceeds requested padding target")
        summary_bytes += b" " * (pad_summary_to - len(summary_bytes))
    (directory / "summary.json").write_bytes(summary_bytes)
    return scenario


def measure(name, paths, expected_exit):
    command = [PYTHON, str(AUDITOR), *(str(path) for path in paths)]
    start = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    peak = None
    while process.poll() is None:
        observed = peak_working_set(process)
        if observed is not None:
            peak = max(peak or 0, observed)
        time.sleep(0.01)
    observed = peak_working_set(process)
    if observed is not None:
        peak = max(peak or 0, observed)
    stdout, stderr = process.communicate()
    return {"case": name, "elapsed_seconds_end_to_end": round(time.perf_counter() - start, 6),
            "interpreter": PYTHON,
            "peak_working_set_bytes": peak, "exit_code": process.returncode,
            "expected_exit_code": expected_exit, "file_bytes": [path.stat().st_size for path in paths],
            "diagnostic": stderr.strip().splitlines()[:1], "passed": process.returncode == expected_exit,
            "success_marker": "AUDIT PASSED" in stdout}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--repeats", type=int, default=3, help="2-10 measurements per case")
    args = parser.parse_args()
    if not 2 <= args.repeats <= 10:
        parser.error("--repeats must be between 2 and 10")
    if args.output_dir.exists():
        parser.error("output directory already exists; refusing to overwrite")
    args.output_dir.mkdir(parents=True)
    small = args.output_dir / "small"
    near = args.output_dir / "near"
    make_valid(small, 6)
    near_scenario = make_valid(near, 50_000, pad_summary_to=FILE_LIMIT - 4096)
    small_paths = [small / name for name in ("scenario.json", "optimized.csv", "baseline.csv", "summary.json")]
    near_paths = [near / name for name in ("scenario.json", "optimized.csv", "baseline.csv", "summary.json")]
    over_periods = args.output_dir / "over-periods.json"
    near_scenario["timeseries"].append(near_scenario["timeseries"][-1])
    over_periods.write_text(json.dumps(near_scenario, separators=(",", ":")), encoding="utf-8")
    over_file = args.output_dir / "over-file.json"
    with over_file.open("wb") as stream:
        stream.seek(FILE_LIMIT)
        stream.write(b"x")
    over_line = args.output_dir / "over-line.csv"
    over_line.write_bytes(b"a" * (64 * 1024 + 1) + b"\n")
    cases = [
        ("small_valid", small_paths, 0),
        ("near_50000_rows_and_32mib_file", near_paths, 0),
        ("over_50000_rows", [over_periods, *near_paths[1:]], 2),
        ("over_32mib_file", [*small_paths[:3], over_file], 2),
        ("over_64kib_line", [small_paths[0], over_line, *small_paths[2:]], 2),
    ]
    results = []
    for name, paths, expected_exit in cases:
        runs = [measure(name, paths, expected_exit) for _ in range(args.repeats)]
        times = [run["elapsed_seconds_end_to_end"] for run in runs]
        memories = [run["peak_working_set_bytes"] for run in runs]
        results.append({"case": name, "run_count": args.repeats, "expected_exit_code": expected_exit,
                        "elapsed_seconds_end_to_end": {"median": statistics.median(times),
                                                       "min": min(times), "max": max(times)},
                        "peak_working_set_bytes": {"median": statistics.median(memories),
                                                   "min": min(memories), "max": max(memories)}
                        if all(value is not None for value in memories) else None,
                        "runs": runs,
                        "passed": all(run["passed"] and
                                      run["success_marker"] == (expected_exit == 0) for run in runs)})
    (args.output_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
