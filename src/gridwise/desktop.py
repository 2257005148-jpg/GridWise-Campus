"""Local Tkinter dashboard for synthetic or explicitly selected CSV files."""

from __future__ import annotations

import queue
import sys
import threading
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


def _enable_windows_dpi_awareness():
    """Opt in before Tk creates a window, preserving an existing process policy."""
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
    context = user32.GetThreadDpiAwarenessContext()
    if user32.GetAwarenessFromDpiAwarenessContext(context) != 0:
        return
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        raise ctypes.WinError(ctypes.get_last_error())


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
        root.title("GridWise Campus v0.3.4 | 校园能源分析")
        root.geometry("1140x780")
        root.minsize(900, 650)
        root.configure(bg=PAPER)
        self.path = tk.StringVar(value=str(SAMPLE))
        self.step = tk.StringVar(value="1")
        self.status = tk.StringVar(value="请选择 CSV 并检查质量 / Select a CSV and check quality")
        self.imported = None
        self.chart = None
        self.chart_canvases = []
        self._events = queue.Queue()
        self._generation = 0
        self._cancel = None
        self._cancel_requested = False
        self._active_kind = None
        self._closed = False
        self._closing = False
        self._pending_jobs = set()
        self._cards = []
        self._card_columns = None
        self._style()
        self._layout()
        self.path.trace_add("write", self._invalidate)
        self.step.trace_add("write", self._invalidate)
        self._poll_id = root.after(50, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Next>", self._scroll_keyboard)
        root.bind("<Prior>", self._scroll_keyboard)
        root.bind("<Escape>", self._cancel_job)

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
        style.configure("TEntry", padding=6)
        style.configure("Run.TButton", background=TEAL, foreground=WHITE)
        style.map("Run.TButton", background=[("active", "#06696D"),
                                                ("disabled", "#A8B8B9")])
        style.configure("TNotebook", background=PAPER, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 9),
                        font=("Microsoft YaHei UI", 9))

    def _layout(self):
        self._viewport = tk.Canvas(self.root, bg=PAPER, highlightthickness=0)
        self._viewport.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical",
                                  command=self._viewport.yview)
        scrollbar.pack(side="right", fill="y")
        self._viewport.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(self._viewport, padding=18)
        self._content = self._viewport.create_window((0, 0), window=outer, anchor="nw")
        self._viewport.bind("<Configure>", self._resize_viewport)
        outer.bind("<Configure>", lambda _event: self._viewport.configure(
            scrollregion=self._viewport.bbox("all")))
        self._viewport.bind("<MouseWheel>", self._scroll_page)
        outer.bind("<MouseWheel>", self._scroll_page)
        ttk.Label(outer, text="GridWise Campus", style="Title.TLabel").pack(anchor="w")
        self.subtitle = ttk.Label(outer, text="校园能源调度观察台 / Offline dispatch workspace",
                                  style="Sub.TLabel")
        self.subtitle.pack(anchor="w", fill="x", pady=(0, 18))

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
        self.check_button = ttk.Button(actions, text="检查数据 / Check data", command=self._check)
        self.check_button.pack(side="left")
        self.run_button = ttk.Button(actions, text="运行对比 / Run comparison",
                                     style="Run.TButton", command=self._run, state="disabled")
        self.run_button.pack(side="left", padx=10)
        self.cancel_button = ttk.Button(actions, text="取消 / Cancel",
                                        command=self._cancel_job, state="disabled")
        self.cancel_button.pack(side="left")
        self.status_label = ttk.Label(actions, textvariable=self.status,
                                      style="Sub.TLabel", wraplength=450)
        self.status_label.pack(side="left", padx=12, fill="x", expand=True)
        self.status_label.bind("<Configure>", lambda event: self.status_label.configure(
            wraplength=max(80, event.width - 12)))
        self.progress = ttk.Progressbar(outer, mode="indeterminate")

        ttk.Label(outer, text="数据质量与运行日志 / Data quality and run log").pack(anchor="w")
        quality_frame = ttk.Frame(outer)
        quality_frame.pack(fill="x", pady=(10, 14))
        self.quality = tk.Text(quality_frame, height=5, wrap="word", bg=WHITE, fg=INK,
                               relief="flat", padx=12, pady=9, takefocus=1,
                               font=("Microsoft YaHei UI", 9))
        self.quality.pack(side="left", fill="x", expand=True)
        quality_scroll = ttk.Scrollbar(quality_frame, orient="vertical",
                                       command=self.quality.yview)
        quality_scroll.pack(side="right", fill="y")
        self.quality.configure(yscrollcommand=quality_scroll.set)
        self._quality_text("数据与运行日志 / Data and run log\n等待检查 / Awaiting check")

        metric_frame = ttk.Frame(outer)
        metric_frame.pack(fill="x", pady=(0, 12))
        self.metric_frame = metric_frame
        metric_frame.bind("<Configure>", self._relayout_metric_cards)
        self.metric_labels = {}
        for index, (key, caption) in enumerate((
            ("cost_cny", "购电费用 / Cost · CNY"),
            ("grid_energy_kwh", "购电量 / Grid energy · kWh"),
            ("peak_grid_kw", "购电峰值 / Grid peak · kW"),
        )):
            card = tk.Frame(metric_frame, bg=WHITE, padx=14, pady=10)
            self._cards.append(card)
            tk.Label(card, text=caption, bg=WHITE, fg=MUTED,
                     font=("Microsoft YaHei UI", 9)).pack(anchor="w")
            values = tk.Frame(card, bg=WHITE)
            values.pack(fill="x", pady=(5, 0))
            optimized_row = tk.Frame(values, bg=WHITE)
            optimized_row.pack(fill="x")
            tk.Label(optimized_row, text="优化 / Optimized", bg=WHITE, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="left")
            optimized_label = tk.Label(optimized_row, text="—", bg=WHITE, fg=TEAL,
                                       font=("Microsoft YaHei UI", 15, "bold"))
            optimized_label.pack(side="right")
            baseline_row = tk.Frame(values, bg=WHITE)
            baseline_row.pack(fill="x")
            tk.Label(baseline_row, text="基线 / Baseline", bg=WHITE, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="left")
            baseline_label = tk.Label(baseline_row, text="—", bg=WHITE, fg=MUTED,
                                      font=("Microsoft YaHei UI", 13))
            baseline_label.pack(side="right")
            self.metric_labels[key] = (optimized_label, baseline_label)

        chart_height = max(300, round(300 * float(self.root.tk.call("tk", "scaling")) / 1.333))
        self.notebook = ttk.Notebook(outer, height=chart_height)
        self.notebook.pack(fill="x")
        for specification in CHARTS:
            tab = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(tab, text=specification[0])
            canvas = tk.Canvas(tab, bg=WHITE, highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            canvas.bind("<Configure>", lambda _event, c=canvas, s=specification:
                        self._draw(c, s))
            canvas.bind("<MouseWheel>", self._scroll_page)
            self.chart_canvases.append((canvas, specification))
        self.footer = ttk.Label(
            outer, text="仅离线合成或已获授权的数据 / Synthetic or explicitly supplied data only. "
                        "成本、购电量与峰值独立解释 / Interpret metrics separately.",
            style="Sub.TLabel")
        self.footer.pack(anchor="w", fill="x", pady=(10, 0))

    def _scroll_page(self, event):
        self._viewport.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _scroll_keyboard(self, event):
        if isinstance(event.widget, tk.Text):
            return None  # Keep the log's own Page Up/Down navigation.
        self._viewport.yview_scroll(1 if event.keysym == "Next" else -1, "pages")
        return "break"

    def _resize_viewport(self, event):
        self._viewport.itemconfigure(self._content, width=event.width)
        text_width = max(180, event.width - 48)
        self.subtitle.configure(wraplength=text_width)
        self.footer.configure(wraplength=text_width)
        self._relayout_metric_cards()
        full = [specification[0] for specification in CHARTS]
        compact = ["能源 / Energy", "购电 / Grid", "逐时 / Period", "累计 / Total", "SOC"]
        for index, title in enumerate(full):
            self.notebook.tab(index, text=title)
        if self.notebook.winfo_reqwidth() > event.width - 36:
            for index, title in enumerate(compact):
                self.notebook.tab(index, text=title)

    def _relayout_metric_cards(self, _event=None):
        if not self._cards:
            return
        available = max(1, self._viewport.winfo_width() - 36)
        requested = max(card.winfo_reqwidth() for card in self._cards)
        columns = min(3, max(1, (available + 10) // (requested + 10)))
        if columns == self._card_columns:
            return
        for card in self._cards:
            card.grid_forget()
        for column in range(3):
            self.metric_frame.columnconfigure(column, weight=1 if column < columns else 0)
        for index, card in enumerate(self._cards):
            card.grid(row=index // columns, column=index % columns, padx=(0, 10),
                      pady=(0, 8), sticky="ew")
        self._card_columns = columns

    def _quality_text(self, message):
        self.quality.configure(state="normal")
        self.quality.delete("1.0", "end")
        self.quality.insert("1.0", message)
        self.quality.configure(state="disabled")

    def _append_log(self, message):
        self.quality.configure(state="normal")
        self.quality.insert("end", "\n" + message)
        self.quality.see("end")
        self.quality.configure(state="disabled")

    def _clear_results(self):
        self.chart = None
        for pair in self.metric_labels.values():
            for label in pair:
                label.configure(text="—")
        for canvas, specification in self.chart_canvases:
            self._draw(canvas, specification)

    def _invalidate(self, *_):
        if self._closing:
            return
        if self._cancel is not None:
            self._cancel.set()
        self._stop_progress()
        self._generation += 1
        self._cancel = None
        self._cancel_requested = False
        self._active_kind = None
        self.imported = None
        self.check_button.configure(state="normal")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.status.set("输入已改变，请重新检查 / Input changed; check again")
        self._clear_results()
        self._quality_text("数据与运行日志 / Data and run log\n等待检查 / Awaiting check")

    def _job_running(self):
        return self._active_kind == "run" and self._cancel is not None

    def _start_job(self, kind, operation):
        self._generation += 1
        generation = self._generation
        self._pending_jobs.add(generation)
        cancel = threading.Event()
        self._cancel = cancel
        self._cancel_requested = False
        self._active_kind = kind
        self.check_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_button.focus_set()
        self.progress.pack(fill="x", pady=(2, 0))
        self.progress.start(15)

        def worker():
            try:
                value = operation(cancel)
                self._events.put((generation, kind, value, None))
            except Exception as exc:
                self._events.put((generation, kind, None, exc))

        threading.Thread(target=worker, daemon=True, name=f"gridwise-{kind}").start()

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                generation, kind, value, error = self._events.get_nowait()
            except queue.Empty:
                break
            self._pending_jobs.discard(generation)
            if self._closing:
                if not self._pending_jobs:
                    self._finish_close()
                    return
                continue
            if generation != self._generation:
                continue
            cancelled = self._cancel_requested
            focus_on_cancel = self.root.focus_get() == self.cancel_button
            self._stop_progress()
            self._cancel = None
            self._cancel_requested = False
            self._active_kind = None
            self.check_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            if cancelled:
                self._clear_results()
                if kind == "run" and self.imported is not None:
                    self.run_button.configure(state="normal")
                self.status.set("已取消 / Cancelled")
                self._append_log("任务已取消 / Task cancelled")
            else:
                try:
                    if error is not None:
                        raise error
                    if kind == "quality":
                        self._show_quality(value)
                    else:
                        self._show_result(value)
                except Exception as exc:
                    self._show_failure(kind, exc)
            if focus_on_cancel:
                (self.run_button if kind == "run" and self.imported is not None
                 else self.check_button).focus_set()
        self._poll_id = self.root.after(50, self._poll)

    def _cancel_job(self, _event=None):
        if self._closing or self._cancel is None or self._cancel_requested:
            return
        self._cancel_requested = True
        self._cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在取消 / Cancelling…")

    def _stop_progress(self):
        self.progress.stop()
        self.progress.pack_forget()

    def _show_failure(self, kind, error):
        self._clear_results()
        if kind == "run" and self.imported is not None:
            self.run_button.configure(state="normal")
        self.status.set("运行失败；详见日志 / Run failed; see log" if kind == "run"
                        else "检查失败；详见日志 / Check failed; see log")
        self._append_log(f"{type(error).__name__}: {error}")

    def _on_close(self):
        if self._closing or self._closed:
            return
        self._closing = True
        if self._cancel is not None:
            self._cancel.set()
        if self._pending_jobs:
            self.check_button.configure(state="disabled")
            self.run_button.configure(state="disabled")
            self.cancel_button.configure(state="disabled")
            self.status.set("正在关闭并清理 / Closing and cleaning up…")
            return
        self._finish_close()

    def _finish_close(self):
        self._closed = True
        self._stop_progress()
        self.root.after_cancel(self._poll_id)
        self.root.destroy()

    def _choose(self):
        if self._closing:
            return
        selected = filedialog.askopenfilename(
            title="选择能源 CSV / Select energy CSV", filetypes=[("CSV", "*.csv"),
                                                    ("All files", "*.*")])
        if selected:
            self.path.set(selected)

    def _check(self):
        if self._closing or self._active_kind is not None:
            return
        self._invalidate()
        path, step = Path(self.path.get()), self.step.get()
        self.status.set("正在检查数据 / Checking data…")
        self._start_job("quality", lambda _cancel: import_energy_csv(path, step))

    def _show_quality(self, result):
        report = result.report
        counts = ", ".join(f"{key}: {value}" for key, value in report.missing_counts.items())
        lines = [f"ready={report.ready}  |  rows={report.row_count}  |  step={report.step_hours} h",
                 f"缺失值 / Missing: {counts}"]
        lines.extend(f"{report.source}:{item.line} [{item.field}] {item.code}: {item.detail}"
                     for item in report.issues)
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
            self.imported = None
            self.run_button.configure(state="disabled")
            self.status.set("质量检查未通过；调度已禁止 / Invalid input; dispatch blocked")

    def _run(self):
        if self._closing:
            return
        if self._job_running():
            return
        if self.imported is None or not self.imported.report.ready:
            self.status.set("请先通过质量检查 / Check data first")
            return
        if len(self.imported.records) > MAX_INTERACTIVE_PERIODS:
            self.status.set("超过交互式时段上限 / Interactive period limit exceeded")
            return
        imported = self.imported
        self._clear_results()
        self.run_button.configure(state="disabled")
        self.status.set("正在计算与审计 / Solving and auditing…")
        self._start_job("run", lambda cancel: run_comparison(imported, cancel_event=cancel))

    def _show_result(self, result):
        try:
            if not result.chart["timestamps"]:
                raise ValueError("empty time series")
            for specification in CHARTS:
                chart_values(result.chart, specification)
            for key in self.metric_labels:
                for kind in ("optimized", "baseline"):
                    float(result.chart["metrics"][kind][key])
        except (KeyError, TypeError, ValueError) as exc:
            raise DashboardError(f"图表数据无效 / Invalid chart data: {exc}") from exc
        self.chart = result.chart
        self.status.set(result.audit_message)
        for key, pair in self.metric_labels.items():
            optimized = self.chart["metrics"]["optimized"][key]
            baseline = self.chart["metrics"]["baseline"][key]
            pair[0].configure(text=f"{optimized:,.2f}")
            pair[1].configure(text=f"{baseline:,.2f}")
        self._relayout_metric_cards()
        for canvas, spec in self.chart_canvases:
            self._draw(canvas, spec)
        self.run_button.configure(state="normal")

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
        scale = max(1, float(self.root.tk.call("tk", "scaling")) / 1.333)
        left, right = 64, width - 28
        top = max(46, round(44 * scale))
        bottom = height - max(78, round(75 * scale))
        maximum = max((max(values) for _, _, values in series), default=0)
        maximum = max(maximum * 1.1, 1)
        canvas.create_line(left, top, left, bottom, right, bottom, fill="#9CB0BB")
        canvas.create_text(left, 20, text=specification[0], anchor="w", fill=INK,
                           font=("Microsoft YaHei UI", 11, "bold"))
        canvas.create_text(right, 20, text=unit, anchor="e", fill=MUTED,
                           font=("Segoe UI", 10))
        for index in range(5):
            y = bottom - (bottom - top) * index / 4
            canvas.create_line(left, y, right, y, fill="#E4EBEF")
            tick = maximum * index / 4
            canvas.create_text(left - 8, y, text=f"{tick:.2f}" if maximum < 10 else f"{tick:.0f}",
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
            stamp = stamps[index]
            offset = stamp[-6:] if len(stamp) >= 6 and stamp[-6] in "+-" else "+00:00"
            label = f"{stamp[5:10]} {stamp[11:16]}\nUTC{offset}"
            canvas.create_text(x, bottom + 27 * scale, text=label,
                               anchor="w" if index == 0 else "e" if index == len(stamps) - 1
                               else "center", fill=MUTED, font=("Segoe UI", 9))
        x = left
        legend_spacing = max(125, min(190, (right - left) // max(1, len(series))))
        for label, color, _ in series:
            legend_y = height - 20 * scale
            canvas.create_line(x, legend_y, x + 20, legend_y, fill=color, width=3)
            canvas.create_text(x + 26, legend_y, text=label, anchor="w", fill=INK,
                               font=("Microsoft YaHei UI", 9))
            x += legend_spacing


def main():
    if tk is None:
        raise RuntimeError("Tkinter is unavailable; install a Python build with Tk support")
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    EnergyDesktop(root)
    root.mainloop()


if __name__ == "__main__":
    main()
