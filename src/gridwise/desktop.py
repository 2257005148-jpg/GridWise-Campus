"""Local Tkinter dashboard for synthetic or explicitly selected CSV files."""

from __future__ import annotations

from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
except ImportError:  # Pure chart selection remains usable without a GUI runtime.
    tk = None
    filedialog = None
    ttk = None

from .dashboard import DashboardError, MAX_INTERACTIVE_PERIODS, ROOT, run_comparison
from .data import import_energy_csv


INK = "#16324A"
MUTED = "#587184"
PAPER = "#F5F8F8"
WHITE = "#FFFFFF"
TEAL = "#087E83"
BLUE = "#3675A8"
GOLD = "#B07820"
RED = "#AF453C"
SAMPLE = ROOT / "examples" / "synthetic-day.csv"


CHARTS = (
    ("能源总览 / Energy overview", (("load_kw", "负荷 / Load", INK),
                                      ("pv_kw", "光伏 / PV", GOLD)), "kW"),
    ("购电功率 / Grid power", (("optimized_grid_kw", "优化 / Optimized", TEAL),
                                   ("baseline_grid_kw", "基线 / Baseline", MUTED)), "kW"),
    ("逐时电费 / Period cost", (("optimized_period_cost_cny", "优化 / Optimized", TEAL),
                                    ("baseline_period_cost_cny", "基线 / Baseline", MUTED)), "CNY"),
    ("累计电费 / Cumulative cost", (("optimized_cumulative_cost_cny", "优化 / Optimized", TEAL),
                                        ("baseline_cumulative_cost_cny", "基线 / Baseline", MUTED)), "CNY"),
    ("储能状态 / Battery SOC", (("optimized_soc_kwh", "SOC", BLUE),), "kWh"),
)


def chart_values(chart: dict, specification: tuple) -> tuple:
    """Select only validated, precomputed chart series for rendering."""
    _, definitions, unit = specification
    timestamps = chart["timestamps"]
    selected = []
    for key, label, color in definitions:
        values = chart["series"][key]
        if len(values) != len(timestamps):
            raise ValueError(f"{key}: length mismatch")
        selected.append((label, color, values))
    return timestamps, selected, unit


class EnergyDesktop:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("GridWise Campus | 校园能源分析")
        root.geometry("1140x780")
        root.minsize(900, 650)
        root.configure(bg=PAPER)
        self.path = tk.StringVar(value=str(SAMPLE))
        self.step = tk.StringVar(value="1")
        self.status = tk.StringVar(value="请选择 CSV 并检查质量 / Select a CSV and check quality")
        self.imported = None
        self.chart = None
        self.chart_canvases = []
        self._style()
        self._layout()
        self.path.trace_add("write", self._invalidate)
        self.step.trace_add("write", self._invalidate)

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=PAPER)
        style.configure("TLabel", background=PAPER, foreground=INK,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"),
                        foreground=INK)
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 10), foreground=MUTED)
        style.configure("Metric.TLabel", font=("Microsoft YaHei UI", 17, "bold"),
                        foreground=INK)
        style.configure("TButton", padding=(13, 8), font=("Microsoft YaHei UI", 10))
        style.configure("Run.TButton", background=TEAL, foreground=WHITE)
        style.map("Run.TButton", background=[("active", "#06696D"),
                                                ("disabled", "#A8B8B9")])
        style.configure("TNotebook", background=PAPER, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 9),
                        font=("Microsoft YaHei UI", 9))

    def _layout(self):
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="GridWise Campus", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="校园能源调度观察台 / Offline dispatch workspace",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 18))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")
        ttk.Label(controls, text="输入 CSV / Input CSV").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, text="时间步长（小时）/ Step (h)").grid(
            row=0, column=2, sticky="w", padx=(16, 0))
        ttk.Entry(controls, textvariable=self.path).grid(row=1, column=0, sticky="ew", pady=5)
        ttk.Button(controls, text="选择文件 / Browse", command=self._choose).grid(
            row=1, column=1, padx=(8, 0))
        ttk.Entry(controls, textvariable=self.step, width=12).grid(
            row=1, column=2, padx=(16, 0), sticky="w")
        controls.columnconfigure(0, weight=1)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 4))
        ttk.Button(actions, text="检查数据 / Check data", command=self._check).pack(side="left")
        self.run_button = ttk.Button(actions, text="运行对比 / Run comparison",
                                     style="Run.TButton", command=self._run, state="disabled")
        self.run_button.pack(side="left", padx=10)
        ttk.Label(actions, textvariable=self.status, style="Sub.TLabel").pack(
            side="left", padx=12)

        self.quality = tk.Text(outer, height=5, wrap="word", bg=WHITE, fg=INK,
                               relief="flat", padx=12, pady=9,
                               font=("Microsoft YaHei UI", 9))
        self.quality.pack(fill="x", pady=(10, 14))
        self._quality_text("质量报告 / Quality report\n等待检查 / Awaiting check")

        metric_frame = ttk.Frame(outer)
        metric_frame.pack(fill="x", pady=(0, 12))
        self.metric_labels = {}
        for index, (key, caption) in enumerate((
            ("cost_cny", "购电费用 / Cost · CNY"),
            ("grid_energy_kwh", "购电量 / Grid energy · kWh"),
            ("peak_grid_kw", "购电峰值 / Grid peak · kW"),
        )):
            card = tk.Frame(metric_frame, bg=WHITE, padx=14, pady=10)
            card.grid(row=0, column=index, padx=(0, 10), sticky="ew")
            metric_frame.columnconfigure(index, weight=1)
            tk.Label(card, text=caption, bg=WHITE, fg=MUTED,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            tk.Label(card, text="优化 / 基线  Optimized / Baseline", bg=WHITE,
                     fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w")
            label = tk.Label(card, text="—", bg=WHITE, fg=INK,
                             font=("Microsoft YaHei UI", 16, "bold"))
            label.pack(anchor="w", pady=(6, 1))
            self.metric_labels[key] = label

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        for specification in CHARTS:
            tab = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(tab, text=specification[0])
            canvas = tk.Canvas(tab, bg=WHITE, highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda _event, c=canvas, s=specification:
                        self._draw(c, s))
            self.chart_canvases.append((canvas, specification))
        ttk.Label(outer, text="仅离线合成或已获授权的数据 / Synthetic or explicitly supplied data only. "
                  "成本、购电量与峰值独立解释 / Interpret metrics separately.",
                  style="Sub.TLabel").pack(anchor="w", pady=(10, 0))

    def _quality_text(self, message):
        self.quality.configure(state="normal")
        self.quality.delete("1.0", "end")
        self.quality.insert("1.0", message)
        self.quality.configure(state="disabled")

    def _invalidate(self, *_):
        self.imported = None
        self.chart = None
        self.run_button.configure(state="disabled")
        self.status.set("输入已改变，请重新检查 / Input changed; check again")
        for label in self.metric_labels.values():
            label.configure(text="—")
        for canvas, spec in self.chart_canvases:
            self._draw(canvas, spec)

    def _choose(self):
        selected = filedialog.askopenfilename(
            title="选择能源 CSV / Select energy CSV", filetypes=[("CSV", "*.csv"),
                                                    ("All files", "*.*")])
        if selected:
            self.path.set(selected)

    def _check(self):
        self._invalidate()
        result = import_energy_csv(Path(self.path.get()), self.step.get())
        report = result.report
        counts = ", ".join(f"{key}: {value}" for key, value in report.missing_counts.items())
        lines = [f"ready={report.ready}  |  rows={report.row_count}  |  step={report.step_hours} h",
                 f"缺失值 / Missing: {counts}"]
        lines.extend(f"{report.source}:{item.line} [{item.field}] {item.code}: {item.detail}"
                     for item in report.issues[:8])
        if len(report.issues) > 8:
            lines.append(f"另有 {len(report.issues) - 8} 项问题 / More issues")
        self._quality_text("\n".join(lines))
        if report.ready:
            self.imported = result
            if len(result.records) > MAX_INTERACTIVE_PERIODS:
                self.status.set(f"质量通过；交互调度最多 {MAX_INTERACTIVE_PERIODS} 时段 / "
                                f"Quality passed; interactive limit {MAX_INTERACTIVE_PERIODS}")
            else:
                self.run_button.configure(state="normal")
                self.status.set("质量检查通过 / Quality check passed")
        else:
            self.status.set("质量检查未通过；调度已禁止 / Invalid input; dispatch blocked")

    def _run(self):
        if self.imported is None or not self.imported.report.ready:
            self.status.set("请先通过质量检查 / Check data first")
            return
        self.run_button.configure(state="disabled")
        self.status.set("正在计算与审计 / Solving and auditing…")
        self.root.update_idletasks()
        try:
            result = run_comparison(self.imported)
        except DashboardError as exc:
            self.chart = None
            for label in self.metric_labels.values():
                label.configure(text="—")
            for canvas, spec in self.chart_canvases:
                self._draw(canvas, spec)
            self.status.set(f"无法显示结果 / No result: {exc}")
            return
        finally:
            self.run_button.configure(state="normal")
        self.chart = result.chart
        self.status.set(result.audit_message)
        for key, label in self.metric_labels.items():
            optimized = self.chart["metrics"]["optimized"][key]
            baseline = self.chart["metrics"]["baseline"][key]
            label.configure(text=f"{optimized:,.2f}  /  {baseline:,.2f}")
        for canvas, spec in self.chart_canvases:
            self._draw(canvas, spec)

    def _draw(self, canvas, specification):
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 220)
        if self.chart is None:
            canvas.create_text(width / 2, height / 2,
                               text="通过质量检查并运行对比后显示图表\n"
                                    "Check data and run comparison to see charts",
                               fill=MUTED, font=("Microsoft YaHei UI", 11), justify="center")
            return
        stamps, series, unit = chart_values(self.chart, specification)
        left, right, top, bottom = 64, width - 28, 46, height - 55
        maximum = max((max(values) for _, _, values in series), default=0)
        maximum = max(maximum * 1.1, 1)
        canvas.create_line(left, top, left, bottom, right, bottom, fill="#9CB0BB")
        canvas.create_text(left, 20, text=unit, anchor="w", fill=MUTED,
                           font=("Segoe UI", 10))
        for index in range(5):
            y = bottom - (bottom - top) * index / 4
            canvas.create_line(left, y, right, y, fill="#E4EBEF")
            canvas.create_text(left - 8, y, text=f"{maximum * index / 4:.0f}",
                               anchor="e", fill=MUTED, font=("Segoe UI", 9))
        span = max(1, len(stamps) - 1)
        for label, color, values in series:
            coords = []
            for index, value in enumerate(values):
                coords.extend((left + (right - left) * index / span,
                               bottom - (bottom - top) * value / maximum))
            if len(coords) >= 4:
                canvas.create_line(*coords, fill=color, width=2.5, smooth=False)
            elif coords:
                canvas.create_oval(coords[0] - 3, coords[1] - 3, coords[0] + 3,
                                   coords[1] + 3, fill=color, outline=color)
        for index in sorted({0, len(stamps) // 2, len(stamps) - 1}):
            x = left + (right - left) * index / span
            canvas.create_text(x, bottom + 19, text=stamps[index][5:],
                               anchor="w" if index == 0 else "e" if index == len(stamps) - 1
                               else "center", fill=MUTED, font=("Segoe UI", 9))
        x = left
        for label, color, _ in series:
            canvas.create_line(x, height - 18, x + 20, height - 18, fill=color, width=3)
            canvas.create_text(x + 26, height - 18, text=label, anchor="w", fill=INK,
                               font=("Microsoft YaHei UI", 9))
            x += 190


def main():
    if tk is None:
        raise RuntimeError("Tkinter is unavailable; install a Python build with Tk support")
    root = tk.Tk()
    EnergyDesktop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
