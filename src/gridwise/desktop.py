"""Local Tkinter dashboard for synthetic or explicitly selected CSV files."""

from __future__ import annotations

import queue
import math
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

from .dashboard import (DashboardError, MAX_INTERACTIVE_PERIODS, ROOT, default_runs_root,
                        list_run_history, load_verified_comparison, run_comparison)
from .data import import_energy_csv, summarize_energy_input
from .run_storage import (RunStorageError, default_config_path, load_saved_runs_root,
                          save_runs_root)


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
    def __init__(self, root: tk.Tk, *, runs_root: Path | None = None,
                 config_path: Path | None = None):
        self.root = root
        root.title("GridWise Campus | 校园能源分析")
        root.geometry("1140x780")
        root.minsize(900, 650)
        root.configure(bg=PAPER)
        self.path = tk.StringVar(value=str(SAMPLE))
        self.step = tk.StringVar(value="1")
        self.optimization_mode = tk.StringVar(value="cost")
        self.peak_cap = tk.StringVar(value="55")
        self.policy_result = tk.StringVar(
            value="模式：纯电费优化 / Mode: cost-only optimization")
        self._updating_policy = False
        self.config_path = config_path or default_config_path()
        self._config_error = None
        saved = None
        if runs_root is None:
            try:
                saved = load_saved_runs_root(self.config_path)
            except RunStorageError as exc:
                self._config_error = str(exc)
        self.runs_root = tk.StringVar(value=str(runs_root or saved or default_runs_root()))
        self.storage_notice = tk.StringVar(value=(
            "已保存的本地目录 / Saved local directory" if saved is not None else
            "临时目录；选择并保存可持久保留 / Temporary; choose and save for persistence"))
        self.selected_run = tk.StringVar()
        self._history_entries = {}
        self.status = tk.StringVar(value="请选择 CSV 并检查质量 / Select a CSV and check quality")
        self.imported = None
        self.input_summary = None
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
        if self._config_error:
            self.status.set("运行目录设置不可用，已使用临时目录 / Saved runs root unavailable")
            self._append_log(f"RunStorageError: {self._config_error}")
        self.path.trace_add("write", self._invalidate)
        self.step.trace_add("write", self._invalidate)
        self.runs_root.trace_add("write", self._invalidate_root)
        self.optimization_mode.trace_add("write", self._policy_changed)
        self.peak_cap.trace_add("write", self._policy_changed)
        self._poll_id = root.after(50, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Next>", self._scroll_keyboard)
        root.bind("<Prior>", self._scroll_keyboard)
        root.bind("<Escape>", self._cancel_job)
        if saved is not None:
            self._refresh_history()

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

        self.import_scope_label = ttk.Label(
            outer,
            text=("CSV 仅替换负荷、光伏、电价和时间序列；电网、电池及 SOC 参数仍来自合成设备场景；"
                  f"桌面最多 {MAX_INTERACTIVE_PERIODS} 时段。 / CSV replaces load, PV, price and "
                  "timestamps only; grid, battery and SOC parameters remain from the synthetic "
                  f"equipment scenario; desktop limit: {MAX_INTERACTIVE_PERIODS} periods."),
            style="Sub.TLabel", wraplength=980)
        self.import_scope_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.import_scope_label.bind(
            "<Configure>", lambda event: self.import_scope_label.configure(
                wraplength=max(120, event.width - 12)))

        policy = ttk.LabelFrame(outer, text="优化模式 / Optimization mode", padding=(12, 8))
        policy.pack(fill="x", pady=(12, 0))
        self.cost_mode_button = ttk.Radiobutton(
            policy, text="纯电费优化 / Cost-only", variable=self.optimization_mode,
            value="cost")
        self.cost_mode_button.grid(row=0, column=0, sticky="w")
        self.peak_mode_button = ttk.Radiobutton(
            policy, text="峰值上限优化 / Peak cap", variable=self.optimization_mode,
            value="peak_cap")
        self.peak_mode_button.grid(row=0, column=1, sticky="w", padx=(18, 0))
        ttk.Label(policy, text="峰值上限 / Cap (kW)").grid(
            row=0, column=2, sticky="e", padx=(24, 6))
        self.peak_cap_entry = ttk.Entry(policy, textvariable=self.peak_cap, width=12,
                                        state="disabled")
        self.peak_cap_entry.grid(row=0, column=3, sticky="w")
        ttk.Label(
            policy,
            text="削峰可能增加电费；购电量变化不等于节能 / Peak reduction may raise cost; energy changes are not savings.",
            style="Sub.TLabel", wraplength=760).grid(
                row=1, column=0, columnspan=4, sticky="w", pady=(7, 0))
        policy.columnconfigure(1, weight=1)

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

        history = ttk.Frame(outer)
        history.pack(fill="x", pady=(8, 12))
        ttk.Label(history, text="本地运行根目录 / Local runs root").grid(
            row=0, column=0, columnspan=5, sticky="w")
        ttk.Entry(history, textvariable=self.runs_root).grid(
            row=1, column=0, columnspan=3, sticky="ew", pady=5)
        ttk.Button(history, text="选择目录 / Browse root", command=self._choose_runs_root).grid(
            row=1, column=3, padx=(8, 0))
        self.save_root_button = ttk.Button(history, text="保存目录 / Save root",
                                           command=self._save_runs_root)
        self.save_root_button.grid(row=1, column=4, padx=(8, 0))
        ttk.Label(history, textvariable=self.storage_notice, style="Sub.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w")
        self.refresh_button = ttk.Button(history, text="刷新运行 / Refresh runs",
                                         command=self._refresh_history)
        self.refresh_button.grid(row=2, column=4, padx=(8, 0))
        ttk.Label(history, text="运行历史 / Run history (VALID / INCOMPLETE / DAMAGED)").grid(
            row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))
        self.history_combo = ttk.Combobox(history, textvariable=self.selected_run,
                                          state="readonly", values=())
        self.history_combo.grid(row=4, column=0, columnspan=4, sticky="ew", pady=5)
        self.load_button = ttk.Button(history, text="读取所选 / Load selected",
                                      command=self._load_history, state="disabled")
        self.load_button.grid(row=4, column=4, padx=(8, 0))
        history.columnconfigure(0, weight=1)
        history.columnconfigure(1, weight=1)
        history.columnconfigure(2, weight=1)
        self.history_combo.bind("<<ComboboxSelected>>", self._select_history)

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

        self.policy_result_label = ttk.Label(outer, textvariable=self.policy_result,
                                              style="Sub.TLabel", wraplength=900)
        self.policy_result_label.pack(anchor="w", fill="x", pady=(0, 10))

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
        self.policy_result.set(
            "模式：峰值上限优化（等待运行）/ Mode: peak cap (not run)"
            if self.optimization_mode.get() == "peak_cap" else
            "模式：纯电费优化 / Mode: cost-only optimization")
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
        self.input_summary = None
        self.check_button.configure(state="normal")
        self.run_button.configure(state="disabled")
        self.load_button.configure(state="disabled")
        self.refresh_button.configure(state="normal")
        self.selected_run.set("")
        self.cancel_button.configure(state="disabled")
        self.status.set("输入已改变，请重新检查 / Input changed; check again")
        self._clear_results()
        self._quality_text("数据与运行日志 / Data and run log\n等待检查 / Awaiting check")

    def _invalidate_root(self, *_):
        self._invalidate()
        self.storage_notice.set("目录未保存 / Directory not saved")
        self.history_combo.configure(values=())
        self._history_entries.clear()

    def _policy_changed(self, *_):
        if self._updating_policy or self._closing:
            return
        if self._cancel is not None:
            self._cancel.set()
        self._generation += 1
        self._cancel = None
        self._cancel_requested = False
        self._active_kind = None
        self._stop_progress()
        self.selected_run.set("")
        self.load_button.configure(state="disabled")
        self._clear_results()
        self.status.set("优化模式已改变；请重新运行 / Optimization mode changed; run again")
        if self.imported is not None and self.imported.report.ready and \
                len(self.imported.records) <= MAX_INTERACTIVE_PERIODS:
            self.run_button.configure(state="normal")
        self._sync_policy_controls()

    def _sync_policy_controls(self):
        busy = self._active_kind is not None or self._closing
        radio_state = "disabled" if busy else "normal"
        self.cost_mode_button.configure(state=radio_state)
        self.peak_mode_button.configure(state=radio_state)
        entry_state = "normal" if not busy and self.optimization_mode.get() == "peak_cap" \
            else "disabled"
        self.peak_cap_entry.configure(state=entry_state)

    def _peak_cap_value(self):
        text = self.peak_cap.get().strip()
        try:
            value = float(text)
        except (TypeError, ValueError) as exc:
            raise DashboardError("峰值上限必须是数字 / Peak cap must be numeric") from exc
        if not text or not math.isfinite(value) or value < 0 or value > 1_000_000_000_000:
            raise DashboardError(
                "峰值上限必须是 0 至 1e12 之间的有限数值 / Peak cap must be finite and within 0..1e12")
        return value

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
        self._sync_policy_controls()
        self.check_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.load_button.configure(state="disabled")
        self.save_root_button.configure(state="disabled")
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
            self._sync_policy_controls()
            self.check_button.configure(state="normal")
            self.refresh_button.configure(state="normal")
            self.save_root_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            if cancelled:
                self._clear_results()
                if kind in ("run", "history_load") and self.imported is not None:
                    self.run_button.configure(state="normal")
                self.status.set("已取消 / Cancelled")
                self._append_log("任务已取消 / Task cancelled")
            else:
                try:
                    if error is not None:
                        raise error
                    if kind == "quality":
                        self._show_quality(value)
                    elif kind == "history":
                        self._show_history(value)
                    else:
                        self._show_result(value)
                except Exception as exc:
                    self._show_failure(kind, exc)
            if focus_on_cancel:
                (self.run_button if kind in ("run", "history_load") and self.imported is not None
                 else self.check_button).focus_set()
            self._select_history()
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
        if kind in ("run", "history_load") and self.imported is not None:
            self.run_button.configure(state="normal")
        self.status.set("运行失败；详见日志 / Run failed; see log" if kind in ("run", "history_load")
                        else "检查失败；详见日志 / Check failed; see log")
        self._append_log(f"{type(error).__name__}: {error}")

    def _on_close(self):
        if self._closing or self._closed:
            return
        self._closing = True
        self._sync_policy_controls()
        if self._cancel is not None:
            self._cancel.set()
        if self._pending_jobs:
            self.check_button.configure(state="disabled")
            self.run_button.configure(state="disabled")
            self.refresh_button.configure(state="disabled")
            self.load_button.configure(state="disabled")
            self.save_root_button.configure(state="disabled")
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

    def _choose_runs_root(self):
        if self._closing or self._active_kind is not None:
            return
        selected = filedialog.askdirectory(title="选择运行根目录 / Select runs root")
        if selected:
            self.runs_root.set(selected)

    def _save_runs_root(self):
        if self._closing or self._active_kind is not None:
            return
        try:
            save_runs_root(self.config_path, Path(self.runs_root.get()))
        except RunStorageError as exc:
            self.status.set("保存运行目录失败；详见日志 / Cannot save runs root")
            self._append_log(f"RunStorageError: {exc}")
            return
        self.storage_notice.set("已保存的本地目录 / Saved local directory")
        self.status.set("运行目录已保存 / Runs root saved")

    def _refresh_history(self):
        if self._closing or self._active_kind is not None:
            return
        runs_root = Path(self.runs_root.get())
        self.status.set("正在核验运行历史 / Verifying run history…")
        self._start_job("history", lambda cancel: list_run_history(runs_root, cancel_event=cancel))

    def _show_history(self, entries):
        self._history_entries = {entry.name: entry for entry in entries}
        labels = tuple(self._history_label(entry) for entry in entries)
        self.history_combo.configure(values=labels)
        self.selected_run.set("")
        self.load_button.configure(state="disabled")
        counts = {status: sum(entry.status == status for entry in entries)
                  for status in ("VALID", "INCOMPLETE", "DAMAGED")}
        self.status.set("运行历史 / Runs: " + ", ".join(
            f"{status}={count}" for status, count in counts.items()))
        for entry in entries:
            if entry.status != "VALID":
                self._append_log(f"{entry.name} [{entry.status}]: {entry.detail}")

    def _select_history(self, _event=None):
        selected = self.selected_run.get()
        name = selected.split(" [", 1)[0] if " [" in selected else ""
        available = (not self._closing and self._active_kind is None and
                     self.imported is not None and self.imported.report.ready and
                     name in self._history_entries)
        self.load_button.configure(state="normal" if available else "disabled")

    def _load_history(self):
        if self._closing or self._active_kind is not None:
            return
        selected = self.selected_run.get()
        name = selected.split(" [", 1)[0] if " [" in selected else ""
        if self.imported is None or not self.imported.report.ready or name not in self._history_entries:
            self.status.set("请先检查输入并选择运行 / Check input and select a run")
            return
        imported = self.imported
        runs_root = Path(self.runs_root.get())
        self._clear_results()
        self.run_button.configure(state="disabled")
        self.status.set("正在重新校验运行 / Revalidating selected run…")
        self._start_job("history_load", lambda cancel: load_verified_comparison(
            imported, name, runs_root, cancel_event=cancel))

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
        summary = summarize_energy_input(result) if report.ready else None
        if summary is not None:
            lines.extend([
                "数据摘要 / Input summary",
                f"时间 / Time: {summary['start_timestamp']} → {summary['end_timestamp']}",
                f"负荷 / Load: {summary['load_kw']['min']:,.2f}–{summary['load_kw']['max']:,.2f} kW; "
                f"{summary['load_energy_kwh']:,.2f} kWh",
                f"光伏可用 / PV available: {summary['pv_kw']['min']:,.2f}–"
                f"{summary['pv_kw']['max']:,.2f} kW; {summary['pv_available_energy_kwh']:,.2f} kWh",
                f"电价 / Price: {summary['price_cny_per_kwh']['min']:,.4g}–"
                f"{summary['price_cny_per_kwh']['max']:,.4g} CNY/kWh",
            ])
        lines.extend(f"{report.source}:{item.line} [{item.field}] {item.code}: {item.detail}"
                     for item in report.issues)
        self._quality_text("\n".join(lines))
        if report.ready:
            self.imported = result
            self.input_summary = summary
            if len(result.records) > MAX_INTERACTIVE_PERIODS:
                self.status.set(f"质量通过；交互调度最多 {MAX_INTERACTIVE_PERIODS} 时段 / "
                                f"Quality passed; interactive limit {MAX_INTERACTIVE_PERIODS}")
            else:
                self.run_button.configure(state="normal")
                self.status.set("质量检查通过 / Quality check passed")
            self._select_history()
        else:
            self.imported = None
            self.input_summary = None
            self.run_button.configure(state="disabled")
            self.status.set("质量检查未通过；调度已禁止 / Invalid input; dispatch blocked")

    def _run(self):
        if self._closing:
            return
        if self._active_kind is not None:
            return
        if self.imported is None or not self.imported.report.ready:
            self.status.set("请先通过质量检查 / Check data first")
            return
        if len(self.imported.records) > MAX_INTERACTIVE_PERIODS:
            self.status.set("超过交互式时段上限 / Interactive period limit exceeded")
            return
        peak_cap_kw = None
        if self.optimization_mode.get() == "peak_cap":
            try:
                peak_cap_kw = self._peak_cap_value()
            except DashboardError as exc:
                self._clear_results()
                self.status.set("峰值上限无效；详见日志 / Invalid peak cap; see log")
                self._append_log(f"DashboardError: {exc}")
                return
        imported = self.imported
        self._clear_results()
        self.run_button.configure(state="disabled")
        self.status.set("正在计算与审计 / Solving and auditing…")
        runs_root = Path(self.runs_root.get())
        self._start_job("run", lambda cancel: run_comparison(
            imported, cancel_event=cancel, runs_root=runs_root,
            peak_cap_kw=peak_cap_kw))

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
        self._updating_policy = True
        try:
            self.optimization_mode.set(result.optimization_mode)
            if result.peak_cap_kw is not None:
                self.peak_cap.set(f"{result.peak_cap_kw:g}")
        finally:
            self._updating_policy = False
        self._sync_policy_controls()
        if result.run_name:
            from .dashboard import RunHistoryEntry
            self._history_entries[result.run_name] = RunHistoryEntry(
                result.run_name, "VALID", "verified",
                result.optimization_mode, result.peak_cap_kw)
            self.history_combo.configure(values=tuple(
                self._history_label(entry) for entry in self._history_entries.values()))
            self.selected_run.set(self._history_label(self._history_entries[result.run_name]))
        for key, pair in self.metric_labels.items():
            optimized = self.chart["metrics"]["optimized"][key]
            baseline = self.chart["metrics"]["baseline"][key]
            pair[0].configure(text=f"{optimized:,.2f}")
            pair[1].configure(text=f"{baseline:,.2f}")
        if result.optimization_mode == "peak_cap":
            cap = result.peak_cap_kw
            actual = float(self.chart["metrics"]["optimized"]["peak_grid_kw"])
            if cap is None or not math.isfinite(cap) or actual > cap + 1e-5:
                raise DashboardError("削峰结果未满足峰值上限 / Peak result exceeds the cap")
            comparison = "无同输入有效对照 / no verified same-input reference"
            if result.cost_mode_cost_cny is not None:
                delta = float(self.chart["metrics"]["optimized"]["cost_cny"]) - \
                    result.cost_mode_cost_cny
                comparison = f"{delta:+,.2f} CNY"
            self.policy_result.set(
                f"峰值上限 / Cap: {cap:,.2f} kW  |  实际峰值 / Actual: {actual:,.2f} kW  |  "
                f"满足 / PASS  |  相对纯电费 / vs cost-only: {comparison}")
        else:
            self.policy_result.set("模式：纯电费优化 / Mode: cost-only optimization")
        self._relayout_metric_cards()
        for canvas, spec in self.chart_canvases:
            self._draw(canvas, spec)
        self.run_button.configure(state="normal")
        self._select_history()

    @staticmethod
    def _history_label(entry):
        if entry.optimization_mode == "peak_cap" and entry.peak_cap_kw is not None:
            policy = f"峰值 {entry.peak_cap_kw:g} kW / Peak cap"
        elif entry.optimization_mode == "cost":
            policy = "纯电费 / Cost-only"
        else:
            policy = "模式未知 / Unknown mode"
        return f"{entry.name} [{entry.status}] · {policy}"

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
