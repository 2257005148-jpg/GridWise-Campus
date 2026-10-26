"""Command line export for the offline dispatch example."""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from html import escape

from .core import InputError, SolverError, parse_scenario, solve
from .environment import run_environment


MODEL_FAMILY = "cost-mip-v1"
PEAK_MODEL_FAMILY = "peak-cap-mip-v1"
EXPORT_NAMES = ("summary.json", "optimized.csv", "baseline.csv", "grid-purchase.svg")


class ExportRollbackError(OSError):
    """A failed rollback left recovery files in the named staging directory."""


class ExportCleanupError(OSError):
    """Publication state is known, but its staging directory could not be removed."""


def _check_destination(input_path: Path, output_dir: Path, overwrite: bool) -> None:
    """Reject path aliases and unsafe targets before creating any export."""
    for path in (output_dir, *output_dir.parents):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise OSError(f"output path is a link or junction: {path}")
    if output_dir.exists() and not output_dir.is_dir():
        raise OSError(f"output path is not a directory: {output_dir}")
    for name in EXPORT_NAMES:
        target = output_dir / name
        if not os.path.lexists(target):
            continue
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise OSError(f"export target is a link or junction: {target}")
        if not target.is_file():
            raise OSError(f"export target is not a regular file: {target}")
        if os.path.samefile(input_path, target):
            raise OSError(f"export target aliases the input file: {target}")
        if not overwrite:
            raise FileExistsError(f"export already exists (use --overwrite to replace): {target}")


def _publish(stage: Path, output_dir: Path, input_path: Path, overwrite: bool) -> None:
    """Publish staged files, retaining old bytes if a normal write fails."""
    created = []
    replaced = []
    try:
        for name in EXPORT_NAMES:
            target = output_dir / name
            staged = stage / name
            if overwrite and os.path.lexists(target):
                # Refuse a target changed between preflight and publication.
                if target.is_symlink() or not target.is_file():
                    raise OSError(f"export target changed during publication: {target}")
                if os.path.samefile(input_path, target):
                    raise OSError(f"export target aliases the input file: {target}")
                backup = stage / f"backup-{name}"
                os.link(target, backup)
                if not os.path.samefile(target, backup):
                    raise OSError(f"export target changed during publication: {target}")
                os.replace(staged, target)
                replaced.append((target, backup))
            else:
                # Atomic no-clobber creation closes the preflight/write race.
                os.link(staged, target)
                created.append((target, staged))
    except BaseException as original:
        # Roll back even on KeyboardInterrupt or a non-I/O publishing error;
        # process termination cannot be caught and remains a documented limit.
        rollback_errors = []
        for target, staged in reversed(created):
            try:
                if target.exists() and os.path.samefile(target, staged):
                    target.unlink()
            except OSError as exc:
                rollback_errors.append(f"{target}: {exc}")
        for target, backup in reversed(replaced):
            try:
                os.replace(backup, target)
            except OSError as exc:
                rollback_errors.append(f"{target}: {exc}")
        if rollback_errors:
            raise ExportRollbackError(
                f"publication failed: {original}; rollback incomplete: "
                f"{'; '.join(rollback_errors)}; recovery files: {stage}") from original
        raise


def model_version(family: str = MODEL_FAMILY) -> str:
    source = Path(__file__).with_name("core.py").read_bytes()
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return f"{family}+sha256.{hashlib.sha256(normalized).hexdigest()}"


def git_provenance() -> tuple[str, str]:
    root = Path(__file__).resolve().parents[2]
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=5, check=True).stdout.strip()
        if Path(top).resolve() != root:
            return "unavailable", "unavailable"
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=5, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                 "--untracked-files=all", "--", "src", "tools", "requirements", "pyproject.toml"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=5, check=True).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unavailable", "unavailable"
    return head, "dirty" if status else "clean"


def _chart(results: dict) -> str:
    rows = results["optimized"]["rows"]
    baseline = results["baseline"]["rows"]
    top = max([r["grid_kw"] for r in rows + baseline] + [1]) * 1.1
    width, height = 760, 320
    x = lambda i: 65 + i * 645 / max(1, len(rows) - 1)
    y = lambda v: 255 - v * 205 / top
    def line(items, color):
        points = " ".join(f"{x(i):.1f},{y(r['grid_kw']):.1f}" for i, r in enumerate(items))
        return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3"/>'
    labels = "".join(f'<text x="{x(i):.1f}" y="280" text-anchor="middle" font-size="12">{escape(r["timestamp"][11:16])}</text>' for i, r in enumerate(rows))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Grid purchase comparison in kW">'
            '<rect width="100%" height="100%" fill="white"/><text x="65" y="28" font-size="19">Grid purchase / 电网购电 (kW)</text>'
            '<path d="M65 50 V255 H710" stroke="#475569" fill="none"/>'
            f'{line(baseline, "#64748b")}{line(rows, "#0f766e")}{labels}'
            '<text x="70" y="305" fill="#64748b" font-size="13">Baseline / 无储能</text>'
            '<text x="270" y="305" fill="#0f766e" font-size="13">Optimized / 储能优化</text></svg>')


def run(input_path: Path, output_dir: Path, solver_name="appsi_highs", *,
        overwrite=False, peak_cap_kw=None) -> dict:
    input_path = Path(input_path)
    output_dir = Path(os.path.abspath(output_dir))
    _check_destination(input_path, output_dir, overwrite)
    source_bytes = input_path.read_bytes()
    scenario = parse_scenario(json.loads(source_bytes.decode("utf-8-sig")))
    scenario_sha256 = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    git_commit, source_state = git_provenance()
    optimized = solve(scenario, solver_name=solver_name, peak_cap_kw=peak_cap_kw)
    baseline = solve(scenario, solver_name=solver_name, with_battery=False)
    run_metadata = {"model_version": model_version(
                        PEAK_MODEL_FAMILY if peak_cap_kw is not None else MODEL_FAMILY),
                    "git_commit": git_commit,
                       "source_state": source_state, "scenario_sha256": scenario_sha256,
                       "solver_name": solver_name, "status": "optimal", **run_environment()}
    if peak_cap_kw is not None:
        run_metadata.update(optimization_mode="peak_cap", peak_cap_kw=float(peak_cap_kw))
    results = {"run": run_metadata,
               "optimized": optimized, "baseline": baseline}
    _check_destination(input_path, output_dir, overwrite)
    output_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".gridwise-stage-", dir=output_dir))
    preserve_stage = False
    publication_complete = False
    primary_error = None
    try:
        (stage / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in ("optimized", "baseline"):
            result = results[name]
            with (stage / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=result["rows"][0].keys())
                writer.writeheader()
                writer.writerows(result["rows"])
        (stage / "grid-purchase.svg").write_text(_chart(results), encoding="utf-8")
        _check_destination(input_path, output_dir, overwrite)
        _publish(stage, output_dir, input_path, overwrite)
        publication_complete = True
    except BaseException as exc:
        primary_error = exc
        preserve_stage = isinstance(exc, ExportRollbackError)
        raise
    finally:
        if not preserve_stage:
            try:
                shutil.rmtree(stage)
            except OSError as exc:
                state = "publication completed" if publication_complete else "publication not completed"
                prior = f"; prior error: {primary_error}" if primary_error else ""
                raise ExportCleanupError(
                    f"staging cleanup failed after {state}{prior}; retained files: {stage}") from exc
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline microgrid dispatch / 校园微电网离线调度")
    parser.add_argument("input", type=Path, help="scenario JSON / 场景 JSON")
    parser.add_argument("output", type=Path, help="output directory / 输出目录")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace existing export files / 显式替换已有导出文件")
    parser.add_argument("--peak-cap-kw", type=float,
                        help="optional per-period grid import cap in kW for storage optimization / 储能方案逐时购电上限")
    args = parser.parse_args()
    try:
        results = run(args.input, args.output, overwrite=args.overwrite,
                      peak_cap_kw=args.peak_cap_kw)
    except (InputError, SolverError, OSError, ValueError) as exc:
        parser.exit(2, f"gridwise: {exc}\n")
    for name in ("optimized", "baseline"):
        result = results[name]
        print(f"{name}: cost={result['total_cost_cny']:.2f} CNY; grid={result['grid_energy_kwh']:.2f} kWh; peak={result['peak_grid_kw']:.2f} kW; curtailment={result['pv_curtailed_kwh']:.2f} kWh")


if __name__ == "__main__":
    main()
