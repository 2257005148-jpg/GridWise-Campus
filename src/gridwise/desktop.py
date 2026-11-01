"""Local Tkinter dashboard for synthetic or explicitly selected CSV files."""

from __future__ import annotations

import queue
import math
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # Pure chart selection remains usable without a GUI runtime.
    tk = None
    filedialog = None
    messagebox = None
    simpledialog = None
    ttk = None

from .dashboard import (DashboardError, MAX_INTERACTIVE_PERIODS, ROOT,
                        compare_verified_runs, default_device_parameters,
                        default_runs_root, filter_run_history, list_run_history,
                        load_verified_comparison, preview_verified_run,
                        run_comparison, sort_run_history)
from .data import import_energy_csv, summarize_energy_input
from .device import (DEVICE_FIELDS, DeviceParameterError, DeviceParameters,
                     parse_device_parameters)
from .diagnostics import (FailureCategory, FailureStage, RunDiagnostic,
                          diagnose_failure)
from .reports import (ReportCancellation, export_comparison_report,
                      export_single_run_report)
from .run_storage import (RunStorageError, default_config_path, load_saved_runs_root,
                          save_runs_root)
from .scenario_presets import (OptimizationPolicy, ScenarioPreset,
                               ScenarioPresetError, create_preset, delete_preset,
                               load_presets, save_presets, update_preset)


INK = "#16324A"
MUTED = "#587184"
PAPER = "#F5F8F8"
WHITE = "#FFFFFF"
TEAL = "#087E83"
BLUE = "#3675A8"
GOLD = "#B07820"
RED = "#AF453C"
SAMPLE = ROOT / "examples" / "synthetic-day.csv"
HISTORY_FILTER_ALL = "全部 / All"
HISTORY_MODE_COST = "纯电费 / Cost-only"
HISTORY_MODE_PEAK_CAP = "峰值上限 / Peak cap"
HISTORY_STATUS_FILTERS = {
    HISTORY_FILTER_ALL: None,
    "VALID": "VALID",
    "INCOMPLETE": "INCOMPLETE",
    "DAMAGED": "DAMAGED",
}
HISTORY_MODE_FILTERS = {
    HISTORY_FILTER_ALL: None,
    HISTORY_MODE_COST: "cost",
    HISTORY_MODE_PEAK_CAP: "peak_cap",
}
HISTORY_TABLE_COLUMNS = (
    ("name", "运行标识 / Run ID", "name", 360),
    ("status", "状态 / Status", "status", 120),
    ("mode", "优化模式 / Mode", "optimization_mode", 175),
    ("peak_cap", "峰值上限 / Peak cap", "peak_cap_kw", 125),
)


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

DIAGNOSTIC_CATEGORY_LABELS = {
    FailureCategory.INPUT_INVALID: "输入无效 / Input invalid",
    FailureCategory.INFEASIBLE: "模型不可行 / Model infeasible",
    FailureCategory.SOLVER_UNAVAILABLE: "求解器不可用 / Solver unavailable",
    FailureCategory.SOLVER_TIMEOUT: "求解超时 / Solver timeout",
    FailureCategory.SOLVER_ERROR: "求解失败 / Solver error",
    FailureCategory.CANCELLED: "任务取消 / Cancelled",
    FailureCategory.AUDIT_FAILED: "审计失败 / Audit failed",
    FailureCategory.ARTIFACT_INVALID: "产物无效 / Artifact invalid",
    FailureCategory.UNKNOWN: "未知原因 / Unknown cause",
}

DIAGNOSTIC_STAGE_LABELS = {
    FailureStage.INPUT: "输入 / input",
    FailureStage.SOLVE: "求解 / solve",
    FailureStage.AUDIT: "审计 / audit",
    FailureStage.ARTIFACT: "产物 / artifact",
    FailureStage.CONTROL: "控制 / control",
    FailureStage.UNKNOWN: "未知 / unknown",
}


def diagnostic_details_text(diagnostic: RunDiagnostic) -> str:
    """Format one existing diagnosis for display without reclassifying it."""
    lines = [
        "GridWise Campus 运行诊断 / Run diagnosis",
        f"类别 / Category: {diagnostic.category.value} — "
        f"{DIAGNOSTIC_CATEGORY_LABELS[diagnostic.category]}",
        f"阶段 / Stage: {DIAGNOSTIC_STAGE_LABELS[diagnostic.stage]}",
        f"摘要 / Summary: {diagnostic.summary}",
    ]
    if diagnostic.category is FailureCategory.UNKNOWN:
        lines.append(
            "说明 / Note: 证据不足，无法确定根因 / "
            "Insufficient evidence to determine the root cause")
    if diagnostic.solver_status is not None:
        lines.append(f"求解器状态 / Solver status: {diagnostic.solver_status}")
    if diagnostic.termination_condition is not None:
        lines.append(
            f"终止状态 / Termination condition: {diagnostic.termination_condition}")
    lines.extend(("", "原始证据 / Evidence"))
    lines.extend(f"- {item}" for item in diagnostic.evidence)
    lines.extend(("", "原始异常与异常链 / Original error and cause chain"))
    lines.extend(f"- {item}" for item in diagnostic.cause_chain)
    lines.extend(("", "人工检查建议 / Suggested checks"))
    lines.extend(f"- {item}" for item in diagnostic.suggestions)
    return "\n".join(lines)


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


class _JobControl:
    """Linearize user cancellation with completion being queued for the UI."""

    def __init__(self, cancel_event):
        self.cancel_event = cancel_event
        self._lock = threading.Lock()
        self._completed = False

    def request_cancel(self) -> bool:
        with self._lock:
            if self._completed:
                return False
            accepted = self.cancel_event.set()
            return accepted is not False

    def complete(self, publish) -> None:
        with self._lock:
            self._completed = True
            publish()


class EnergyDesktop:
    def __init__(self, root: tk.Tk, *, runs_root: Path | None = None,
                 config_path: Path | None = None,
                 preset_path: Path | None = None):
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
        self._updating_devices = False
        self.default_devices = default_device_parameters()
        self.device_values = {
            field: tk.StringVar(value=f"{getattr(self.default_devices, field):g}")
            for field in DEVICE_FIELDS
        }
        self.device_source = tk.StringVar(
            value="默认合成设备参数 / Default synthetic equipment parameters")
        self.config_path = Path(config_path or default_config_path())
        self.preset_path = Path(
            preset_path or self.config_path.with_name("scenario-presets.json"))
        self.presets: tuple[ScenarioPreset, ...] = ()
        self.selected_preset = tk.StringVar()
        self.preset_notice = tk.StringVar(value="尚无预设 / No presets")
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
        self.history_search = tk.StringVar()
        self.history_status_filter = tk.StringVar(value=HISTORY_FILTER_ALL)
        self.history_mode_filter = tk.StringVar(value=HISTORY_FILTER_ALL)
        self.history_filter_notice = tk.StringVar(value="尚未刷新 / Not refreshed")
        self.history_selection_detail = tk.StringVar(
            value="选择运行查看状态与详情 / Select a run to view status and details")
        self._history_sort_key = "name"
        self._history_sort_descending = False
        self.history_preview = None
        self.history_preview_status = tk.StringVar(
            value="尚未预览 / No verified preview")
        self.history_preview_context = tk.StringVar(
            value="选择有效运行并主动预览 / Select a valid run and preview")
        self.selected_compare_a = tk.StringVar()
        self.selected_compare_b = tk.StringVar()
        self.comparison_status = tk.StringVar(
            value="尚未比较 / No verified comparison yet")
        self.comparison_context = tk.StringVar(
            value="选择两个有效运行 / Select two valid runs")
        self._history_entries = {}
        self.history_comparison = None
        self.status = tk.StringVar(value="请选择 CSV 并检查质量 / Select a CSV and check quality")
        self.current_diagnostic = None
        self.diagnostic_category = tk.StringVar(
            value="无活动诊断 / No active diagnosis")
        self.diagnostic_stage = tk.StringVar(value="阶段 / Stage: —")
        self.diagnostic_summary = tk.StringVar(
            value="当前没有运行失败 / No current run failure")
        self.diagnostic_evidence = tk.StringVar(value="证据 / Evidence: —")
        self.diagnostic_suggestions = tk.StringVar(
            value="诊断只显示证据，不会自动修改参数 / Diagnostics never change parameters")
        self._diagnostic_text_value = ""
        self._diagnostic_expanded = False
        self.imported = None
        self.input_summary = None
        self.chart = None
        self.chart_canvases = []
        self._events = queue.Queue()
        self._generation = 0
        self._cancel = None
        self._job_control = None
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
        self.history_search.trace_add("write", self._history_filter_changed)
        self.history_status_filter.trace_add("write", self._history_filter_changed)
        self.history_mode_filter.trace_add("write", self._history_filter_changed)
        self.selected_preset.trace_add("write", self._preset_selection_changed)
        self.optimization_mode.trace_add("write", self._policy_changed)
        self.peak_cap.trace_add("write", self._policy_changed)
        for value in self.device_values.values():
            value.trace_add("write", self._device_changed)
        self._reload_presets_at_startup()
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
            text=("CSV 仅替换负荷、光伏、电价和时间序列；电网、电池及 SOC 默认值来自合成设备场景，"
                  "可在下方修改；"
                  f"桌面最多 {MAX_INTERACTIVE_PERIODS} 时段。 / CSV replaces load, PV, price and "
                  "timestamps only; grid, battery and SOC defaults come from the synthetic "
                  f"equipment scenario; desktop limit: {MAX_INTERACTIVE_PERIODS} periods."),
            style="Sub.TLabel", wraplength=980)
        self.import_scope_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.import_scope_label.bind(
            "<Configure>", lambda event: self.import_scope_label.configure(
                wraplength=max(120, event.width - 12)))

        devices = ttk.LabelFrame(outer, text="设备参数 / Device parameters", padding=(12, 8))
        devices.pack(fill="x", pady=(12, 0))
        labels = {
            "grid_max_kw": "电网最大购电 / Grid max (kW)",
            "capacity_kwh": "电池容量 / Capacity (kWh)",
            "max_charge_kw": "最大充电 / Max charge (kW)",
            "max_discharge_kw": "最大放电 / Max discharge (kW)",
            "initial_kwh": "初始 SOC / Initial SOC (kWh)",
            "terminal_kwh": "终端 SOC / Terminal SOC (kWh)",
            "charge_efficiency": "充电效率 / Charge efficiency",
            "discharge_efficiency": "放电效率 / Discharge efficiency",
        }
        display_order = ("grid_max_kw", "capacity_kwh", "max_charge_kw",
                         "max_discharge_kw", "initial_kwh", "terminal_kwh",
                         "charge_efficiency", "discharge_efficiency")
        self.device_entries = {}
        for index, field in enumerate(display_order):
            cell = ttk.Frame(devices)
            cell.grid(row=index // 4, column=index % 4, sticky="ew",
                      padx=(0 if index % 4 == 0 else 12, 0), pady=(0, 7))
            ttk.Label(cell, text=labels[field]).pack(anchor="w")
            entry = ttk.Entry(cell, textvariable=self.device_values[field], width=16)
            entry.pack(fill="x", pady=(3, 0))
            self.device_entries[field] = entry
        for column in range(4):
            devices.columnconfigure(column, weight=1)
        self.device_source_label = ttk.Label(
            devices, textvariable=self.device_source, style="Sub.TLabel")
        self.device_source_label.grid(row=2, column=0, columnspan=4, sticky="w")

        preset_area = ttk.Frame(devices)
        preset_area.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(preset_area, text="场景预设 / Scenario preset").grid(
            row=0, column=0, sticky="w")
        self.preset_combo = ttk.Combobox(
            preset_area, textvariable=self.selected_preset, state="readonly", width=28)
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        preset_actions = ttk.Frame(preset_area)
        preset_actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.save_preset_button = ttk.Button(
            preset_actions, text="保存为 / Save as", command=self._save_preset_as)
        self.save_preset_button.pack(side="left")
        self.load_preset_button = ttk.Button(
            preset_actions, text="加载 / Load", command=self._load_selected_preset,
            state="disabled")
        self.load_preset_button.pack(side="left", padx=(6, 0))
        self.update_preset_button = ttk.Button(
            preset_actions, text="更新 / Update", command=self._update_selected_preset,
            state="disabled")
        self.update_preset_button.pack(side="left", padx=(6, 0))
        self.delete_preset_button = ttk.Button(
            preset_actions, text="删除 / Delete", command=self._delete_selected_preset,
            state="disabled")
        self.delete_preset_button.pack(side="left", padx=(6, 0))
        preset_area.columnconfigure(1, weight=1)
        ttk.Label(devices, textvariable=self.preset_notice, style="Sub.TLabel").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(5, 0))

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

        diagnostic = ttk.LabelFrame(
            outer, text="运行诊断 / Run diagnosis", padding=(12, 8))
        diagnostic.pack(fill="x", pady=(8, 4))
        diagnostic_header = ttk.Frame(diagnostic)
        diagnostic_header.pack(fill="x")
        ttk.Label(diagnostic_header, textvariable=self.diagnostic_category).pack(
            side="left", anchor="w")
        ttk.Label(diagnostic_header, textvariable=self.diagnostic_stage,
                  style="Sub.TLabel").pack(side="left", padx=(16, 0), anchor="w")
        self.copy_diagnostic_button = ttk.Button(
            diagnostic_header, text="复制详情 / Copy details",
            command=self._copy_diagnostic_details, state="disabled")
        self.copy_diagnostic_button.pack(side="right")
        self.diagnostic_details_button = ttk.Button(
            diagnostic_header, text="显示详情 / Show details",
            command=self._toggle_diagnostic_details, state="disabled")
        self.diagnostic_details_button.pack(side="right", padx=(0, 8))
        self.diagnostic_summary_label = ttk.Label(
            diagnostic, textvariable=self.diagnostic_summary, wraplength=960)
        self.diagnostic_summary_label.pack(anchor="w", fill="x", pady=(6, 0))
        self.diagnostic_evidence_label = ttk.Label(
            diagnostic, textvariable=self.diagnostic_evidence,
            style="Sub.TLabel", wraplength=960)
        self.diagnostic_evidence_label.pack(anchor="w", fill="x", pady=(3, 0))
        self.diagnostic_suggestions_label = ttk.Label(
            diagnostic, textvariable=self.diagnostic_suggestions,
            style="Sub.TLabel", wraplength=960)
        self.diagnostic_suggestions_label.pack(anchor="w", fill="x", pady=(3, 0))
        self.diagnostic_details_frame = ttk.Frame(diagnostic)
        self.diagnostic_details = tk.Text(
            self.diagnostic_details_frame, height=8, wrap="word", bg=WHITE, fg=INK,
            relief="flat", padx=10, pady=8, takefocus=1)
        self.diagnostic_details.pack(side="left", fill="both", expand=True)
        diagnostic_scroll = ttk.Scrollbar(
            self.diagnostic_details_frame, orient="vertical",
            command=self.diagnostic_details.yview)
        diagnostic_scroll.pack(side="right", fill="y")
        self.diagnostic_details.configure(
            yscrollcommand=diagnostic_scroll.set, state="disabled")

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
        ttk.Label(history, text="搜索运行标识 / Search run ID").grid(
            row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(history, text="状态 / Status").grid(row=4, column=3, sticky="w")
        ttk.Label(history, text="优化模式 / Mode").grid(row=4, column=4, sticky="w")
        self.history_search_entry = ttk.Entry(history, textvariable=self.history_search)
        self.history_search_entry.grid(row=5, column=0, columnspan=3, sticky="ew", pady=5)
        self.history_status_combo = ttk.Combobox(
            history, textvariable=self.history_status_filter, state="readonly",
            values=tuple(HISTORY_STATUS_FILTERS))
        self.history_status_combo.grid(row=5, column=3, sticky="ew", padx=(8, 0), pady=5)
        self.history_mode_combo = ttk.Combobox(
            history, textvariable=self.history_mode_filter, state="readonly",
            values=tuple(HISTORY_MODE_FILTERS))
        self.history_mode_combo.grid(row=5, column=4, sticky="ew", padx=(8, 0), pady=5)
        ttk.Label(history, textvariable=self.history_filter_notice, style="Sub.TLabel").grid(
            row=6, column=0, columnspan=4, sticky="w")
        self.clear_history_filters_button = ttk.Button(
            history, text="清除筛选 / Clear filters", command=self._clear_history_filters)
        self.clear_history_filters_button.grid(row=6, column=4, padx=(8, 0))
        history_table_frame = ttk.Frame(history)
        history_table_frame.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=5)
        self.history_table = ttk.Treeview(
            history_table_frame, columns=tuple(column[0] for column in HISTORY_TABLE_COLUMNS),
            show="headings", height=5, selectmode="browse", takefocus=1)
        for column, heading, sort_key, width in HISTORY_TABLE_COLUMNS:
            self.history_table.heading(
                column, text=heading,
                command=lambda key=sort_key: self._sort_history(key))
            self.history_table.column(
                column, width=width, minwidth=90,
                anchor="w" if column in ("name", "mode") else "center")
        self.history_table.pack(side="left", fill="both", expand=True)
        history_scroll = ttk.Scrollbar(
            history_table_frame, orient="vertical", command=self.history_table.yview)
        history_scroll.pack(side="right", fill="y")
        self.history_table.configure(yscrollcommand=history_scroll.set)
        self.load_button = ttk.Button(history, text="读取所选 / Load selected",
                                      command=self._load_history, state="disabled")
        self.load_button.grid(row=7, column=4, padx=(8, 0), sticky="n")
        ttk.Label(
            history, textvariable=self.history_selection_detail, style="Sub.TLabel",
            wraplength=960).grid(row=8, column=0, columnspan=5, sticky="w", pady=(0, 4))
        ttk.Label(history, text="历史运行指标预览 / Historical run metrics preview").grid(
            row=9, column=0, columnspan=4, sticky="w", pady=(8, 2))
        self.preview_button = ttk.Button(
            history, text="预览指标 / Preview Metrics",
            command=self._preview_history, state="disabled")
        self.preview_button.grid(row=9, column=4, padx=(8, 0))
        ttk.Label(history, textvariable=self.history_preview_status, style="Sub.TLabel").grid(
            row=10, column=0, columnspan=5, sticky="w")
        ttk.Label(
            history, textvariable=self.history_preview_context, style="Sub.TLabel",
            wraplength=960).grid(row=11, column=0, columnspan=5, sticky="w", pady=(2, 4))
        self.preview_table = ttk.Treeview(
            history, columns=("metric", "value"), show="headings", height=3,
            selectmode="none", takefocus=1)
        self.preview_table.heading("metric", text="指标 / Metric")
        self.preview_table.heading("value", text="值 / Value")
        self.preview_table.column("metric", width=260, minwidth=170, anchor="w")
        self.preview_table.column("value", width=220, minwidth=120, anchor="e")
        for key, label in (("cost_cny", "电费 / Cost"),
                           ("grid_energy_kwh", "购电量 / Grid energy"),
                           ("peak_grid_kw", "峰值购电功率 / Grid peak")):
            self.preview_table.insert("", "end", iid=key, values=(label, "—"))
        self.preview_table.grid(row=12, column=0, columnspan=5, sticky="ew")
        history.columnconfigure(0, weight=1)
        history.columnconfigure(1, weight=1)
        history.columnconfigure(2, weight=1)
        history.columnconfigure(3, weight=1)
        self.history_table.bind("<<TreeviewSelect>>", self._select_history)

        ttk.Label(history, text="历史运行公平对比 / Fair historical comparison").grid(
            row=13, column=0, columnspan=5, sticky="w", pady=(10, 2))
        ttk.Label(history, text="运行 A / Run A").grid(row=14, column=0, columnspan=2,
                                                       sticky="w")
        ttk.Label(history, text="运行 B / Run B").grid(row=14, column=2, columnspan=2,
                                                       sticky="w", padx=(8, 0))
        self.compare_a_combo = ttk.Combobox(
            history, textvariable=self.selected_compare_a, state="readonly", values=())
        self.compare_a_combo.grid(row=15, column=0, columnspan=2, sticky="ew", pady=5)
        self.compare_b_combo = ttk.Combobox(
            history, textvariable=self.selected_compare_b, state="readonly", values=())
        self.compare_b_combo.grid(row=15, column=2, columnspan=2, sticky="ew",
                                  padx=(8, 0), pady=5)
        self.compare_button = ttk.Button(
            history, text="公平对比 / Compare", command=self._compare_history,
            state="disabled")
        self.compare_button.grid(row=15, column=4, padx=(8, 0))
        self.compare_a_combo.bind("<<ComboboxSelected>>", self._comparison_selection_changed)
        self.compare_b_combo.bind("<<ComboboxSelected>>", self._comparison_selection_changed)
        ttk.Label(history, textvariable=self.comparison_status, style="Sub.TLabel",
                  wraplength=960).grid(row=16, column=0, columnspan=5, sticky="w")
        ttk.Label(history, textvariable=self.comparison_context, style="Sub.TLabel",
                  wraplength=960).grid(row=17, column=0, columnspan=5, sticky="w",
                                       pady=(2, 5))
        columns = ("metric", "run_a", "run_b", "delta")
        self.comparison_table = ttk.Treeview(
            history, columns=columns, show="headings", height=3,
            selectmode="none", takefocus=1)
        for column, heading, width in (
                ("metric", "指标 / Metric", 210),
                ("run_a", "运行 A / Run A", 170),
                ("run_b", "运行 B / Run B", 170),
                ("delta", "B − A", 170)):
            self.comparison_table.heading(column, text=heading)
            self.comparison_table.column(column, width=width, minwidth=110,
                                         anchor="w" if column == "metric" else "e")
        for key, label in (("cost_cny", "购电费用 / Cost"),
                           ("grid_energy_kwh", "购电量 / Grid energy"),
                           ("peak_grid_kw", "购电峰值 / Grid peak")):
            self.comparison_table.insert("", "end", iid=key,
                                         values=(label, "—", "—", "—"))
        self.comparison_table.grid(row=18, column=0, columnspan=5, sticky="ew")
        report_actions = ttk.Frame(history)
        report_actions.grid(row=19, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.export_run_button = ttk.Button(
            report_actions, text="导出所选运行报告 / Export selected run",
            command=self._export_selected_run, state="disabled")
        self.export_run_button.pack(side="left")
        self.export_comparison_button = ttk.Button(
            report_actions, text="导出 A/B 报告 / Export A/B report",
            command=self._export_history_comparison, state="disabled")
        self.export_comparison_button.pack(side="left", padx=(8, 0))

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

    def _diagnostic_text(self, message):
        self.diagnostic_details.configure(state="normal")
        self.diagnostic_details.delete("1.0", "end")
        self.diagnostic_details.insert("1.0", message)
        self.diagnostic_details.configure(state="disabled")

    def _clear_diagnostic(self):
        self.current_diagnostic = None
        self._diagnostic_text_value = ""
        self.diagnostic_category.set("无活动诊断 / No active diagnosis")
        self.diagnostic_stage.set("阶段 / Stage: —")
        self.diagnostic_summary.set("当前没有运行失败 / No current run failure")
        self.diagnostic_evidence.set("证据 / Evidence: —")
        self.diagnostic_suggestions.set(
            "诊断只显示证据，不会自动修改参数 / Diagnostics never change parameters")
        self._diagnostic_text("")
        self.diagnostic_details_button.configure(
            text="显示详情 / Show details", state="disabled")
        self.copy_diagnostic_button.configure(state="disabled")
        if self._diagnostic_expanded:
            self.diagnostic_details_frame.pack_forget()
            self._diagnostic_expanded = False

    def _show_diagnostic(self, diagnostic: RunDiagnostic):
        self.current_diagnostic = diagnostic
        self.diagnostic_category.set(
            f"{diagnostic.category.value} — "
            f"{DIAGNOSTIC_CATEGORY_LABELS[diagnostic.category]}")
        self.diagnostic_stage.set(
            f"阶段 / Stage: {DIAGNOSTIC_STAGE_LABELS[diagnostic.stage]}")
        summary = diagnostic.summary
        if diagnostic.category is FailureCategory.UNKNOWN:
            summary += ("；证据不足，无法确定根因 / "
                        "Insufficient evidence to determine the root cause")
        self.diagnostic_summary.set(summary)
        compact_evidence = []
        if diagnostic.solver_status is not None:
            compact_evidence.append(f"solver_status={diagnostic.solver_status}")
        if diagnostic.termination_condition is not None:
            compact_evidence.append(
                f"termination_condition={diagnostic.termination_condition}")
        if not compact_evidence:
            compact_evidence.extend(diagnostic.evidence[:2])
        self.diagnostic_evidence.set(
            "证据 / Evidence: " + (" | ".join(compact_evidence) or "—"))
        self.diagnostic_suggestions.set(
            "建议 / Suggested checks: " + " | ".join(diagnostic.suggestions))
        self._diagnostic_text_value = diagnostic_details_text(diagnostic)
        self._diagnostic_text(self._diagnostic_text_value)
        self.diagnostic_details_button.configure(state="normal")
        self.copy_diagnostic_button.configure(state="normal")

    def _toggle_diagnostic_details(self):
        if self.current_diagnostic is None:
            return
        if self._diagnostic_expanded:
            self.diagnostic_details_frame.pack_forget()
            self.diagnostic_details_button.configure(text="显示详情 / Show details")
            self._diagnostic_expanded = False
        else:
            self.diagnostic_details_frame.pack(fill="both", expand=True, pady=(8, 0))
            self.diagnostic_details_button.configure(text="隐藏详情 / Hide details")
            self._diagnostic_expanded = True

    def _copy_diagnostic_details(self):
        if self.current_diagnostic is None or not self._diagnostic_text_value:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._diagnostic_text_value)
            self.root.update_idletasks()
        except tk.TclError as exc:
            self.status.set(
                "复制诊断失败；诊断仍保留 / Copy diagnosis failed; diagnosis retained")
            self._append_log(f"TclError copying diagnosis: {exc}")
            return
        self.status.set("诊断详情已复制 / Diagnosis details copied")

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

    def _clear_history_preview(self):
        self.history_preview = None
        self.history_preview_status.set("尚未预览 / No verified preview")
        self.history_preview_context.set(
            "选择有效运行并主动预览 / Select a valid run and preview")
        for key, label in (("cost_cny", "电费 / Cost"),
                           ("grid_energy_kwh", "购电量 / Grid energy"),
                           ("peak_grid_kw", "峰值购电功率 / Grid peak")):
            self.preview_table.item(key, values=(label, "—"))
        self.preview_button.configure(state="disabled")

    def _clear_history_comparison(self, *, clear_selection=True):
        self.history_comparison = None
        self.comparison_status.set("尚未比较 / No verified comparison yet")
        self.comparison_context.set("选择两个有效运行 / Select two valid runs")
        for key, label in (("cost_cny", "购电费用 / Cost"),
                           ("grid_energy_kwh", "购电量 / Grid energy"),
                           ("peak_grid_kw", "购电峰值 / Grid peak")):
            self.comparison_table.item(key, values=(label, "—", "—", "—"))
        if clear_selection:
            self.selected_compare_a.set("")
            self.selected_compare_b.set("")
        self.compare_button.configure(state="disabled")
        self.export_comparison_button.configure(state="disabled")

    def _invalidate(self, *_):
        if self._closing:
            return
        self._clear_diagnostic()
        self._clear_history_preview()
        if self._cancel is not None:
            self._cancel.set()
        self._stop_progress()
        self._generation += 1
        self._cancel = None
        self._job_control = None
        self._cancel_requested = False
        self._active_kind = None
        self.imported = None
        self.input_summary = None
        self.check_button.configure(state="normal")
        self.run_button.configure(state="disabled")
        self.load_button.configure(state="disabled")
        self.export_run_button.configure(state="disabled")
        self.refresh_button.configure(state="normal")
        self.selected_run.set("")
        self._clear_history_comparison()
        self.cancel_button.configure(state="disabled")
        self.status.set("输入已改变，请重新检查 / Input changed; check again")
        self._clear_results()
        self._quality_text("数据与运行日志 / Data and run log\n等待检查 / Awaiting check")

    def _invalidate_root(self, *_):
        self._invalidate()
        self.storage_notice.set("目录未保存 / Directory not saved")
        self.compare_a_combo.configure(values=())
        self.compare_b_combo.configure(values=())
        self._history_entries.clear()
        self._apply_history_filters()

    def _policy_changed(self, *_):
        if self._updating_policy or self._closing:
            return
        self._configuration_changed(
            "优化模式已改变；请重新运行 / Optimization mode changed; run again")

    def _device_changed(self, *_):
        if self._updating_devices or self._closing:
            return
        self._configuration_changed(
            "设备参数已改变；旧结果已失效 / "
            "Device parameters changed; prior results invalidated")

    def _configuration_changed(self, message):
        """Invalidate derived state once while preserving checked CSV input."""
        self._clear_diagnostic()
        self._clear_history_preview()
        if self._cancel is not None:
            self._cancel.set()
        self._generation += 1
        self._cancel = None
        self._job_control = None
        self._cancel_requested = False
        self._active_kind = None
        self._stop_progress()
        selection = self.history_table.selection()
        if selection:
            self.history_table.selection_remove(*selection)
        self.history_table.focus("")
        self.selected_run.set("")
        self.history_selection_detail.set(
            "选择运行查看状态与详情 / Select a run to view status and details")
        self.load_button.configure(state="disabled")
        self.export_run_button.configure(state="disabled")
        self._clear_results()
        self._clear_history_comparison()
        self._update_device_source()
        self.status.set(message)
        if self.imported is not None and self.imported.report.ready and \
                len(self.imported.records) <= MAX_INTERACTIVE_PERIODS:
            self.run_button.configure(state="normal")
        self._sync_policy_controls()

    def _update_device_source(self):
        try:
            current = self._device_parameters_value()
        except DashboardError:
            self.device_source.set("设备参数无效 / Invalid device parameters")
        else:
            self.device_source.set(
                "默认合成设备参数 / Default synthetic equipment parameters"
                if current == self.default_devices else
                "用户自定义设备参数 / Custom device parameters")

    def _sync_policy_controls(self):
        busy = self._active_kind is not None or self._closing
        radio_state = "disabled" if busy else "normal"
        self.cost_mode_button.configure(state=radio_state)
        self.peak_mode_button.configure(state=radio_state)
        entry_state = "normal" if not busy and self.optimization_mode.get() == "peak_cap" \
            else "disabled"
        self.peak_cap_entry.configure(state=entry_state)
        device_state = "disabled" if busy else "normal"
        for entry in self.device_entries.values():
            entry.configure(state=device_state)
        comparison_state = "disabled" if busy else "readonly"
        self.compare_a_combo.configure(state=comparison_state)
        self.compare_b_combo.configure(state=comparison_state)
        self._select_comparison()
        self._sync_preset_controls()

    def _sync_preset_controls(self):
        busy = self._active_kind is not None or self._closing
        selected = self._selected_preset_value() is not None
        self.preset_combo.configure(state="disabled" if busy else "readonly")
        self.save_preset_button.configure(state="disabled" if busy else "normal")
        selected_state = "normal" if selected and not busy else "disabled"
        self.load_preset_button.configure(state=selected_state)
        self.update_preset_button.configure(state=selected_state)
        self.delete_preset_button.configure(state=selected_state)

    def _preset_selection_changed(self, *_):
        if not self._closing:
            self._sync_preset_controls()

    def _selected_preset_value(self) -> ScenarioPreset | None:
        selected = self.selected_preset.get()
        return next((item for item in self.presets if item.name == selected), None)

    def _set_preset_options(self, presets, *, selected_id=None):
        self.presets = tuple(presets)
        self.preset_combo.configure(values=tuple(item.name for item in self.presets))
        selected = next(
            (item for item in self.presets if item.preset_id == selected_id), None)
        if selected is None and self.presets:
            current = self.selected_preset.get()
            selected = next((item for item in self.presets if item.name == current), None)
        if selected is None and self.presets:
            selected = self.presets[0]
        self.selected_preset.set(selected.name if selected is not None else "")
        count = len(self.presets)
        self.preset_notice.set(
            f"已加载 {count} 个本地预设 / {count} local preset(s) loaded"
            if count else "尚无预设 / No presets")
        self._sync_preset_controls()

    def _preset_error(self, action, error):
        self.status.set(f"{action}；详见日志 / See log")
        self._append_log(f"ScenarioPresetError: {error}")

    def _reload_presets_at_startup(self):
        try:
            presets = load_presets(self.preset_path)
        except ScenarioPresetError as exc:
            self._preset_error("预设读取失败 / Preset store unavailable", exc)
            return
        self._set_preset_options(presets)

    def _current_preset_configuration(self):
        devices = self._device_parameters_value()
        mode = self.optimization_mode.get()
        if mode == "cost":
            return devices, OptimizationPolicy("cost")
        if mode == "peak_cap":
            return devices, OptimizationPolicy("peak_cap", self._peak_cap_value())
        raise ScenarioPresetError(f"optimization.mode: unsupported value {mode!r}")

    def _save_preset_as(self):
        if self._active_kind is not None or self._closing:
            return
        try:
            devices, policy = self._current_preset_configuration()
        except (DashboardError, ScenarioPresetError) as exc:
            self._preset_error("预设保存失败 / Preset save failed", exc)
            return
        name = simpledialog.askstring(
            "保存场景预设 / Save scenario preset",
            "预设名称 / Preset name:", parent=self.root)
        if name is None:
            return
        try:
            current = load_presets(self.preset_path)
            updated = create_preset(
                current, name=name, device_parameters=devices, optimization=policy)
            save_presets(self.preset_path, updated)
        except ScenarioPresetError as exc:
            self._preset_error("预设保存失败 / Preset save failed", exc)
            return
        self._set_preset_options(updated, selected_id=updated[-1].preset_id)
        self.status.set("预设已保存 / Preset saved")

    def _load_selected_preset(self):
        if self._active_kind is not None or self._closing:
            return
        selected = self._selected_preset_value()
        if selected is None:
            return
        try:
            current = load_presets(self.preset_path)
            preset = next(
                item for item in current if item.preset_id == selected.preset_id)
        except StopIteration:
            self._preset_error(
                "预设加载失败 / Preset load failed",
                ScenarioPresetError(f"preset id not found: {selected.preset_id}"))
            return
        except ScenarioPresetError as exc:
            self._preset_error("预设加载失败 / Preset load failed", exc)
            return

        previous_devices = {
            field: value.get() for field, value in self.device_values.items()}
        previous_policy = (self.optimization_mode.get(), self.peak_cap.get())
        self._updating_devices = True
        self._updating_policy = True
        try:
            for field in DEVICE_FIELDS:
                self.device_values[field].set(
                    f"{getattr(preset.device_parameters, field):g}")
            self.optimization_mode.set(preset.optimization.mode)
            if preset.optimization.peak_cap_kw is not None:
                self.peak_cap.set(f"{preset.optimization.peak_cap_kw:g}")
        except Exception as exc:
            for field, value in previous_devices.items():
                self.device_values[field].set(value)
            self.optimization_mode.set(previous_policy[0])
            self.peak_cap.set(previous_policy[1])
            self._preset_error("预设加载失败 / Preset load failed", exc)
            return
        finally:
            self._updating_devices = False
            self._updating_policy = False
        self._set_preset_options(current, selected_id=preset.preset_id)
        self._configuration_changed(
            "预设已加载；旧结果已失效 / Preset loaded; prior results invalidated")

    def _update_selected_preset(self):
        if self._active_kind is not None or self._closing:
            return
        selected = self._selected_preset_value()
        if selected is None:
            return
        try:
            devices, policy = self._current_preset_configuration()
            current = load_presets(self.preset_path)
            updated = update_preset(
                current, selected.preset_id,
                device_parameters=devices, optimization=policy)
            save_presets(self.preset_path, updated)
        except (DashboardError, ScenarioPresetError) as exc:
            self._preset_error("预设更新失败 / Preset update failed", exc)
            return
        self._set_preset_options(updated, selected_id=selected.preset_id)
        self.status.set("预设已更新 / Preset updated")

    def _delete_selected_preset(self):
        if self._active_kind is not None or self._closing:
            return
        selected = self._selected_preset_value()
        if selected is None:
            return
        if not messagebox.askyesno(
                "删除场景预设 / Delete scenario preset",
                f"删除“{selected.name}”？此操作不会删除历史运行或报告。\n"
                f"Delete '{selected.name}'? Historical runs and reports are kept.",
                parent=self.root):
            return
        try:
            current = load_presets(self.preset_path)
            updated = delete_preset(current, selected.preset_id)
            save_presets(self.preset_path, updated)
        except ScenarioPresetError as exc:
            self._preset_error("预设删除失败 / Preset delete failed", exc)
            return
        self._set_preset_options(updated)
        self.status.set("预设已删除；历史运行和报告未更改 / "
                        "Preset deleted; runs and reports unchanged")

    def _device_parameters_value(self) -> DeviceParameters:
        try:
            return parse_device_parameters(
                {field: value.get() for field, value in self.device_values.items()})
        except DeviceParameterError as exc:
            raise DashboardError(f"设备参数无效 / Invalid device parameters: {exc}") from exc

    def _set_device_parameters(self, parameters: DeviceParameters) -> None:
        self._updating_devices = True
        try:
            for field in DEVICE_FIELDS:
                self.device_values[field].set(f"{getattr(parameters, field):g}")
        finally:
            self._updating_devices = False
        self._update_device_source()

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
        self._clear_diagnostic()
        self._clear_history_preview()
        self._generation += 1
        generation = self._generation
        self._pending_jobs.add(generation)
        cancel = (ReportCancellation() if kind in ("report_single", "report_compare")
                  else threading.Event())
        control = _JobControl(cancel)
        self._cancel = cancel
        self._job_control = control
        self._cancel_requested = False
        self._active_kind = kind
        self._sync_policy_controls()
        self.check_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.load_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.compare_button.configure(state="disabled")
        self.export_run_button.configure(state="disabled")
        self.export_comparison_button.configure(state="disabled")
        self.save_root_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_button.focus_set()
        self.progress.pack(fill="x", pady=(2, 0))
        self.progress.start(15)

        def worker():
            try:
                value = operation(cancel)
                item = (generation, kind, value, None)
            except Exception as exc:
                item = (generation, kind, None, exc)
            control.complete(lambda: self._events.put(item))

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
            self._job_control = None
            self._cancel_requested = False
            self._active_kind = None
            self._sync_policy_controls()
            self.check_button.configure(state="normal")
            self.refresh_button.configure(state="normal")
            self.save_root_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            if cancelled:
                if kind == "history_preview":
                    self._clear_history_preview()
                elif kind == "history_compare":
                    self._clear_history_comparison(clear_selection=False)
                elif kind not in ("report_single", "report_compare"):
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
                    elif kind == "history_compare":
                        self._show_history_comparison(value)
                    elif kind == "history_preview":
                        self._show_history_preview(value)
                    elif kind in ("report_single", "report_compare"):
                        self._show_report_export(value)
                    else:
                        self._show_result(value)
                except Exception as exc:
                    self._show_failure(kind, exc)
            if focus_on_cancel:
                (self.run_button if kind in ("run", "history_load") and self.imported is not None
                 else self.check_button).focus_set()
            self._select_history()
            self._select_comparison()
        self._poll_id = self.root.after(50, self._poll)

    def _cancel_job(self, _event=None):
        if self._closing or self._cancel is None or self._job_control is None or \
                self._cancel_requested:
            return
        accepted = self._job_control.request_cancel()
        if accepted is False:
            self.cancel_button.configure(state="disabled")
            self.status.set(
                "报告已完成最终发布；取消未生效 / Report publication already committed"
                if self._active_kind in ("report_single", "report_compare") else
                "任务已完成；取消未生效 / Task already completed; cancellation ignored")
            return
        self._cancel_requested = True
        self.cancel_button.configure(state="disabled")
        self.status.set("正在取消 / Cancelling…")

    def _stop_progress(self):
        self.progress.stop()
        self.progress.pack_forget()

    def _show_failure(self, kind, error):
        if kind in ("report_single", "report_compare"):
            self.status.set("报告导出失败；详见日志 / Report export failed; see log")
            self._append_log(f"{type(error).__name__}: {error}")
            return
        if kind == "history_compare":
            self._clear_history_comparison(clear_selection=False)
            self.status.set("历史对比失败；详见日志 / History comparison failed; see log")
            self._append_log(f"{type(error).__name__}: {error}")
            return
        if kind == "history_preview":
            self._clear_history_preview()
            self.status.set("指标预览失败；详见日志 / Metrics preview failed; see log")
            self._append_log(f"{type(error).__name__}: {error}")
            return
        self._clear_results()
        self.selected_run.set("")
        self.export_run_button.configure(state="disabled")
        if kind in ("run", "history_load") and self.imported is not None:
            self.run_button.configure(state="normal")
        self.status.set("运行失败；详见日志 / Run failed; see log" if kind in ("run", "history_load")
                        else "检查失败；详见日志 / Check failed; see log")
        self._append_log(f"{type(error).__name__}: {error}")
        self._append_run_diagnostic(error)

    def _append_run_diagnostic(self, error, *, stage_hint=FailureStage.CONTROL):
        diagnostic = getattr(error, "diagnostic", None) or diagnose_failure(
            error, stage_hint=stage_hint)
        self._show_diagnostic(diagnostic)
        self._append_log(
            f"运行诊断 / Run diagnosis: {diagnostic.category.value}\n"
            f"阶段 / Stage: {diagnostic.stage.value}\n"
            f"说明 / Summary: {diagnostic.summary}\n"
            f"证据 / Evidence: {' | '.join(diagnostic.evidence)}\n"
            f"建议 / Suggested checks: {' | '.join(diagnostic.suggestions)}")

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
            self.preview_button.configure(state="disabled")
            self.compare_button.configure(state="disabled")
            self.export_run_button.configure(state="disabled")
            self.export_comparison_button.configure(state="disabled")
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

    def _history_filter_changed(self, *_):
        if not self._closing:
            self._apply_history_filters()

    def _apply_history_filters(self):
        entries = filter_run_history(
            self._history_entries.values(),
            query=self.history_search.get(),
            status=HISTORY_STATUS_FILTERS[self.history_status_filter.get()],
            optimization_mode=HISTORY_MODE_FILTERS[self.history_mode_filter.get()],
        )
        entries = sort_run_history(
            entries, sort_key=self._history_sort_key,
            descending=self._history_sort_descending)
        selected_name = self._selected_history_name(self.selected_run.get())
        self.history_table.delete(*self.history_table.get_children())
        for entry in entries:
            self.history_table.insert(
                "", "end", iid=entry.name, values=self._history_row(entry))
        visible_names = {entry.name for entry in entries}
        if selected_name in visible_names:
            self.history_table.selection_set(selected_name)
            self.history_table.see(selected_name)
        else:
            self.selected_run.set("")
            self.history_selection_detail.set(
                "选择运行查看状态与详情 / Select a run to view status and details")
        self._update_history_headings()
        total = len(self._history_entries)
        if entries:
            self.history_filter_notice.set(
                f"显示 {len(entries)} / {total} 条运行 / Showing {len(entries)} of {total} runs")
        elif total:
            self.history_filter_notice.set("没有匹配运行 / No matching runs")
        else:
            self.history_filter_notice.set("没有运行记录 / No runs found")
        self._select_history()

    def _sort_history(self, sort_key):
        if sort_key == self._history_sort_key:
            self._history_sort_descending = not self._history_sort_descending
        else:
            self._history_sort_key = sort_key
            self._history_sort_descending = False
        self._apply_history_filters()

    def _update_history_headings(self):
        for column, heading, sort_key, _width in HISTORY_TABLE_COLUMNS:
            indicator = ""
            if sort_key == self._history_sort_key:
                indicator = " ▼" if self._history_sort_descending else " ▲"
            self.history_table.heading(column, text=heading + indicator)

    @staticmethod
    def _history_row(entry):
        if entry.optimization_mode == "cost":
            mode = HISTORY_MODE_COST
        elif entry.optimization_mode == "peak_cap":
            mode = HISTORY_MODE_PEAK_CAP
        else:
            mode = "未知 / Unknown"
        cap = f"{entry.peak_cap_kw:g} kW" if entry.peak_cap_kw is not None else "—"
        return entry.name, entry.status, mode, cap

    def _clear_history_filters(self):
        self.history_search.set("")
        self.history_status_filter.set(HISTORY_FILTER_ALL)
        self.history_mode_filter.set(HISTORY_FILTER_ALL)
        self._apply_history_filters()

    def _refresh_history(self):
        if self._closing or self._active_kind is not None:
            return
        runs_root = Path(self.runs_root.get())
        self.status.set("正在核验运行历史 / Verifying run history…")
        self._start_job("history", lambda cancel: list_run_history(runs_root, cancel_event=cancel))

    def _show_history(self, entries):
        self._history_entries = {entry.name: entry for entry in entries}
        labels = tuple(self._history_label(entry) for entry in entries)
        self.compare_a_combo.configure(values=labels)
        self.compare_b_combo.configure(values=labels)
        self.selected_run.set("")
        self._apply_history_filters()
        self._clear_history_comparison()
        counts = {status: sum(entry.status == status for entry in entries)
                  for status in ("VALID", "INCOMPLETE", "DAMAGED")}
        self.status.set("运行历史 / Runs: " + ", ".join(
            f"{status}={count}" for status, count in counts.items()))
        for entry in entries:
            if entry.status != "VALID":
                self._append_log(f"{entry.name} [{entry.status}]: {entry.detail}")

    def _select_history(self, _event=None):
        table_selection = self.history_table.selection()
        if table_selection:
            name = table_selection[0]
            table_entry = self._history_entries.get(name)
            if table_entry is not None:
                self.selected_run.set(self._history_label(table_entry))
        else:
            selected = self.selected_run.get()
            name = selected.split(" [", 1)[0] if " [" in selected else ""
        entry = self._history_entries.get(name)
        if self.history_preview is not None and self.history_preview.run_name != name:
            self._clear_history_preview()
        if entry is None:
            self.history_selection_detail.set(
                "选择运行查看状态与详情 / Select a run to view status and details")
        elif entry.status == "VALID":
            self.history_selection_detail.set(
                f"{entry.name} [VALID]：完整性校验通过 / Integrity verified")
        else:
            self._clear_history_preview()
            self.history_selection_detail.set(
                f"{entry.name} [{entry.status}]: {entry.detail}")
        available = (not self._closing and self._active_kind is None and
                     self.imported is not None and self.imported.report.ready and
                      entry is not None and entry.status == "VALID")
        self.load_button.configure(state="normal" if available else "disabled")
        self.preview_button.configure(state="normal" if available else "disabled")
        export_available = available
        self.export_run_button.configure(
            state="normal" if export_available else "disabled")

    def _preview_history(self):
        if self._closing or self._active_kind is not None:
            return
        name = self._selected_history_name(self.selected_run.get())
        entry = self._history_entries.get(name)
        if self.imported is None or not self.imported.report.ready or \
                entry is None or entry.status != "VALID":
            self._clear_history_preview()
            self.status.set(
                "请选择有效运行并检查当前输入 / Select a valid run and check current input")
            return
        imported = self.imported
        runs_root = Path(self.runs_root.get())
        self._start_job("history_preview", lambda cancel: preview_verified_run(
            imported, name, runs_root, cancel_event=cancel))
        self.history_preview_status.set("正在核验并重算指标 / Verifying and recalculating metrics…")
        self.status.set("正在预览历史指标 / Previewing historical metrics…")

    def _show_history_preview(self, preview):
        name = self._selected_history_name(self.selected_run.get())
        entry = self._history_entries.get(name)
        if preview.run_name != name or entry is None or entry.status != "VALID":
            return
        units = dict(preview.units)
        values = {
            "cost_cny": preview.metrics.cost_cny,
            "grid_energy_kwh": preview.metrics.grid_energy_kwh,
            "peak_grid_kw": preview.metrics.peak_grid_kw,
        }
        labels = {
            "cost_cny": "电费 / Cost",
            "grid_energy_kwh": "购电量 / Grid energy",
            "peak_grid_kw": "峰值购电功率 / Grid peak",
        }
        for key in ("cost_cny", "grid_energy_kwh", "peak_grid_kw"):
            self.preview_table.item(
                key, values=(labels[key], f"{values[key]:,.2f} {units[key]}"))
        mode = (HISTORY_MODE_PEAK_CAP if preview.optimization_mode == "peak_cap"
                else HISTORY_MODE_COST)
        cap = f"{preview.peak_cap_kw:g} kW" if preview.peak_cap_kw is not None else "—"
        self.history_preview = preview
        self.history_preview_status.set(
            f"完整性 / Integrity: {preview.integrity_status}；当前 CSV 已对齐 / Input aligned")
        self.history_preview_context.set(
            f"模式 / Mode: {mode}  |  峰值上限 / Peak cap: {cap}")
        self.status.set("历史指标预览完成 / Historical metrics preview ready")

    @staticmethod
    def _selected_history_name(value):
        return value.split(" [", 1)[0] if " [" in value else ""

    def _comparison_selection_changed(self, _event=None):
        self._clear_history_comparison(clear_selection=False)
        self._select_comparison()

    def _select_comparison(self, _event=None):
        name_a = self._selected_history_name(self.selected_compare_a.get())
        name_b = self._selected_history_name(self.selected_compare_b.get())
        entry_a = self._history_entries.get(name_a)
        entry_b = self._history_entries.get(name_b)
        available = (not self._closing and self._active_kind is None and
                     self.imported is not None and self.imported.report.ready and
                     name_a != name_b and entry_a is not None and entry_b is not None and
                     entry_a.status == "VALID" and entry_b.status == "VALID")
        self.compare_button.configure(state="normal" if available else "disabled")
        export_available = (available and self.history_comparison is not None and
                            self.history_comparison.run_a.name == name_a and
                            self.history_comparison.run_b.name == name_b)
        self.export_comparison_button.configure(
            state="normal" if export_available else "disabled")

    def _compare_history(self):
        if self._closing or self._active_kind is not None:
            return
        name_a = self._selected_history_name(self.selected_compare_a.get())
        name_b = self._selected_history_name(self.selected_compare_b.get())
        if self.imported is None or not self.imported.report.ready or \
                name_a == name_b or name_a not in self._history_entries or \
                name_b not in self._history_entries:
            self.status.set("请选择两个不同的有效运行 / Select two distinct valid runs")
            return
        imported = self.imported
        runs_root = Path(self.runs_root.get())
        self._clear_history_comparison(clear_selection=False)
        self.status.set("正在核验公平比较条件 / Verifying fair-comparison context…")
        self._start_job("history_compare", lambda cancel: compare_verified_runs(
            imported, name_a, name_b, runs_root, cancel_event=cancel))

    @staticmethod
    def _comparison_policy(run):
        if run.optimization_mode == "peak_cap" and run.peak_cap_kw is not None:
            return f"峰值上限 {run.peak_cap_kw:g} kW / Peak cap"
        return "纯电费 / Cost-only"

    def _show_history_comparison(self, comparison):
        self.history_comparison = comparison
        labels = {
            "cost_cny": "购电费用 / Cost",
            "grid_energy_kwh": "购电量 / Grid energy",
            "peak_grid_kw": "购电峰值 / Grid peak",
        }
        for key, label in labels.items():
            unit = comparison.units[key]
            value_a = getattr(comparison.run_a.metrics, key)
            value_b = getattr(comparison.run_b.metrics, key)
            delta = getattr(comparison.delta_b_minus_a, key)
            self.comparison_table.item(
                key, values=(label, f"{value_a:,.2f} {unit}",
                             f"{value_b:,.2f} {unit}", f"{delta:+,.2f} {unit}"))
        self.comparison_status.set(
            f"完整性 / Integrity: {comparison.integrity_status}  |  "
            f"公平条件 / Fairness: {comparison.fairness_status}  |  "
            "B − A；正负值仅描述对应指标变化 / signs describe each metric only")
        devices = comparison.run_a.device_parameters
        self.comparison_context.set(
            f"A: {self._comparison_policy(comparison.run_a)}  |  "
            f"B: {self._comparison_policy(comparison.run_b)}  |  "
            f"电网 / Grid {devices.grid_max_kw:g} kW; 电池 / Battery "
            f"{devices.capacity_kwh:g} kWh; 充/放 / Charge-discharge "
            f"{devices.max_charge_kw:g}/{devices.max_discharge_kw:g} kW; "
            f"初末 / Initial-terminal {devices.initial_kwh:g}/{devices.terminal_kwh:g} kWh; "
            f"效率 / Efficiency {devices.charge_efficiency:g}/{devices.discharge_efficiency:g}")
        self.status.set("历史运行公平对比通过 / Fair historical comparison verified")
        self._select_comparison()

    @staticmethod
    def _report_target(title, initialfile):
        selected = filedialog.asksaveasfilename(
            title=title, defaultextension=".html",
            filetypes=[("HTML report", "*.html")], initialfile=initialfile,
            confirmoverwrite=False)
        return Path(selected) if selected else None

    def _export_selected_run(self):
        if self._closing or self._active_kind is not None:
            return
        name = self._selected_history_name(self.selected_run.get())
        entry = self._history_entries.get(name)
        if self.imported is None or not self.imported.report.ready or \
                entry is None or entry.status != "VALID":
            self.status.set("请选择有效运行 / Select a valid run")
            return
        target = self._report_target(
            "导出单次运行报告 / Export single-run report", f"gridwise-{name}.html")
        if target is None:
            return
        imported = self.imported
        runs_root = Path(self.runs_root.get())
        self.status.set("正在重新校验并导出报告 / Revalidating and exporting report…")
        self._start_job("report_single", lambda cancel: export_single_run_report(
            target, imported, name, runs_root, cancel_event=cancel))

    def _export_history_comparison(self):
        if self._closing or self._active_kind is not None or self.history_comparison is None:
            return
        name_a = self._selected_history_name(self.selected_compare_a.get())
        name_b = self._selected_history_name(self.selected_compare_b.get())
        if self.imported is None or not self.imported.report.ready or \
                self.history_comparison.run_a.name != name_a or \
                self.history_comparison.run_b.name != name_b:
            self.status.set("请先完成当前 A/B 公平对比 / Complete the current A/B comparison")
            return
        target = self._report_target(
            "导出 A/B 对比报告 / Export A/B report",
            f"gridwise-{name_a}-vs-{name_b}.html")
        if target is None:
            return
        imported = self.imported
        runs_root = Path(self.runs_root.get())
        self.status.set("正在重新校验并导出 A/B 报告 / Revalidating and exporting A/B report…")
        self._start_job("report_compare", lambda cancel: export_comparison_report(
            target, imported, name_a, name_b, runs_root, cancel_event=cancel))

    def _show_report_export(self, path):
        self.status.set(f"报告已导出 / Report exported: {path}")
        self._append_log(f"Report exported: {path}")

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
        self._clear_history_comparison()
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
                self._append_run_diagnostic(exc, stage_hint=FailureStage.INPUT)
                return
        try:
            device_parameters = self._device_parameters_value()
        except DashboardError as exc:
            self._clear_results()
            self.status.set("设备参数无效；详见日志 / Invalid device parameters; see log")
            self._append_log(f"DashboardError: {exc}")
            self._append_run_diagnostic(exc, stage_hint=FailureStage.INPUT)
            return
        imported = self.imported
        self._clear_results()
        self.run_button.configure(state="disabled")
        self.status.set("正在计算与审计 / Solving and auditing…")
        runs_root = Path(self.runs_root.get())
        self._start_job("run", lambda cancel: run_comparison(
            imported, cancel_event=cancel, runs_root=runs_root,
            peak_cap_kw=peak_cap_kw, device_parameters=device_parameters))

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
        self._clear_diagnostic()
        self.chart = result.chart
        self.status.set(result.audit_message)
        if result.device_parameters is not None:
            self._set_device_parameters(result.device_parameters)
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
            labels = tuple(self._history_label(entry) for entry in self._history_entries.values())
            self.compare_a_combo.configure(values=labels)
            self.compare_b_combo.configure(values=labels)
            self.selected_run.set(self._history_label(self._history_entries[result.run_name]))
            self._apply_history_filters()
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
