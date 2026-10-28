"""Self-contained HTML reports from already verified dashboard results."""

from __future__ import annotations

import math
import os
import threading
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .dashboard import (DashboardError, HistoricalRunComparison, compare_verified_runs,
                        load_verified_comparison)
from .data import DataError, ImportResult, summarize_energy_input


MAX_REPORT_BYTES = 4 * 1024 * 1024


class ReportError(RuntimeError):
    """A complete, reliable report could not be generated or published."""


class ReportCancellation(threading.Event):
    """Serialize cancellation with the irreversible report publication step."""

    def __init__(self):
        super().__init__()
        self._publication_lock = threading.Lock()
        self._publication_committed = False

    def set(self) -> bool:
        """Accept cancellation unless the final publication already committed."""
        with self._publication_lock:
            if self._publication_committed:
                return False
            super().set()
            return True

    def commit(self, operation):
        """Run one final publication only when cancellation did not win the lock."""
        with self._publication_lock:
            if self.is_set():
                raise ReportError("report export cancelled")
            result = operation()
            self._publication_committed = True
            return result


def _number(value, where: str) -> float:
    if isinstance(value, bool):
        raise ReportError(f"{where}: expected finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReportError(f"{where}: expected finite number") from exc
    if not math.isfinite(result):
        raise ReportError(f"{where}: expected finite number")
    return result


def _generated_at(value: datetime | None) -> str:
    stamp = value or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ReportError("generated_at must be a timezone-aware datetime")
    return stamp.isoformat()


def _input_context(imported: ImportResult) -> dict:
    try:
        summary = summarize_energy_input(imported)
    except DataError as exc:
        raise ReportError(f"input is not ready for reporting: {exc}") from exc
    return {
        "source": imported.report.source,
        "start": summary["start_timestamp"],
        "end": summary["end_timestamp"],
        "rows": summary["row_count"],
        "step": _number(summary["step_hours"], "input.step_hours"),
    }


def _mode(mode: str, cap) -> str:
    if mode == "cost" and cap is None:
        return "纯电费优化 / Cost-only"
    if mode == "peak_cap":
        return f"峰值上限优化 / Peak cap ({_number(cap, 'peak_cap_kw'):.2f} kW)"
    raise ReportError(f"unsupported optimization mode: {mode!r}")


def _metric_map(value, where: str) -> dict[str, float]:
    if isinstance(value, dict):
        getter = value.__getitem__
    else:
        getter = lambda name: getattr(value, name)
    try:
        return {name: _number(getter(name), f"{where}.{name}") for name in
                ("cost_cny", "grid_energy_kwh", "peak_grid_kw")}
    except (KeyError, AttributeError) as exc:
        raise ReportError(f"{where}: missing metric {exc}") from exc


def _device_rows(parameters) -> str:
    if parameters is None:
        raise ReportError("device_parameters: missing")
    fields = (
        ("电网购电上限 / Grid maximum", "grid_max_kw", "kW"),
        ("电池容量 / Battery capacity", "capacity_kwh", "kWh"),
        ("最大充电功率 / Maximum charge", "max_charge_kw", "kW"),
        ("最大放电功率 / Maximum discharge", "max_discharge_kw", "kW"),
        ("初始 SOC / Initial SOC", "initial_kwh", "kWh"),
        ("终端 SOC / Terminal SOC", "terminal_kwh", "kWh"),
        ("充电效率 / Charge efficiency", "charge_efficiency", "p.u."),
        ("放电效率 / Discharge efficiency", "discharge_efficiency", "p.u."),
    )
    rows = []
    for label, field, unit in fields:
        try:
            value = _number(getattr(parameters, field), f"device_parameters.{field}")
        except AttributeError as exc:
            raise ReportError(f"device_parameters: missing {field}") from exc
        rows.append(f"<tr><th>{escape(label)}</th><td>{value:.2f} {escape(unit)}</td></tr>")
    return "".join(rows)


def _cards(metrics: dict[str, float], *, prefix: str = "") -> str:
    definitions = (
        ("cost_cny", "电费 / Cost", "CNY"),
        ("grid_energy_kwh", "购电量 / Grid energy", "kWh"),
        ("peak_grid_kw", "购电峰值 / Grid peak", "kW"),
    )
    return "".join(
        f'<div class="metric"><span>{escape(prefix + label)}</span>'
        f'<strong>{metrics[key]:,.2f}</strong><small>{unit}</small></div>'
        for key, label, unit in definitions)


def _comparison_rows(run_a: dict[str, float], run_b: dict[str, float],
                     delta: dict[str, float]) -> str:
    definitions = (
        ("cost_cny", "电费 / Cost", "CNY"),
        ("grid_energy_kwh", "购电量 / Grid energy", "kWh"),
        ("peak_grid_kw", "购电峰值 / Grid peak", "kW"),
    )
    return "".join(
        f"<tr><th>{escape(label)}</th><td>{run_a[key]:,.2f} {unit}</td>"
        f"<td>{run_b[key]:,.2f} {unit}</td>"
        f'<td class="delta">{delta[key]:+,.2f} {unit}</td></tr>'
        for key, label, unit in definitions)


def _document(title: str, generated: str, context: dict, body: str) -> bytes:
    source = escape(str(context["source"]))
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
:root {{--ink:#16324a;--muted:#587184;--paper:#f5f8f8;--white:#fff;--teal:#087e83;--gold:#b07820;--line:#cfdadd;}}
* {{box-sizing:border-box}} body {{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 "Microsoft YaHei UI","Segoe UI",sans-serif}}
main {{max-width:980px;margin:0 auto;padding:40px 28px 56px}} header {{border-top:8px solid var(--teal);padding:26px 0 20px;border-bottom:1px solid var(--line)}}
h1 {{margin:0 0 8px;font-size:30px;line-height:1.2}} h2 {{margin:30px 0 12px;font-size:19px}} p {{max-width:78ch}} .meta {{color:var(--muted);margin:4px 0}}
.status {{display:inline-block;border:1px solid var(--teal);color:var(--teal);font-weight:700;padding:5px 10px;margin:8px 8px 0 0}}
.metrics {{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}} .metric {{background:var(--white);border-top:4px solid var(--teal);padding:16px}}
.metric span,.metric small {{display:block;color:var(--muted)}} .metric strong {{display:block;font-size:27px;margin-top:8px}} table {{width:100%;border-collapse:collapse;background:var(--white)}} th,td {{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left}} th {{font-weight:600}} td {{text-align:right}} .delta {{color:var(--gold);font-weight:700}}
.notice {{border-left:4px solid var(--gold);background:#fffaf0;padding:12px 16px}} code {{font-family:Consolas,monospace;font-size:.92em}} footer {{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted)}}
@media (max-width:720px) {{.metrics {{grid-template-columns:1fr}} main {{padding:24px 16px}} table {{font-size:13px}} th,td {{padding:8px}}}}
</style>
</head>
<body><main>
<header><h1>{escape(title)}</h1><p class="meta">生成时间 / Generated: {escape(generated)}</p></header>
<section><h2>输入上下文 / Input context</h2>
<p class="meta">来源 / Source: {source}</p>
<p class="meta">时间 / Time: {escape(str(context['start']))} → {escape(str(context['end']))}</p>
<p class="meta">时段 / Periods: {context['rows']} · 步长 / Step: {context['step']:g} h</p></section>
{body}
<section><h2>计算口径与限制 / Definitions and limits</h2>
<p><code>period_cost_cny = grid_kw × price_cny_per_kwh × step_hours</code><br>
<code>grid_energy_kwh = Σ(grid_kw × step_hours)</code><br>
<code>peak_grid_kw = max(grid_kw)</code></p>
<p class="notice">电费、购电量和峰值是独立指标。降低电费不自动表示削峰或节能。文件哈希只能检测内容变化，不能证明输入真实性。本报告不证明真实校园数据、设备控制或外部效果。 / Cost, energy and peak are separate metrics. Hashes detect changes but do not prove source truth.</p></section>
<footer>GridWise Campus · 本地离线合成或用户明确提供的数据 / Local offline analysis only</footer>
</main></body></html>"""
    return html.encode("utf-8")


def build_single_run_html(imported: ImportResult, run_name: str, runs_root: Path, *,
                          generated_at: datetime | None = None,
                          cancel_event=None) -> bytes:
    """Reverify one run, then format its already recalculated dashboard metrics."""
    try:
        result = load_verified_comparison(
            imported, run_name, Path(runs_root), cancel_event=cancel_event)
        context = _input_context(imported)
        optimized = _metric_map(result.chart["metrics"]["optimized"], "optimized")
        baseline = _metric_map(result.chart["metrics"]["baseline"], "baseline")
        delta = _metric_map(
            result.chart["delta_optimized_minus_baseline"],
            "delta_optimized_minus_baseline")
        if not result.run_name or result.run_name != run_name:
            raise ReportError("verified run identifier mismatch")
        body = f"""
<section><h2>验证状态 / Verification</h2><span class="status">VERIFIED</span></section>
<section><h2>运行策略 / Run policy</h2><p>{escape(_mode(result.optimization_mode, result.peak_cap_kw))}</p><p class="meta">运行 / Run: {escape(result.run_name)}</p></section>
<section><h2>优化方案 / Optimized result</h2><div class="metrics">{_cards(optimized)}</div></section>
<section><h2>无储能基线 / No-storage baseline</h2><div class="metrics">{_cards(baseline)}</div></section>
<section><h2>优化 − 基线 / Optimized − baseline</h2><div class="metrics">{_cards(delta)}</div></section>
<section><h2>设备参数 / Equipment parameters</h2><table>{_device_rows(result.device_parameters)}</table></section>"""
        return _document("GridWise Campus 单次运行报告 / Single-run report",
                         _generated_at(generated_at), context, body)
    except ReportError:
        raise
    except (DashboardError, KeyError, TypeError) as exc:
        raise ReportError(f"single-run report unavailable: {exc}") from exc


def build_comparison_html(imported: ImportResult, run_a_name: str, run_b_name: str,
                          runs_root: Path, *, generated_at: datetime | None = None,
                          cancel_event=None) -> bytes:
    """Reverify and fairly compare two runs before formatting their existing metrics."""
    try:
        comparison: HistoricalRunComparison = compare_verified_runs(
            imported, run_a_name, run_b_name, Path(runs_root),
            cancel_event=cancel_event)
        if comparison.integrity_status != "VERIFIED" or comparison.fairness_status != "MATCHED":
            raise ReportError("comparison status must be VERIFIED and MATCHED")
        context = _input_context(imported)
        metrics_a = _metric_map(comparison.run_a.metrics, "run_a.metrics")
        metrics_b = _metric_map(comparison.run_b.metrics, "run_b.metrics")
        delta = _metric_map(comparison.delta_b_minus_a, "delta_b_minus_a")
        body = f"""
<section><h2>验证状态 / Verification</h2><span class="status">{escape(comparison.integrity_status)}</span><span class="status">{escape(comparison.fairness_status)}</span></section>
<section><h2>策略 / Policies</h2><p><strong>A:</strong> {escape(_mode(comparison.run_a.optimization_mode, comparison.run_a.peak_cap_kw))}<br><strong>B:</strong> {escape(_mode(comparison.run_b.optimization_mode, comparison.run_b.peak_cap_kw))}</p><p class="meta">A: {escape(comparison.run_a.name)}<br>B: {escape(comparison.run_b.name)}</p></section>
<section><h2>指标对比 / Metric comparison</h2><table><thead><tr><th>指标 / Metric</th><th>运行 A</th><th>运行 B</th><th>B − A</th></tr></thead><tbody>{_comparison_rows(metrics_a, metrics_b, delta)}</tbody></table></section>
<section><h2>运行 A 设备参数 / Run A equipment</h2><table>{_device_rows(comparison.run_a.device_parameters)}</table></section>
<section><h2>运行 B 设备参数 / Run B equipment</h2><table>{_device_rows(comparison.run_b.device_parameters)}</table></section>"""
        return _document("GridWise Campus A/B 公平对比报告 / Fair comparison report",
                         _generated_at(generated_at), context, body)
    except ReportError:
        raise
    except (DashboardError, KeyError, TypeError) as exc:
        raise ReportError(f"comparison report unavailable: {exc}") from exc


def _reject_link_path(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ReportError(f"link or junction path refused: {item}")


def publish_html_report(target: Path, payload: bytes, *, overwrite: bool = False,
                        cancel_event=None) -> Path:
    """Publish one complete HTML file through a private same-directory temporary."""
    path = Path(target)
    if not path.is_absolute():
        raise ReportError(f"report target must be absolute: {path}")
    if path.suffix.lower() != ".html":
        raise ReportError(f"report target must use .html: {path}")
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_REPORT_BYTES:
        raise ReportError(f"report payload must be 1..{MAX_REPORT_BYTES} bytes")
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportError("report payload must be UTF-8") from exc
    if not decoded.lstrip().lower().startswith("<!doctype html>") or \
            not decoded.rstrip().lower().endswith("</html>"):
        raise ReportError("report payload is not a complete HTML document")

    _cancelled(cancel_event)
    _reject_link_path(path)
    parent = path.parent
    if not parent.is_dir():
        raise ReportError(f"report parent directory is unavailable: {parent}")
    exists = os.path.lexists(path)
    if exists:
        if not path.is_file() or path.is_symlink() or \
                (hasattr(path, "is_junction") and path.is_junction()):
            raise ReportError(f"report target is not a regular file: {path}")
        if not overwrite:
            raise ReportError(f"report target already exists: {path}")

    temporary = parent / f".gridwise-report-{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        def replace():
            if not overwrite and os.path.lexists(path):
                raise ReportError(f"report target appeared before publication: {path}")
            os.replace(temporary, path)
            return path

        if isinstance(cancel_event, ReportCancellation):
            return cancel_event.commit(replace)
        _cancelled(cancel_event)
        return replace()
    except Exception as exc:
        try:
            if os.path.lexists(temporary):
                temporary.unlink()
        except Exception as cleanup_error:
            residual = temporary.resolve(strict=False)
            raise ReportError(
                f"report export failed: {exc}; cleanup failed: {cleanup_error}; "
                f"residual temporary file: {residual}") from exc
        if isinstance(exc, ReportError):
            raise
        raise ReportError(f"report export failed: {exc}") from exc


def _cancelled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ReportError("report export cancelled")


def export_single_run_report(target: Path, imported: ImportResult, run_name: str,
                             runs_root: Path, *, overwrite: bool = False,
                             generated_at: datetime | None = None,
                             cancel_event=None) -> Path:
    """Reverify, render and atomically publish one run report."""
    _cancelled(cancel_event)
    payload = build_single_run_html(
        imported, run_name, runs_root, generated_at=generated_at,
        cancel_event=cancel_event)
    _cancelled(cancel_event)
    return publish_html_report(
        Path(target), payload, overwrite=overwrite, cancel_event=cancel_event)


def export_comparison_report(target: Path, imported: ImportResult,
                             run_a_name: str, run_b_name: str, runs_root: Path, *,
                             overwrite: bool = False,
                             generated_at: datetime | None = None,
                             cancel_event=None) -> Path:
    """Reverify, compare, render and atomically publish one A/B report."""
    _cancelled(cancel_event)
    payload = build_comparison_html(
        imported, run_a_name, run_b_name, runs_root,
        generated_at=generated_at, cancel_event=cancel_event)
    _cancelled(cancel_event)
    return publish_html_report(
        Path(target), payload, overwrite=overwrite, cancel_event=cancel_event)
