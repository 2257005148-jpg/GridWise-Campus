"""Command line export for the offline dispatch example."""

import argparse
import csv
import hashlib
import json
import subprocess
from pathlib import Path
from html import escape

from .core import InputError, SolverError, parse_scenario, solve
from .environment import run_environment


MODEL_FAMILY = "cost-mip-v1"


def model_version() -> str:
    source = Path(__file__).with_name("core.py").read_bytes()
    normalized = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return f"{MODEL_FAMILY}+sha256.{hashlib.sha256(normalized).hexdigest()}"


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


def run(input_path: Path, output_dir: Path, solver_name="appsi_highs") -> dict:
    source_bytes = input_path.read_bytes()
    scenario = parse_scenario(json.loads(source_bytes.decode("utf-8-sig")))
    scenario_sha256 = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
    git_commit, source_state = git_provenance()
    optimized = solve(scenario, solver_name=solver_name)
    baseline = solve(scenario, solver_name=solver_name, with_battery=False)
    results = {"run": {"model_version": model_version(), "git_commit": git_commit,
                       "source_state": source_state, "scenario_sha256": scenario_sha256,
                       "solver_name": solver_name, "status": "optimal", **run_environment()},
               "optimized": optimized, "baseline": baseline}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in ("optimized", "baseline"):
        result = results[name]
        with (output_dir / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=result["rows"][0].keys())
            writer.writeheader()
            writer.writerows(result["rows"])
    (output_dir / "grid-purchase.svg").write_text(_chart(results), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline microgrid dispatch / 校园微电网离线调度")
    parser.add_argument("input", type=Path, help="scenario JSON / 场景 JSON")
    parser.add_argument("output", type=Path, help="output directory / 输出目录")
    args = parser.parse_args()
    try:
        results = run(args.input, args.output)
    except (InputError, SolverError, OSError, ValueError) as exc:
        parser.exit(2, f"gridwise: {exc}\n")
    for name in ("optimized", "baseline"):
        result = results[name]
        print(f"{name}: cost={result['total_cost_cny']:.2f} CNY; grid={result['grid_energy_kwh']:.2f} kWh; peak={result['peak_grid_kw']:.2f} kW; curtailment={result['pv_curtailed_kwh']:.2f} kWh")


if __name__ == "__main__":
    main()
