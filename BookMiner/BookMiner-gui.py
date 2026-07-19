#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
import pickle
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk


BASE_DIR = Path(__file__).resolve().parent
BOOK_MINER_SCRIPT = BASE_DIR / "BookMiner.py"
BOOK_MINER_CPP_EXE = BASE_DIR.parent / "BookMinerCpp" / "BookMinerCpp.exe"
KIF_MANAGER_SCRIPT = BASE_DIR.parent / "KifManager" / "kif-manager.py"
GUI_SETTINGS_PATH = BASE_DIR / "BookMiner-gui.pickle"
THINK_SFENS_COMMAND_PATH = "book/think_sfens.txt"
AUTO_THINK_SFENS_COMMAND_PATH = "book/think_sfens-tmp.txt"
THINK_SFENS_PATH = BASE_DIR / THINK_SFENS_COMMAND_PATH
AUTO_THINK_SFENS_PATH = BASE_DIR / AUTO_THINK_SFENS_COMMAND_PATH
GUI_SETTING_DEFAULTS = {
    "default_eval_diff": "30",
    "default_max_step": "99999",
    "peta_next_eval_diff": "",
    "peta_refutation_eval_diff": "",
    "peta_next_max_step": "",
    "peta_refutation_max_step": "",
    "peta_refutation_eval_refu": "100",
    "peta_next_book_extend_ply": "",
    "peta_refutation_book_extend_ply": "",
    "peta_unsolved_book_extend_ply": "",
    "peta_unsolved_eval_drop_limit": "",
    "peta_unsolved_max_step": "",
    "peta_opponent_eval_diff": "",
    "peta_opponent_max_step": "",
    "peta_opponent_book_extend_ply": "",
    "eval_limit": "400",
    "game_ply_limit": "200",
    "enqueue_book_extend_ply": "6",
    "peta_next_ply_limit": "",
    "peta_refutation_ply_limit": "",
    "peta_unsolved_ply_limit": "",
    "peta_opponent_ply_limit": "",
    "peta_next_eval_limit": "",
    "peta_refutation_eval_limit": "",
    "peta_unsolved_eval_limit": "",
    "peta_opponent_eval_limit": "",
    "auto_step2_peta_next": "1",
    "auto_step2_peta_refutation": "0",
    "auto_step2_peta_unsolved": "0",
    "auto_step2_peta_opponent": "0",
    "auto_enqueue_threshold": "1000",
    "log_view_mode": "2x2",
    "task_list_mode": "1",
    "step2_collapsed": "0",
}
BOOK_PROGRESS_RE = re.compile(r"\[Book(Read|Write)(Start|Progress|Done)\]\s+(\d+)/(\d+|\?)")
TASK_QUEUE_PROGRESS_RE = re.compile(r"\[TaskQueue(Start|Progress|Done)\]\s+(\d+)/(\d+|\?)")
TASK_QUEUE_JOB_STATUS_RE = re.compile(r"\[TaskQueue(Start|Progress|JobDone|Done)\]\s+(\d+)/(\d+|\?)(.*)")
TASK_QUEUE_FIELD_RE = re.compile(r"\b([A-Za-z_]+)=([^\s]+)")
MINING_PROGRESS_RE = re.compile(r"\[MiningProgress\]\s+positions=(\d+)")
STARTUP_STAGE_RE = re.compile(r"\[StartupStage\]\s+stage=(\S+)\s+message=(.*)")
ENGINE_INIT_RE = re.compile(r"\[EngineInit(Start|Progress|Done)\]\s+(\d+)/(\d+)")
ENGINE_READY_RE = re.compile(r"\[EngineReadyProgress\]\s+(\d+)/(\d+)")
BACKUP_STATUS_RE = re.compile(r"\[(BackupServiceStarted|BackupNext|BackupStart|BackupDone)\](.*)")
COMMAND_READY_RE = re.compile(r"\[CommandReady\]")
PETA_COMMAND_DONE_RE = re.compile(r"\[PetaCommandDone\]")
PETA_READ_DONE_RE = re.compile(r"\[PetaReadDone\]")
PETA_NEXT_DONE_RE = re.compile(r"\[PetaNextDone\]")
PETA_REFUTATION_DONE_RE = re.compile(r"\[PetaRefutationDone\]")
PETA_UNSOLVED_DONE_RE = re.compile(r"\[PetaUnsolvedDone\]")
PETA_OPPONENT_DONE_RE = re.compile(r"\[PetaOpponentDone\]")
PETA_MAKEBOOK_START_RE = re.compile(r"start peta_shock makebook", re.IGNORECASE)
PETA_MAKEBOOK_DONE_RE = re.compile(r"\.\.peta_shock makebook has done|peta_shock makebook failed", re.IGNORECASE)
PETA_MAKEBOOK_CONTEXT_RE = re.compile(
    r"^\s*(?:\[[^\]]+\]\s*)?(engine path|source book|peta book|command)\s*=",
    re.IGNORECASE,
)
PETA_MAKEBOOK_LINE_RE = re.compile(
    r"retrograde analysis|read a book db|write a book db|makebook peta_shock",
    re.IGNORECASE,
)
PETA_COMMAND_LOG_MIRROR_RE = re.compile(
    r"start (?:p|pl) command|\.\.(?:p|pl) command has done|"
    r"read peta shocked book|reading the peta_book has done",
    re.IGNORECASE,
)
YANEURAOU_PROGRESS_BAR_RE = re.compile(r"^\s*0%\s+\[.*\]\s+100%\s*$")
STEP_BUTTON_WIDTH = 12
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 720
MIN_WINDOW_WIDTH = 760
MIN_WINDOW_HEIGHT = 520
WINDOW_SCREEN_MARGIN = 48
LOG_MAX_LINES = 1000
LOG_TRIM_THRESHOLD = 1200
MINING_STATS_SAMPLE_INTERVAL_MS = 60 * 1000
MINING_STATS_WINDOW_SECONDS = 60 * 60
AUTO_ENQUEUE_IDLE = "idle"
AUTO_ENQUEUE_PETA = "peta_shock"
AUTO_ENQUEUE_NEXT = "peta_next"
AUTO_ENQUEUE_ENQUEUE = "enqueue"
AUTO_STEP2_PETA_NEXT = "peta_next"
AUTO_STEP2_PETA_REFUTATION = "peta_refutation"
AUTO_STEP2_PETA_UNSOLVED = "peta_unsolved"
AUTO_STEP2_PETA_OPPONENT = "peta_opponent"
AUTO_STEP2_ORDER = [
    AUTO_STEP2_PETA_NEXT,
    AUTO_STEP2_PETA_REFUTATION,
    AUTO_STEP2_PETA_UNSOLVED,
    AUTO_STEP2_PETA_OPPONENT,
]
LOG_PANES = [
    ("other", "コマンドログ", "コマンド", 8),
    ("task", "タスク状況ログ", "タスク状況", 7),
    ("search", "探索ログ", "探索", 10),
    ("peta", "petaログ", "peta", 7),
]
LOG_GRID_HEIGHT = 8
LOG_VIEW_MODES = [
    ("4x1", "4×1"),
    ("1x4", "1×4"),
    ("2x2", "2×2"),
    ("tabs", "タブ化"),
]
LOG_VIEW_MODE_LABELS = {key: label for key, label in LOG_VIEW_MODES}
LOG_VIEW_MODE_KEYS = {label: key for key, label in LOG_VIEW_MODES}


@dataclass
class TaskJobListItem:
    job_id: int
    eval_limit: int | str | None
    game_ply_limit: int | str | None
    book_extend_ply: str | None
    deferred: int
    taken: int
    total: int | None
    remaining: int | None


@dataclass(frozen=True)
class ThinkSfenMetadata:
    book_extend_ply: int | None = None
    eval_limit: int | None = None
    game_ply_limit: int | None = None


def normalize_log_view_mode(value: str | None) -> str:
    if value in LOG_VIEW_MODE_LABELS:
        return value
    if value in LOG_VIEW_MODE_KEYS:
        return LOG_VIEW_MODE_KEYS[value]
    if value == "stack":
        return "4x1"
    if value == "tabbed":
        return "tabs"
    return GUI_SETTING_DEFAULTS["log_view_mode"]


def load_gui_settings() -> dict[str, str]:
    if not GUI_SETTINGS_PATH.is_file():
        return {}

    try:
        with open(GUI_SETTINGS_PATH, "rb") as f:
            data = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError, AttributeError, TypeError):
        return {}

    if not isinstance(data, dict):
        return {}

    settings: dict[str, str] = {}
    for key in GUI_SETTING_DEFAULTS:
        value = data.get(key)
        if isinstance(value, str):
            settings[key] = value
        elif value is not None:
            settings[key] = str(value)

    legacy_eval_diff = data.get("eval_diff")
    if isinstance(legacy_eval_diff, str) and legacy_eval_diff.strip():
        settings.setdefault("peta_next_eval_diff", legacy_eval_diff)
        settings.setdefault("peta_refutation_eval_diff", legacy_eval_diff)

    legacy_eval_refu = data.get("eval_refutation_margin")
    if isinstance(legacy_eval_refu, str) and legacy_eval_refu.strip():
        settings.setdefault("peta_refutation_eval_refu", legacy_eval_refu)

    legacy_unsolved_eval_diff = data.get("peta_unsolved_eval_diff")
    if isinstance(legacy_unsolved_eval_diff, str):
        settings.setdefault("peta_unsolved_eval_drop_limit", legacy_unsolved_eval_diff)

    legacy_renamed_keys = {
        "peta_next_refutation_eval_diff": "peta_refutation_eval_diff",
        "peta_next_refutation_max_step": "peta_refutation_max_step",
        "peta_next_refutation_eval_refu": "peta_refutation_eval_refu",
        "peta_next_refutation_book_extend_ply": "peta_refutation_book_extend_ply",
        "peta_next_refutation_ply_limit": "peta_refutation_ply_limit",
        "auto_step2_peta_next_refutation": "auto_step2_peta_refutation",
    }
    for legacy_key, new_key in legacy_renamed_keys.items():
        value = data.get(legacy_key)
        if new_key not in settings and isinstance(value, str):
            settings[new_key] = value

    legacy_book_extend_ply = data.get("think_command_ply")
    if "enqueue_book_extend_ply" not in data and isinstance(legacy_book_extend_ply, str):
        settings["enqueue_book_extend_ply"] = legacy_book_extend_ply

    return settings


def settings_bool(value: str | None, default: str) -> bool:
    text = str(value if value is not None else default).strip().lower()
    return text in {"1", "true", "yes", "on"}


def split_think_sfen_metadata(line: str) -> tuple[str, ThinkSfenMetadata]:
    parts = line.split(",")
    position_cmd = parts[0].strip()
    book_extend_ply: int | None = None
    eval_limit: int | None = None
    game_ply_limit: int | None = None
    for raw_meta in parts[1:]:
        meta = raw_meta.strip()
        if not meta or "=" not in meta:
            continue
        key, value = [x.strip() for x in meta.split("=", 1)]
        if key == "book_extend_ply" and value.lower() != "none":
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed >= 0:
                book_extend_ply = parsed
        elif key == "eval_limit" and value.lower() != "none":
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed >= 0:
                eval_limit = parsed
        elif key == "game_ply_limit" and value.lower() != "none":
            try:
                parsed = int(value)
            except ValueError:
                continue
            if parsed > 0:
                game_ply_limit = parsed
    return position_cmd, ThinkSfenMetadata(book_extend_ply, eval_limit, game_ply_limit)


def book_extend_ply_rank(value: int | None) -> int:
    return -1 if value is None else value


def think_sfen_metadata_rank(metadata: ThinkSfenMetadata) -> tuple[int, int, int]:
    return (
        book_extend_ply_rank(metadata.book_extend_ply),
        book_extend_ply_rank(metadata.eval_limit),
        book_extend_ply_rank(metadata.game_ply_limit),
    )


def configure_initial_window_size(root: tk.Tk) -> None:
    root.update_idletasks()
    max_width = max(MIN_WINDOW_WIDTH, root.winfo_screenwidth() - WINDOW_SCREEN_MARGIN)
    max_height = max(MIN_WINDOW_HEIGHT, root.winfo_screenheight() - WINDOW_SCREEN_MARGIN)
    width = min(max(DEFAULT_WINDOW_WIDTH, root.winfo_reqwidth()), max_width)
    height = min(max(DEFAULT_WINDOW_HEIGHT, root.winfo_reqheight()), max_height)
    root.geometry(f"{width}x{height}")
    root.minsize(min(MIN_WINDOW_WIDTH, width), min(MIN_WINDOW_HEIGHT, height))


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def show(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            self.window,
            text=self.text,
            justify="left",
            padding=(8, 5),
            relief="solid",
            borderwidth=1,
            wraplength=420,
        )
        label.pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class BookMinerGui(ttk.Frame):
    def __init__(self, master: tk.Tk, *, enable_shogidb: bool = False, use_cpp: bool = False) -> None:
        super().__init__(master, padding=12)
        gui_settings = load_gui_settings()
        self.master = master
        self.enable_shogidb = enable_shogidb
        self.use_cpp = use_cpp
        self.bookminer_name = "BookMinerCpp.exe" if use_cpp else "BookMiner.py"
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str | None] = queue.Queue()
        self.output_buffer = ""
        self.log_widgets: dict[str, list[scrolledtext.ScrolledText]] = {}
        log_view_mode = normalize_log_view_mode(gui_settings.get("log_view_mode"))
        self.log_view_mode = tk.StringVar(value=LOG_VIEW_MODE_LABELS[log_view_mode])
        self.log_view_frames: dict[str, ttk.Frame] = {}
        self.task_list_mode_enabled = tk.BooleanVar(
            value=settings_bool(
                gui_settings.get("task_list_mode"),
                GUI_SETTING_DEFAULTS["task_list_mode"],
            )
        )
        self.step2_collapsed = tk.BooleanVar(
            value=settings_bool(
                gui_settings.get("step2_collapsed"),
                GUI_SETTING_DEFAULTS["step2_collapsed"],
            )
        )
        self.task_job_items: dict[int, TaskJobListItem] = {}
        self.task_job_views: list[tuple[scrolledtext.ScrolledText, ttk.Frame, ttk.Treeview]] = []
        self.progress_labels = {
            "read": tk.StringVar(value="定跡読込: 待機中"),
            "engine": tk.StringVar(value="エンジン起動: 待機中"),
            "write": tk.StringVar(value="定跡書込: 待機中"),
            "task": tk.StringVar(value="enqueue進捗: 待機中"),
        }
        self.progress_bars: dict[str, ttk.Progressbar] = {}
        self.startup_status = tk.StringVar(value="状態: 停止中")
        self.backup_status = tk.StringVar(value="次回自動保存 -")
        self.mining_status = tk.StringVar(value="現在 - 局面    現在の採掘速度 - 局面/日")
        self.latest_mining_positions: int | None = None
        self.mining_samples: list[tuple[float, int]] = []
        self.command_ready = False
        self.command_buttons: list[ttk.Widget] = []
        self.auto_enqueue_enabled = tk.BooleanVar(value=False)
        self.auto_enqueue_threshold = tk.StringVar(
            value=gui_settings.get("auto_enqueue_threshold", GUI_SETTING_DEFAULTS["auto_enqueue_threshold"])
        )
        self.auto_enqueue_state = AUTO_ENQUEUE_IDLE
        self.auto_step2_queue: list[str] = []
        self.auto_current_step2: str | None = None
        self.auto_tmp_seen: dict[str, str] = {}
        self.busy_action: str | None = None
        self.enqueue_pending = False
        self.peta_makebook_active = False
        self.task_queue_remaining: int | None = None

        self.default_eval_diff = tk.StringVar(
            value=gui_settings.get("default_eval_diff", GUI_SETTING_DEFAULTS["default_eval_diff"])
        )
        self.default_max_step = tk.StringVar(
            value=gui_settings.get("default_max_step", GUI_SETTING_DEFAULTS["default_max_step"])
        )

        self.peta_next_eval_diff = tk.StringVar(
            value=gui_settings.get(
                "peta_next_eval_diff",
                gui_settings.get("eval_diff", GUI_SETTING_DEFAULTS["peta_next_eval_diff"]),
            )
        )
        self.peta_refutation_eval_diff = tk.StringVar(
            value=gui_settings.get(
                "peta_refutation_eval_diff",
                gui_settings.get("eval_diff", GUI_SETTING_DEFAULTS["peta_refutation_eval_diff"]),
            )
        )
        self.peta_next_max_step = tk.StringVar(
            value=gui_settings.get(
                "peta_next_max_step",
                gui_settings.get("max_step", GUI_SETTING_DEFAULTS["peta_next_max_step"]),
            )
        )
        self.peta_refutation_max_step = tk.StringVar(
            value=gui_settings.get(
                "peta_refutation_max_step",
                GUI_SETTING_DEFAULTS["peta_refutation_max_step"],
            )
        )
        self.peta_refutation_eval_refu = tk.StringVar(
            value=gui_settings.get(
                "peta_refutation_eval_refu",
                GUI_SETTING_DEFAULTS["peta_refutation_eval_refu"],
            )
        )
        self.peta_next_book_extend_ply = tk.StringVar(
            value=gui_settings.get(
                "peta_next_book_extend_ply",
                GUI_SETTING_DEFAULTS["peta_next_book_extend_ply"],
            )
        )
        self.peta_refutation_book_extend_ply = tk.StringVar(
            value=gui_settings.get(
                "peta_refutation_book_extend_ply",
                GUI_SETTING_DEFAULTS["peta_refutation_book_extend_ply"],
            )
        )
        self.peta_unsolved_book_extend_ply = tk.StringVar(
            value=gui_settings.get(
                "peta_unsolved_book_extend_ply",
                GUI_SETTING_DEFAULTS["peta_unsolved_book_extend_ply"],
            )
        )
        self.peta_unsolved_eval_drop_limit = tk.StringVar(
            value=gui_settings.get(
                "peta_unsolved_eval_drop_limit",
                GUI_SETTING_DEFAULTS["peta_unsolved_eval_drop_limit"],
            )
        )
        self.peta_unsolved_max_step = tk.StringVar(
            value=gui_settings.get(
                "peta_unsolved_max_step",
                GUI_SETTING_DEFAULTS["peta_unsolved_max_step"],
            )
        )
        self.peta_opponent_eval_diff = tk.StringVar(
            value=gui_settings.get(
                "peta_opponent_eval_diff",
                GUI_SETTING_DEFAULTS["peta_opponent_eval_diff"],
            )
        )
        self.peta_opponent_max_step = tk.StringVar(
            value=gui_settings.get(
                "peta_opponent_max_step",
                GUI_SETTING_DEFAULTS["peta_opponent_max_step"],
            )
        )
        self.peta_opponent_book_extend_ply = tk.StringVar(
            value=gui_settings.get(
                "peta_opponent_book_extend_ply",
                GUI_SETTING_DEFAULTS["peta_opponent_book_extend_ply"],
            )
        )
        self.eval_limit = tk.StringVar(value=gui_settings.get("eval_limit", GUI_SETTING_DEFAULTS["eval_limit"]))
        self.game_ply_limit = tk.StringVar(
            value=gui_settings.get("game_ply_limit", GUI_SETTING_DEFAULTS["game_ply_limit"])
        )
        self.enqueue_book_extend_ply = tk.StringVar(
            value=gui_settings.get("enqueue_book_extend_ply", GUI_SETTING_DEFAULTS["enqueue_book_extend_ply"])
        )
        self.peta_next_ply_limit = tk.StringVar(
            value=gui_settings.get("peta_next_ply_limit", GUI_SETTING_DEFAULTS["peta_next_ply_limit"])
        )
        self.peta_refutation_ply_limit = tk.StringVar(
            value=gui_settings.get(
                "peta_refutation_ply_limit",
                GUI_SETTING_DEFAULTS["peta_refutation_ply_limit"],
            )
        )
        self.peta_unsolved_ply_limit = tk.StringVar(
            value=gui_settings.get(
                "peta_unsolved_ply_limit",
                GUI_SETTING_DEFAULTS["peta_unsolved_ply_limit"],
            )
        )
        self.peta_opponent_ply_limit = tk.StringVar(
            value=gui_settings.get(
                "peta_opponent_ply_limit",
                GUI_SETTING_DEFAULTS["peta_opponent_ply_limit"],
            )
        )
        self.peta_next_eval_limit = tk.StringVar(
            value=gui_settings.get("peta_next_eval_limit", GUI_SETTING_DEFAULTS["peta_next_eval_limit"])
        )
        self.peta_refutation_eval_limit = tk.StringVar(
            value=gui_settings.get("peta_refutation_eval_limit", GUI_SETTING_DEFAULTS["peta_refutation_eval_limit"])
        )
        self.peta_unsolved_eval_limit = tk.StringVar(
            value=gui_settings.get("peta_unsolved_eval_limit", GUI_SETTING_DEFAULTS["peta_unsolved_eval_limit"])
        )
        self.peta_opponent_eval_limit = tk.StringVar(
            value=gui_settings.get("peta_opponent_eval_limit", GUI_SETTING_DEFAULTS["peta_opponent_eval_limit"])
        )
        self.auto_step2_peta_next_enabled = tk.BooleanVar(
            value=settings_bool(gui_settings.get("auto_step2_peta_next"), GUI_SETTING_DEFAULTS["auto_step2_peta_next"])
        )
        self.auto_step2_peta_refutation_enabled = tk.BooleanVar(
            value=settings_bool(
                gui_settings.get("auto_step2_peta_refutation"),
                GUI_SETTING_DEFAULTS["auto_step2_peta_refutation"],
            )
        )
        self.auto_step2_peta_unsolved_enabled = tk.BooleanVar(
            value=settings_bool(
                gui_settings.get("auto_step2_peta_unsolved"),
                GUI_SETTING_DEFAULTS["auto_step2_peta_unsolved"],
            )
        )
        self.auto_step2_peta_opponent_enabled = tk.BooleanVar(
            value=settings_bool(
                gui_settings.get("auto_step2_peta_opponent"),
                GUI_SETTING_DEFAULTS["auto_step2_peta_opponent"],
            )
        )

        self.grid(sticky="nsew")
        self._build()
        self._poll_output()
        self._poll_mining_stats()
        self.after(100, self.start_process)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        commands = ttk.Frame(self)
        commands.grid(row=0, column=0, sticky="ew")
        commands.columnconfigure(15, weight=1)

        ttk.Label(commands, text="手順0.").grid(row=0, column=0, sticky="w", pady=3)
        self.kif_manager_button = ttk.Button(
            commands,
            text="棋譜抽出",
            width=STEP_BUTTON_WIDTH,
            command=self.start_kif_manager,
        )
        self.kif_manager_button.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(
            self.kif_manager_button,
            "KifManager を起動します。手順1.～2.の代わりに book/think_sfens.txt を用意します。",
        )

        ttk.Label(commands, text="手順1.").grid(row=1, column=0, sticky="w", pady=3)
        self.peta_button = ttk.Button(
            commands,
            text="peta_shock",
            width=STEP_BUTTON_WIDTH,
            command=self.send_peta_shock,
        )
        self.peta_button.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.peta_button, "`p` を送信します。未変更なら読み込み済みDBを再利用し、変更済みなら定跡DBを書き出して peta shock 化します。")
        self.peta_latest_button = ttk.Button(
            commands,
            text="peta_shock_latest",
            width=17,
            command=self.send_peta_shock_latest,
        )
        self.peta_latest_button.grid(row=1, column=2, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.peta_latest_button, "`pl` を送信します。DBを保存せず、book/backup/ にある最新の通常bookを peta shock 化して読み込みます。")
        self.peta_read_button = ttk.Button(
            commands,
            text="peta_read",
            width=STEP_BUTTON_WIDTH,
            command=self.send_peta_read,
        )
        self.peta_read_button.grid(row=1, column=3, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.peta_read_button, "`r` を送信します。外部で peta shock 化して book/backup/ に置いた最新 peta_book を読み込みます。")

        self.step2_toggle_button = ttk.Button(
            commands,
            width=10,
            command=self.on_step2_collapsed_toggled,
        )
        self.step2_toggle_button.grid(row=2, column=0, sticky="w", pady=3)
        Tooltip(self.step2_toggle_button, "手順2の詳細行を折りたたみ/展開します。")

        ttk.Label(commands, text="デフォルト値").grid(row=2, column=1, sticky="w", padx=(8, 0), pady=3)
        ttk.Label(commands, text="eval_diff").grid(row=2, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.default_eval_diff, width=8).grid(row=2, column=5, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=2, column=6, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.default_max_step, width=8).grid(row=2, column=7, sticky="w", pady=3)
        ttk.Label(commands, text="game ply limit").grid(row=2, column=8, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.game_ply_limit, width=8).grid(row=2, column=9, sticky="w", pady=3)
        ttk.Label(commands, text="book extend ply").grid(row=2, column=10, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.enqueue_book_extend_ply, width=8).grid(row=2, column=11, sticky="w", pady=3)
        ttk.Label(commands, text="eval_limit").grid(row=2, column=12, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.eval_limit, width=8).grid(row=2, column=13, sticky="w", pady=3)

        self.next_button = ttk.Button(
            commands,
            text="peta next",
            width=STEP_BUTTON_WIDTH,
            command=self.send_peta_next,
        )
        self.next_button.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.next_button, "`pn eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。peta shock 化した定跡から次に掘る leaf 局面を作ります。空欄はデフォルト値行を使います。")
        ttk.Label(commands, text="eval_diff").grid(row=3, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_next_eval_diff, width=8).grid(row=3, column=5, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=3, column=6, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_next_max_step, width=8).grid(row=3, column=7, sticky="w", pady=3)
        ttk.Label(commands, text="game ply limit").grid(row=3, column=8, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_next_ply_limit, width=8).grid(row=3, column=9, sticky="w", pady=3)
        ttk.Label(commands, text="book extend ply").grid(row=3, column=10, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_next_book_extend_ply, width=8).grid(row=3, column=11, sticky="w", pady=3)
        ttk.Label(commands, text="eval_limit").grid(row=3, column=12, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_next_eval_limit, width=8).grid(row=3, column=13, sticky="w", pady=3)
        ttk.Checkbutton(commands, text="自動", variable=self.auto_step2_peta_next_enabled).grid(
            row=3, column=14, sticky="w", padx=(12, 12), pady=3
        )

        ttk.Label(commands, text="").grid(row=4, column=0, sticky="w", pady=3)
        self.refutation_button = ttk.Button(
            commands,
            text="peta refutation",
            width=16,
            command=self.send_peta_refutation,
        )
        self.refutation_button.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(
            self.refutation_button,
            "`pr eval_refutation_margin eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。peta next のleafのうち、元DBでbestでなかった反駁leafだけを抽出します。空欄はデフォルト値行を使います。",
        )
        ttk.Label(commands, text="eval refu.").grid(row=4, column=2, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_eval_refu, width=8).grid(row=4, column=3, sticky="w", pady=3)
        ttk.Label(commands, text="eval_diff").grid(row=4, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_eval_diff, width=8).grid(row=4, column=5, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=4, column=6, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_max_step, width=8).grid(row=4, column=7, sticky="w", pady=3)
        ttk.Label(commands, text="game ply limit").grid(row=4, column=8, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_ply_limit, width=8).grid(row=4, column=9, sticky="w", pady=3)
        ttk.Label(commands, text="book extend ply").grid(row=4, column=10, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_book_extend_ply, width=8).grid(row=4, column=11, sticky="w", pady=3)
        ttk.Label(commands, text="eval_limit").grid(row=4, column=12, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_refutation_eval_limit, width=8).grid(row=4, column=13, sticky="w", pady=3)
        ttk.Checkbutton(commands, text="自動", variable=self.auto_step2_peta_refutation_enabled).grid(
            row=4, column=14, sticky="w", padx=(12, 12), pady=3
        )

        ttk.Label(commands, text="").grid(row=5, column=0, sticky="w", pady=3)
        self.unsolved_button = ttk.Button(
            commands,
            text="peta unsolved",
            width=16,
            command=self.send_peta_unsolved,
        )
        self.unsolved_button.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(
            self.unsolved_button,
            "`pu eval_drop_limit max_step game_ply_limit book_extend_ply eval_limit` を送信します。book/think_unsolved_sfens.txt の棋譜prefixからpeta_book上のPV leafを抽出します。空欄はデフォルト値行を使います。",
        )
        ttk.Label(commands, text="eval_drop_limit").grid(row=5, column=2, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_unsolved_eval_drop_limit, width=8).grid(row=5, column=3, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=5, column=6, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_unsolved_max_step, width=8).grid(row=5, column=7, sticky="w", pady=3)
        ttk.Label(commands, text="game ply limit").grid(row=5, column=8, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_unsolved_ply_limit, width=8).grid(row=5, column=9, sticky="w", pady=3)
        ttk.Label(commands, text="book extend ply").grid(row=5, column=10, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_unsolved_book_extend_ply, width=8).grid(row=5, column=11, sticky="w", pady=3)
        ttk.Label(commands, text="eval_limit").grid(row=5, column=12, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_unsolved_eval_limit, width=8).grid(row=5, column=13, sticky="w", pady=3)
        ttk.Checkbutton(commands, text="自動", variable=self.auto_step2_peta_unsolved_enabled).grid(
            row=5, column=14, sticky="w", padx=(12, 12), pady=3
        )

        ttk.Label(commands, text="").grid(row=6, column=0, sticky="w", pady=3)
        self.opponent_button = ttk.Button(
            commands,
            text="peta opponent",
            width=16,
            command=self.send_peta_opponent,
        )
        self.opponent_button.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(
            self.opponent_button,
            "`po eval_diff max_step game_ply_limit book_extend_ply eval_limit` を送信します。book/book_opponent/ の相手bookと現行peta_bookのbest進行から、対策候補leafを抽出します。空欄はデフォルト値行を使います。",
        )
        ttk.Label(commands, text="eval_diff").grid(row=6, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_opponent_eval_diff, width=8).grid(row=6, column=5, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=6, column=6, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_opponent_max_step, width=8).grid(row=6, column=7, sticky="w", pady=3)
        ttk.Label(commands, text="game ply limit").grid(row=6, column=8, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_opponent_ply_limit, width=8).grid(row=6, column=9, sticky="w", pady=3)
        ttk.Label(commands, text="book extend ply").grid(row=6, column=10, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_opponent_book_extend_ply, width=8).grid(row=6, column=11, sticky="w", pady=3)
        ttk.Label(commands, text="eval_limit").grid(row=6, column=12, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.peta_opponent_eval_limit, width=8).grid(row=6, column=13, sticky="w", pady=3)
        ttk.Checkbutton(commands, text="自動", variable=self.auto_step2_peta_opponent_enabled).grid(
            row=6, column=14, sticky="w", padx=(12, 12), pady=3
        )

        self.step2_widgets = [
            widget
            for widget in commands.grid_slaves()
            if widget is not self.step2_toggle_button
            and int(widget.grid_info().get("row", -1)) in {2, 3, 4, 5, 6}
        ]
        self._refresh_step2_visibility()

        ttk.Label(commands, text="手順3.").grid(row=7, column=0, sticky="w", pady=3)
        self.enqueue_button = ttk.Button(
            commands,
            text="enqueue",
            width=STEP_BUTTON_WIDTH,
            command=self.send_enqueue,
        )
        self.enqueue_button.grid(row=7, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.enqueue_button, "`e` を送信し、book/think_sfens.txt の局面を行ごとのメタ情報に従って探索キューに積みます。")

        ttk.Label(commands, text="手順4.").grid(row=8, column=0, sticky="w", pady=3)
        self.auto_check = ttk.Checkbutton(
            commands,
            text="自動enqueue",
            variable=self.auto_enqueue_enabled,
            command=self.on_auto_enqueue_toggled,
        )
        self.auto_check.grid(row=8, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.auto_check, "queueの残りが指定値より少なくなったら、peta_shock後に手順2で自動チェックされた抽出を順に実行し、結果をまとめてenqueueします。")
        ttk.Label(commands, text="queueの残りが").grid(row=8, column=2, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.auto_enqueue_threshold, width=8).grid(row=8, column=3, sticky="w", pady=3)
        ttk.Label(commands, text="より少なくなったら、自動チェックされた手順2をまとめてenqueue").grid(
            row=8,
            column=4,
            columnspan=4,
            sticky="w",
            padx=(8, 0),
            pady=3,
        )

        ttk.Label(commands, text="手順5.").grid(row=9, column=0, sticky="w", pady=3)
        self.write_button = ttk.Button(
            commands,
            text="DB手動保存",
            width=STEP_BUTTON_WIDTH,
            command=self.send_backup,
        )
        self.write_button.grid(row=9, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(self.write_button, "`w` を送信し、現在の定跡DBを book/backup/ に書き出します。")
        ttk.Label(commands, textvariable=self.backup_status).grid(
            row=9,
            column=2,
            columnspan=6,
            sticky="w",
            padx=(12, 0),
            pady=3,
        )
        self.command_buttons = [
            self.peta_button,
            self.peta_latest_button,
            self.peta_read_button,
            self.next_button,
            self.refutation_button,
            self.unsolved_button,
            self.opponent_button,
            self.enqueue_button,
            self.auto_check,
            self.write_button,
        ]

        progress = ttk.Frame(self)
        progress.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        progress.columnconfigure(1, weight=1)
        ttk.Label(progress, textvariable=self.startup_status).grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 4),
        )
        self._add_progress_row(progress, 1, "read")
        self._add_progress_row(progress, 2, "engine")
        self._add_progress_row(progress, 3, "write")
        self._add_progress_row(progress, 4, "task")
        ttk.Label(progress, textvariable=self.mining_status).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(5, 0),
        )

        log_area = ttk.Frame(self)
        log_area.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        log_area.columnconfigure(0, weight=1)
        log_area.rowconfigure(1, weight=1)

        log_controls = ttk.Frame(log_area)
        log_controls.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(log_controls, text="ログ表示").pack(side="left")
        log_view_combo = ttk.Combobox(
            log_controls,
            textvariable=self.log_view_mode,
            values=[label for _key, label in LOG_VIEW_MODES],
            state="readonly",
            width=8,
        )
        log_view_combo.pack(side="left", padx=(8, 0))
        log_view_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_log_view())
        Tooltip(log_view_combo, "ログ欄の並べ方を 4×1、1×4、2×2、タブ化から選びます。")

        self._build_log_views(log_area)
        self._update_task_view_mode()
        self._update_log_view()

        self._update_buttons()

    def _reset_progress(self) -> None:
        self.startup_status.set("状態: 起動中")
        self.progress_labels["read"].set("定跡読込: 待機中")
        self.progress_labels["engine"].set("エンジン起動: 待機中")
        self.progress_labels["write"].set("定跡書込: 待機中")
        self.progress_labels["task"].set("enqueue進捗: 待機中")
        self.backup_status.set("次回自動保存 -")
        self.mining_status.set("現在 - 局面    現在の採掘速度 - 局面/日")
        self.latest_mining_positions = None
        self.mining_samples.clear()
        self.task_queue_remaining = None
        self.task_job_items.clear()
        self._refresh_task_job_views()
        self.auto_enqueue_state = AUTO_ENQUEUE_IDLE
        self.busy_action = None
        self.command_ready = False
        for bar in self.progress_bars.values():
            bar.configure(maximum=1)
            bar["value"] = 0

    def _add_progress_row(self, parent: ttk.Frame, row: int, key: str) -> None:
        ttk.Label(parent, textvariable=self.progress_labels[key], width=36).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        bar = ttk.Progressbar(parent, mode="determinate", maximum=1, value=0)
        bar.grid(row=row, column=1, sticky="ew", pady=2)
        self.progress_bars[key] = bar

    def _register_log_widget(self, key: str, text: scrolledtext.ScrolledText) -> None:
        self.log_widgets.setdefault(key, []).append(text)

    def _add_log_header(self, parent: ttk.Frame, title: str, key: str) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        ttk.Label(header, text=title).pack(side="left")
        if key != "task":
            return

        check = ttk.Checkbutton(
            header,
            text="タスク一覧",
            variable=self.task_list_mode_enabled,
            command=self.on_task_list_mode_toggled,
        )
        check.pack(side="left", padx=(12, 0))
        Tooltip(check, "jobごとの enqueue 進捗一覧に切り替えます。完了した job は一覧から消えます。")

    def _add_log_content(self, parent: ttk.Frame, key: str, height: int) -> None:
        text = scrolledtext.ScrolledText(parent, wrap="word", height=height)
        text.grid(row=1, column=0, sticky="nsew")
        text.configure(state="disabled")
        self._register_log_widget(key, text)

        if key != "task":
            return

        list_frame = ttk.Frame(parent)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            list_frame,
            columns=("job", "remaining", "total", "deferred", "eval_limit", "game_ply_limit", "book_extend_ply"),
            show="headings",
            height=height,
        )
        tree.heading("job", text="job")
        tree.heading("remaining", text="残り")
        tree.heading("total", text="総数")
        tree.heading("deferred", text="defer")
        tree.heading("eval_limit", text="eval_limit")
        tree.heading("game_ply_limit", text="game_ply")
        tree.heading("book_extend_ply", text="extend_ply")
        tree.column("job", width=58, minwidth=48, anchor="w", stretch=False)
        tree.column("remaining", width=64, minwidth=52, anchor="e", stretch=False)
        tree.column("total", width=64, minwidth=52, anchor="e", stretch=False)
        tree.column("deferred", width=56, minwidth=46, anchor="e", stretch=False)
        tree.column("eval_limit", width=76, minwidth=64, anchor="e", stretch=False)
        tree.column("game_ply_limit", width=78, minwidth=66, anchor="e", stretch=False)
        tree.column("book_extend_ply", width=82, minwidth=70, anchor="e", stretch=True)

        yscroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=yscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

        self.task_job_views.append((text, list_frame, tree))
        self._refresh_task_job_tree(tree)

    def _add_log_pane(self, paned: ttk.PanedWindow, title: str, key: str, height: int) -> None:
        frame = ttk.Frame(paned, padding=(0, 0, 0, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self._add_log_header(frame, title, key)
        self._add_log_content(frame, key, height)
        paned.add(frame, weight=1)

    def _build_log_views(self, parent: ttk.Frame) -> None:
        for key, _label in LOG_VIEW_MODES:
            frame = ttk.Frame(parent)
            frame.grid(row=1, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            self.log_view_frames[key] = frame

        self._build_paned_logs(self.log_view_frames["4x1"], "vertical")
        self._build_paned_logs(self.log_view_frames["1x4"], "horizontal")
        self._build_grid_logs(self.log_view_frames["2x2"])
        self._build_tabbed_logs(self.log_view_frames["tabs"])

    def _build_paned_logs(self, parent: ttk.Frame, orient: str) -> None:
        logs = ttk.PanedWindow(parent, orient=orient)
        logs.grid(row=0, column=0, sticky="nsew")
        for key, title, _tab_title, height in LOG_PANES:
            self._add_log_pane(logs, title, key, height)

    def _build_grid_logs(self, parent: ttk.Frame) -> None:
        for row in range(2):
            parent.rowconfigure(row, weight=1, uniform="log-grid-row")
        for col in range(2):
            parent.columnconfigure(col, weight=1, uniform="log-grid-col")

        for index, (key, title, _tab_title, _height) in enumerate(LOG_PANES):
            row = index // 2
            col = index % 2
            frame = ttk.Frame(parent, padding=(0, 0, 6 if col == 0 else 0, 6 if row == 0 else 0))
            frame.grid(row=row, column=col, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            self._add_log_header(frame, title, key)
            self._add_log_content(frame, key, LOG_GRID_HEIGHT)

    def _build_tabbed_logs(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew")
        self.log_notebook = notebook
        self.log_tab_keys: dict[str, ttk.Frame] = {}

        for key, title, tab_title, height in LOG_PANES:
            frame = ttk.Frame(notebook, padding=(0, 4, 0, 0))
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(1, weight=1)
            self._add_log_header(frame, title, key)
            self._add_log_content(frame, key, height)
            notebook.add(frame, text=tab_title)
            self.log_tab_keys[key] = frame

    def _update_task_view_mode(self) -> None:
        show_list = self.task_list_mode_enabled.get()
        for text, list_frame, _tree in self.task_job_views:
            if show_list:
                text.grid_remove()
                list_frame.grid()
            else:
                list_frame.grid_remove()
                text.grid()
                text.see("end")

    def on_task_list_mode_toggled(self) -> None:
        self._update_task_view_mode()
        self.save_gui_settings()

    def on_step2_collapsed_toggled(self) -> None:
        self.step2_collapsed.set(not self.step2_collapsed.get())
        self._refresh_step2_visibility()
        self.save_gui_settings()

    def _refresh_step2_visibility(self) -> None:
        if not hasattr(self, "step2_toggle_button"):
            return

        collapsed = self.step2_collapsed.get()
        self.step2_toggle_button.configure(text="手順2. ▶" if collapsed else "手順2. ▼")

        for widget in getattr(self, "step2_widgets", []):
            if collapsed:
                widget.grid_remove()
            else:
                widget.grid()

    def _refresh_task_job_views(self) -> None:
        for _text, _list_frame, tree in self.task_job_views:
            self._refresh_task_job_tree(tree)

    def _refresh_task_job_tree(self, tree: ttk.Treeview) -> None:
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for job_id in sorted(self.task_job_items):
            item = self.task_job_items[job_id]
            eval_limit_display = str(item.eval_limit) if item.eval_limit is not None else "-"
            game_ply_limit_display = str(item.game_ply_limit) if item.game_ply_limit is not None else "-"
            book_extend_ply_display = item.book_extend_ply if item.book_extend_ply else "-"
            total_display = str(item.total) if item.total is not None else "?"
            remaining_display = str(item.remaining) if item.remaining is not None else "-"
            deferred_display = str(item.deferred)
            tree.insert(
                "",
                "end",
                iid=str(job_id),
                values=(
                    f"job {job_id}",
                    remaining_display,
                    total_display,
                    deferred_display,
                    eval_limit_display,
                    game_ply_limit_display,
                    book_extend_ply_display,
                ),
            )

    def _update_log_view(self) -> None:
        mode = normalize_log_view_mode(LOG_VIEW_MODE_KEYS.get(self.log_view_mode.get()))
        self.log_view_mode.set(LOG_VIEW_MODE_LABELS[mode])

        for key, frame in self.log_view_frames.items():
            if key == mode:
                frame.grid()
                frame.tkraise()
            else:
                frame.grid_remove()

        for widgets in self.log_widgets.values():
            for log in widgets:
                log.see("end")

    def start_process(self) -> None:
        if self.is_running():
            return

        if self.use_cpp:
            if not BOOK_MINER_CPP_EXE.is_file():
                messagebox.showerror("起動失敗", f"BookMinerCpp.exe が見つかりません: {BOOK_MINER_CPP_EXE}")
                return
            args = [str(BOOK_MINER_CPP_EXE), "--from_gui"]
            cwd = BOOK_MINER_CPP_EXE.parent
            command_text = f"{BOOK_MINER_CPP_EXE.name} --from_gui"
        else:
            if not BOOK_MINER_SCRIPT.is_file():
                messagebox.showerror("起動失敗", f"BookMiner.py が見つかりません: {BOOK_MINER_SCRIPT}")
                return
            args = [sys.executable, str(BOOK_MINER_SCRIPT), "--from_gui"]
            cwd = BASE_DIR
            command_text = f"{sys.executable} {BOOK_MINER_SCRIPT.name} --from_gui"

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            self.process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
                bufsize=0,
                env=env,
            )
        except OSError as exc:
            self.process = None
            messagebox.showerror("起動失敗", str(exc))
            return

        self._reset_progress()
        self._append_log("other", f"$ {command_text}\n")
        threading.Thread(target=self._read_output, daemon=True).start()
        self._update_buttons()

    def start_kif_manager(self) -> None:
        if not KIF_MANAGER_SCRIPT.is_file():
            messagebox.showerror("起動失敗", f"KifManager が見つかりません: {KIF_MANAGER_SCRIPT}")
            return
        args = [sys.executable, str(KIF_MANAGER_SCRIPT), "--from_bookminer"]
        if self.enable_shogidb:
            args.append("--shogidb")
        try:
            subprocess.Popen(
                args,
                cwd=KIF_MANAGER_SCRIPT.parent,
            )
        except OSError as exc:
            messagebox.showerror("起動失敗", str(exc))

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                chunk = process.stdout.read(1)
                if chunk == "":
                    break
                self.output_queue.put(chunk)
        finally:
            self.output_queue.put(None)

    def _poll_output(self) -> None:
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._on_process_ended()
            else:
                self._handle_output(item)
        self.after(100, self._poll_output)

    def _on_process_ended(self) -> None:
        self._flush_output_buffer()
        process = self.process
        if process is not None:
            return_code = process.poll()
            self._append_log("other", f"\n[GUI] {self.bookminer_name} exited. return code = {return_code}\n")
        self.process = None
        self.auto_enqueue_state = AUTO_ENQUEUE_IDLE
        self.auto_step2_queue = []
        self.auto_current_step2 = None
        self.auto_tmp_seen = {}
        self.busy_action = None
        self.command_ready = False
        self.startup_status.set("状態: 停止中")
        self._update_buttons()

    def _handle_output(self, text: str) -> None:
        self.output_buffer += text
        while True:
            line = self._take_output_line()
            if line is None:
                break
            self._handle_log_output_line(line)

        if self.output_buffer.endswith("> "):
            self._append_log("other", self.output_buffer)
            self.output_buffer = ""

    def _flush_output_buffer(self) -> None:
        if self.output_buffer:
            self._handle_log_output_line(self.output_buffer)
            self.output_buffer = ""

    def _take_output_line(self) -> str | None:
        newline_index = self.output_buffer.find("\n")
        carriage_index = self.output_buffer.find("\r")
        indexes = [index for index in (newline_index, carriage_index) if index != -1]
        if not indexes:
            return None

        index = min(indexes)
        separator = self.output_buffer[index]
        line = self.output_buffer[:index]
        self.output_buffer = self.output_buffer[index + 1 :]
        if separator == "\r" and self.output_buffer.startswith("\n"):
            self.output_buffer = self.output_buffer[1:]
        return line + "\n"

    def _handle_log_output_line(self, line: str) -> None:
        self._handle_progress_line(line)
        if not self._should_suppress_log_line(line):
            key = self._classify_log_line(line)
            self._append_log(key, line)
            if self._should_mirror_to_command_log(key, line):
                self._append_log("other", line)

    def _should_suppress_log_line(self, line: str) -> bool:
        return YANEURAOU_PROGRESS_BAR_RE.fullmatch(line.strip()) is not None

    def _should_mirror_to_command_log(self, key: str, line: str) -> bool:
        if key != "peta":
            return False
        return (
            PETA_MAKEBOOK_START_RE.search(line) is not None
            or PETA_MAKEBOOK_DONE_RE.search(line) is not None
            or PETA_MAKEBOOK_LINE_RE.search(line) is not None
            or PETA_COMMAND_DONE_RE.search(line) is not None
            or PETA_READ_DONE_RE.search(line) is not None
            or PETA_COMMAND_LOG_MIRROR_RE.search(line) is not None
        )

    def _handle_progress_line(self, line: str) -> None:
        self._handle_peta_makebook_context_line(line)
        self._handle_startup_line(line)
        self._handle_book_progress_line(line)
        self._handle_task_job_list_line(line)
        self._handle_task_queue_progress_line(line)
        self._handle_mining_progress_line(line)
        self._handle_auto_enqueue_line(line)

    def _handle_peta_makebook_context_line(self, line: str) -> None:
        if PETA_MAKEBOOK_START_RE.search(line):
            self.peta_makebook_active = True
            self._update_buttons()
            return
        if PETA_MAKEBOOK_DONE_RE.search(line) or PETA_COMMAND_DONE_RE.search(line):
            self.peta_makebook_active = False
            self._update_buttons()

    def _handle_startup_line(self, line: str) -> None:
        stage_match = STARTUP_STAGE_RE.search(line)
        if stage_match is not None:
            _stage, message = stage_match.groups()
            self.startup_status.set(f"状態: {message}")
            return

        engine_match = ENGINE_INIT_RE.search(line)
        if engine_match is not None:
            phase, count_text, total_text = engine_match.groups()
            count = int(count_text)
            total = int(total_text)
            ready_match = re.search(r"\bready=(\d+)", line)
            if phase == "Done":
                label_text = f"エンジン起動完了 {count}/{total}"
            elif ready_match is not None:
                label_text = f"エンジン起動中 {count}/{total} ready={ready_match.group(1)}"
            else:
                label_text = f"エンジン起動中 {count}/{total}"
            self.progress_labels["engine"].set(label_text)
            bar = self.progress_bars["engine"]
            if total > 0:
                bar.configure(maximum=total)
                bar["value"] = min(count, total)
            else:
                bar.configure(maximum=1)
                bar["value"] = 1
            if phase == "Done":
                self.startup_status.set("状態: 自動保存サービス起動待ち")
            return

        ready_match = ENGINE_READY_RE.search(line)
        if ready_match is not None:
            count_text, total_text = ready_match.groups()
            self.startup_status.set(f"状態: エンジン応答待ち {count_text}/{total_text}")
            self.progress_labels["engine"].set(f"エンジン応答待ち {count_text}/{total_text}")
            return

        backup_match = BACKUP_STATUS_RE.search(line)
        if backup_match is not None:
            tag, rest = backup_match.groups()
            next_match = re.search(r"\bnext=(\S+)", rest)
            if tag in ("BackupServiceStarted", "BackupNext") and next_match is not None:
                next_time = next_match.group(1).replace("_", " ")
                self.backup_status.set(f"次回自動保存 {next_time}")
                if tag == "BackupServiceStarted":
                    self.startup_status.set("状態: 自動保存サービス起動完了")
            return

        if COMMAND_READY_RE.search(line):
            self.command_ready = True
            self.startup_status.set("状態: コマンド受付を開始しました。")
            self._append_log("other", "[GUI] コマンド受付を開始しました。\n")
            self._update_buttons()

    def _handle_book_progress_line(self, line: str) -> None:
        match = BOOK_PROGRESS_RE.search(line)
        if match is None:
            return
        direction, phase, count_text, total_text = match.groups()
        key = "read" if direction == "Read" else "write"
        base = "定跡読込" if key == "read" else "定跡書込"
        count = int(count_text)
        total = None if total_text == "?" else int(total_text)

        if phase == "Done":
            label = f"{base}完了"
        else:
            label = f"{base}中"

        total_display = str(total) if total is not None else "?"
        self.progress_labels[key].set(f"{label} {count}/{total_display}")

        bar = self.progress_bars[key]
        if total is not None and total > 0:
            bar.configure(maximum=total)
            bar["value"] = min(count, total)
        elif phase == "Done":
            bar.configure(maximum=1)
            bar["value"] = 1
        else:
            bar.configure(maximum=1)
            bar["value"] = 0

        if phase == "Done" and key == "write" and self.busy_action == "manual_backup":
            self.busy_action = None
            self._update_buttons()

    def _handle_task_queue_progress_line(self, line: str) -> None:
        match = TASK_QUEUE_PROGRESS_RE.search(line)
        if match is None:
            return

        phase, count_text, total_text = match.groups()
        count = int(count_text)
        total = None if total_text == "?" else int(total_text)
        remaining = None if total is None else max(total - count, 0)

        label = "enqueue完了" if phase == "Done" else "enqueue進捗"
        total_display = str(total) if total is not None else "?"
        self.progress_labels["task"].set(f"{label} {count}/{total_display}")

        bar = self.progress_bars["task"]
        if total is not None and total > 0:
            bar.configure(maximum=total)
            bar["value"] = min(count, total)
        elif phase == "Done":
            bar.configure(maximum=1)
            bar["value"] = 1
        else:
            bar.configure(maximum=1)
            bar["value"] = 0

        self.task_queue_remaining = remaining

        if self.busy_action == "manual_enqueue" and phase == "Start":
            self.busy_action = None
            self._update_buttons()
            return

        if self.enqueue_pending and phase == "Start":
            self.enqueue_pending = False
            self._update_buttons()
            return

        if self.auto_enqueue_state == AUTO_ENQUEUE_ENQUEUE and phase == "Start":
            self._append_log("task", "[GUI] auto enqueue sequence completed.\n")
            self.auto_enqueue_state = AUTO_ENQUEUE_IDLE
            self.busy_action = None
            self._update_buttons()
            self._maybe_start_auto_enqueue()
            return

        self._maybe_start_auto_enqueue()

    def _handle_task_job_list_line(self, line: str) -> None:
        match = TASK_QUEUE_JOB_STATUS_RE.search(line)
        if match is None:
            return

        phase, _count_text, _total_text, rest = match.groups()
        fields = dict(TASK_QUEUE_FIELD_RE.findall(rest))

        if phase == "Done":
            self.task_job_items.clear()
            self._refresh_task_job_views()
            return

        job_text = fields.get("job")
        if job_text is None:
            return

        try:
            job_id = int(job_text)
        except ValueError:
            return

        if phase == "JobDone":
            self.task_job_items.pop(job_id, None)
            self._refresh_task_job_views()
            return

        progress_text = fields.get("job_progress")
        if progress_text is None:
            return

        progress_match = re.fullmatch(r"(\d+)/(\d+|\?)", progress_text)
        if progress_match is None:
            return

        taken_text, total_text = progress_match.groups()
        taken = int(taken_text)
        total = None if total_text == "?" else int(total_text)
        remaining = self._parse_task_job_remaining(fields.get("job_remaining"), taken, total)
        deferred = self._parse_task_job_deferred(fields.get("deferred"))
        if deferred is None and job_id in self.task_job_items:
            deferred = self.task_job_items[job_id].deferred
        if deferred is None:
            deferred = 0
        eval_limit = self._parse_task_job_eval_limit(fields.get("eval_limit"))
        if eval_limit is None and job_id in self.task_job_items:
            eval_limit = self.task_job_items[job_id].eval_limit
        game_ply_limit = self._parse_task_job_eval_limit(fields.get("game_ply_limit"))
        if game_ply_limit is None and job_id in self.task_job_items:
            game_ply_limit = self.task_job_items[job_id].game_ply_limit
        book_extend_ply = fields.get("book_extend_ply")
        if book_extend_ply is None and job_id in self.task_job_items:
            book_extend_ply = self.task_job_items[job_id].book_extend_ply

        if total == 0 or remaining == 0 or (total is not None and taken >= total):
            self.task_job_items.pop(job_id, None)
        else:
            self.task_job_items[job_id] = TaskJobListItem(
                job_id=job_id,
                eval_limit=eval_limit,
                game_ply_limit=game_ply_limit,
                book_extend_ply=book_extend_ply,
                deferred=deferred,
                taken=taken,
                total=total,
                remaining=remaining,
            )
        self._refresh_task_job_views()

    def _parse_task_job_eval_limit(self, eval_limit_text: str | None) -> int | str | None:
        if eval_limit_text is None:
            return None
        try:
            return int(eval_limit_text)
        except ValueError:
            return eval_limit_text

    def _parse_task_job_deferred(self, deferred_text: str | None) -> int | None:
        if deferred_text is None:
            return None
        try:
            return int(deferred_text)
        except ValueError:
            return None

    def _parse_task_job_remaining(
        self,
        remaining_text: str | None,
        taken: int,
        total: int | None,
    ) -> int | None:
        if remaining_text is not None:
            try:
                return int(remaining_text)
            except ValueError:
                return None
        if total is None:
            return None
        return max(total - taken, 0)

    def _handle_mining_progress_line(self, line: str) -> None:
        match = MINING_PROGRESS_RE.search(line)
        if match is None:
            return

        positions = int(match.group(1))
        if self.latest_mining_positions is not None and positions < self.latest_mining_positions:
            self.mining_samples.clear()

        self.latest_mining_positions = positions
        if not self.mining_samples:
            self._record_mining_sample(time.time(), positions)
        else:
            self._update_mining_status()

    def _poll_mining_stats(self) -> None:
        if self.is_running() and self.latest_mining_positions is not None:
            self._record_mining_sample(time.time(), self.latest_mining_positions)
        self.after(MINING_STATS_SAMPLE_INTERVAL_MS, self._poll_mining_stats)

    def _record_mining_sample(self, now: float, positions: int) -> None:
        if self.mining_samples and positions < self.mining_samples[-1][1]:
            self.mining_samples.clear()

        self.mining_samples.append((now, positions))
        cutoff = now - MINING_STATS_WINDOW_SECONDS
        while len(self.mining_samples) > 1 and self.mining_samples[0][0] < cutoff:
            self.mining_samples.pop(0)

        self._update_mining_status(now, positions)

    def _update_mining_status(self, now: float | None = None, positions: int | None = None) -> None:
        if positions is None:
            positions = self.latest_mining_positions
        if positions is None:
            self.mining_status.set("現在 - 局面    現在の採掘速度 - 局面/日")
            return

        if now is None:
            now = time.time()

        speed_text = "-"
        if len(self.mining_samples) >= 2:
            start_time, start_positions = self.mining_samples[0]
            elapsed = now - start_time
            if elapsed > 0:
                added_positions = max(positions - start_positions, 0)
                speed = round(added_positions * 24 * 60 * 60 / elapsed)
                speed_text = f"{speed:,}"

        self.mining_status.set(
            f"現在 {positions:,} 局面    現在の採掘速度 {speed_text} 局面/日"
        )

    def _handle_auto_enqueue_line(self, line: str) -> None:
        if "Exception :" in line and self.auto_enqueue_state != AUTO_ENQUEUE_IDLE:
            self._abort_auto_enqueue("auto enqueue stopped: BookMiner.py reported an exception.")
            return
        if "Exception :" in line and self.busy_action is not None:
            self._append_log("other", "[GUI] BookMiner command failed. manual busy state was cleared.\n")
            self.busy_action = None
            self._update_buttons()
            return

        if PETA_COMMAND_DONE_RE.search(line):
            if self.busy_action in {"manual_peta_shock", "manual_peta_shock_latest"}:
                self.busy_action = None
                self._update_buttons()
                return

            if self.auto_enqueue_state == AUTO_ENQUEUE_PETA:
                if not self.auto_enqueue_enabled.get():
                    self._abort_auto_enqueue("auto enqueue stopped: disabled after peta_shock.")
                    return
                self.auto_enqueue_state = AUTO_ENQUEUE_NEXT
                self._start_next_auto_step2()
                return

        if PETA_READ_DONE_RE.search(line):
            if self.busy_action == "manual_peta_read":
                self.busy_action = None
                self._update_buttons()
                return

        if PETA_NEXT_DONE_RE.search(line):
            if self.busy_action == "manual_peta_next":
                self.busy_action = None
                self._update_buttons()
                return

            if self.auto_enqueue_state == AUTO_ENQUEUE_NEXT:
                self._complete_auto_step2(AUTO_STEP2_PETA_NEXT)
                return

        if PETA_REFUTATION_DONE_RE.search(line):
            if self.busy_action == "manual_peta_refutation":
                self.busy_action = None
                self._update_buttons()
                return
            if self.auto_enqueue_state == AUTO_ENQUEUE_NEXT:
                self._complete_auto_step2(AUTO_STEP2_PETA_REFUTATION)
                return

        if PETA_UNSOLVED_DONE_RE.search(line):
            if self.busy_action == "manual_peta_unsolved":
                self.busy_action = None
                self._update_buttons()
                return
            if self.auto_enqueue_state == AUTO_ENQUEUE_NEXT:
                self._complete_auto_step2(AUTO_STEP2_PETA_UNSOLVED)
                return

        if PETA_OPPONENT_DONE_RE.search(line):
            if self.busy_action == "manual_peta_opponent":
                self.busy_action = None
                self._update_buttons()
                return
            if self.auto_enqueue_state == AUTO_ENQUEUE_NEXT:
                self._complete_auto_step2(AUTO_STEP2_PETA_OPPONENT)
                return

    def on_auto_enqueue_toggled(self) -> None:
        if self.auto_enqueue_enabled.get():
            if self._get_auto_enqueue_threshold() is None:
                self.auto_enqueue_enabled.set(False)
                return
            if not self._selected_auto_step2_methods():
                messagebox.showerror("入力エラー", "自動enqueueで実行する手順2を1つ以上チェックしてください。")
                self.auto_enqueue_enabled.set(False)
                return
            self._maybe_start_auto_enqueue()
        elif self.auto_enqueue_state != AUTO_ENQUEUE_IDLE:
            self._append_log("task", "[GUI] auto enqueue disabled. current BookMiner command will not be interrupted.\n")

    def _get_auto_enqueue_threshold(self) -> int | None:
        value = self.auto_enqueue_threshold.get().strip()
        try:
            threshold = int(value)
        except ValueError:
            messagebox.showerror("入力エラー", "自動enqueueのqueue残り数には、1以上の整数を指定してください。")
            return None

        if threshold <= 0:
            messagebox.showerror("入力エラー", "自動enqueueのqueue残り数には、1以上の整数を指定してください。")
            return None

        return threshold

    def _maybe_start_auto_enqueue(self) -> None:
        if not self.auto_enqueue_enabled.get():
            return
        if not self.is_running():
            return
        if self.auto_enqueue_state != AUTO_ENQUEUE_IDLE:
            return
        if self.busy_action is not None:
            return
        if self.task_queue_remaining is None:
            return

        threshold = self._get_auto_enqueue_threshold()
        if threshold is None:
            self.auto_enqueue_enabled.set(False)
            return
        if self.task_queue_remaining >= threshold:
            return

        selected_methods = self._selected_auto_step2_methods()
        if not selected_methods:
            self.auto_enqueue_enabled.set(False)
            self._append_log("task", "[GUI] auto enqueue disabled: no step 2 method is selected.\n")
            return

        if not self._reset_auto_think_sfens_tmp():
            self.auto_enqueue_enabled.set(False)
            return

        self.auto_step2_queue = selected_methods
        self.auto_current_step2 = None
        self.auto_tmp_seen = {}
        self.auto_enqueue_state = AUTO_ENQUEUE_PETA
        self.busy_action = "auto_enqueue"
        self._update_buttons()
        self._append_log(
            "task",
            f"[GUI] auto enqueue started. remaining={self.task_queue_remaining}, "
            f"threshold={threshold}, step2={','.join(selected_methods)}\n",
        )
        if not self.send_command("p", origin="AUTO"):
            self._abort_auto_enqueue("auto enqueue stopped: failed to send peta_shock.")

    def _abort_auto_enqueue(self, message: str) -> None:
        self._append_log("task", f"[GUI] {message}\n")
        self.auto_enqueue_state = AUTO_ENQUEUE_IDLE
        self.auto_step2_queue = []
        self.auto_current_step2 = None
        self.auto_tmp_seen = {}
        if self.busy_action == "auto_enqueue":
            self.busy_action = None
        self.auto_enqueue_enabled.set(False)
        self._update_buttons()

    def _selected_auto_step2_methods(self) -> list[str]:
        methods: list[str] = []
        if self.auto_step2_peta_next_enabled.get():
            methods.append(AUTO_STEP2_PETA_NEXT)
        if self.auto_step2_peta_refutation_enabled.get():
            methods.append(AUTO_STEP2_PETA_REFUTATION)
        if self.auto_step2_peta_unsolved_enabled.get():
            methods.append(AUTO_STEP2_PETA_UNSOLVED)
        if self.auto_step2_peta_opponent_enabled.get():
            methods.append(AUTO_STEP2_PETA_OPPONENT)
        return methods

    def _reset_auto_think_sfens_tmp(self) -> bool:
        try:
            AUTO_THINK_SFENS_PATH.parent.mkdir(parents=True, exist_ok=True)
            if AUTO_THINK_SFENS_PATH.exists():
                AUTO_THINK_SFENS_PATH.unlink()
        except OSError as exc:
            self._append_log("task", f"[GUI] auto enqueue stopped: failed to reset {AUTO_THINK_SFENS_COMMAND_PATH}: {exc}\n")
            return False
        return True

    def _append_auto_think_sfens_tmp(self, source_name: str) -> bool:
        if not THINK_SFENS_PATH.exists():
            self._append_log("task", f"[GUI] auto enqueue stopped: {THINK_SFENS_COMMAND_PATH} was not written by {source_name}.\n")
            return False

        added = 0
        skipped = 0
        updated = 0
        try:
            AUTO_THINK_SFENS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(THINK_SFENS_PATH, "r", encoding="utf-8") as src:
                for raw_line in src:
                    line = raw_line.rstrip("\r\n")
                    if not line:
                        continue
                    position_cmd, metadata = split_think_sfen_metadata(line)
                    old_line = self.auto_tmp_seen.get(position_cmd)
                    if old_line is None:
                        self.auto_tmp_seen[position_cmd] = line
                        added += 1
                        continue
                    _old_position_cmd, old_metadata = split_think_sfen_metadata(old_line)
                    if think_sfen_metadata_rank(metadata) > think_sfen_metadata_rank(old_metadata):
                        self.auto_tmp_seen[position_cmd] = line
                        updated += 1
                        continue
                    else:
                        skipped += 1
            with open(AUTO_THINK_SFENS_PATH, "w", encoding="utf-8") as dst:
                for line in self.auto_tmp_seen.values():
                    dst.write(line + "\n")
        except OSError as exc:
            self._append_log("task", f"[GUI] auto enqueue stopped: failed to append {source_name}: {exc}\n")
            return False

        self._append_log(
            "task",
            f"[GUI] auto enqueue appended {source_name}: added={added}, updated={updated}, duplicates={skipped}, total={len(self.auto_tmp_seen)}\n",
        )
        return True

    def _promote_auto_think_sfens_tmp(self) -> bool:
        try:
            if not AUTO_THINK_SFENS_PATH.exists():
                self._append_log("task", f"[GUI] auto enqueue stopped: {AUTO_THINK_SFENS_COMMAND_PATH} was not created.\n")
                return False
            THINK_SFENS_PATH.parent.mkdir(parents=True, exist_ok=True)
            os.replace(AUTO_THINK_SFENS_PATH, THINK_SFENS_PATH)
        except OSError as exc:
            self._append_log("task", f"[GUI] auto enqueue stopped: failed to prepare {THINK_SFENS_COMMAND_PATH}: {exc}\n")
            return False
        self._append_log("task", f"[GUI] auto enqueue prepared {THINK_SFENS_COMMAND_PATH}: total={len(self.auto_tmp_seen)}\n")
        return True

    def _start_next_auto_step2(self) -> None:
        if not self.auto_enqueue_enabled.get():
            self._abort_auto_enqueue("auto enqueue stopped: disabled before step 2.")
            return

        if not self.auto_step2_queue:
            self.auto_current_step2 = None
            self.auto_enqueue_state = AUTO_ENQUEUE_ENQUEUE
            if not self._promote_auto_think_sfens_tmp():
                self._abort_auto_enqueue("auto enqueue stopped: failed to prepare enqueue input.")
                return
            if not self.send_enqueue(auto=True):
                self._abort_auto_enqueue("auto enqueue stopped: failed to send enqueue.")
                return
            return

        method = self.auto_step2_queue.pop(0)
        self.auto_current_step2 = method
        senders = {
            AUTO_STEP2_PETA_NEXT: self.send_peta_next,
            AUTO_STEP2_PETA_REFUTATION: self.send_peta_refutation,
            AUTO_STEP2_PETA_UNSOLVED: self.send_peta_unsolved,
            AUTO_STEP2_PETA_OPPONENT: self.send_peta_opponent,
        }
        sender = senders[method]
        if not sender(auto=True):
            self._abort_auto_enqueue(f"auto enqueue stopped: failed to send {method}.")

    def _complete_auto_step2(self, method: str) -> None:
        if self.auto_current_step2 != method:
            return
        if not self.auto_enqueue_enabled.get():
            self._abort_auto_enqueue(f"auto enqueue stopped: disabled after {method}.")
            return
        if not self._append_auto_think_sfens_tmp(method):
            self._abort_auto_enqueue(f"auto enqueue stopped: failed to append {method}.")
            return
        self.auto_current_step2 = None
        self._start_next_auto_step2()

    def _begin_manual_action(self, action: str) -> bool:
        if self.auto_enqueue_state != AUTO_ENQUEUE_IDLE:
            messagebox.showinfo("実行中", "自動enqueueの処理中です。完了してから操作してください。")
            return False
        if action == "manual_enqueue" and self.busy_action in {"manual_peta_shock", "manual_peta_shock_latest", "manual_peta_read"}:
            if self.enqueue_pending:
                messagebox.showinfo("実行中", "enqueueコマンドは送信済みです。処理開始まで待ってください。")
                return False
            self.enqueue_pending = True
            self._update_buttons()
            return True
        if self.busy_action is not None:
            messagebox.showinfo("実行中", "BookMinerコマンドの実行中です。完了してから操作してください。")
            return False
        self.busy_action = action
        self._update_buttons()
        return True

    def _classify_log_line(self, line: str) -> str:
        lower = line.lower()
        if (
            "[startupstage]" in lower
            or "[engineinit" in lower
            or "[enginereadyprogress]" in lower
            or "[backupservice" in lower
            or "[backupnext]" in lower
            or "[backupstart]" in lower
            or "[backupdone]" in lower
            or "[commandready]" in lower
        ):
            return "other"
        if (
            "[taskqueue" in lower
            or "[taskworker" in lower
            or "[miningprogress]" in lower
            or "put position commands" in lower
            or re.search(r"\(\d+\) read \d+ position commands", line)
        ):
            return "task"
        book_progress_match = BOOK_PROGRESS_RE.search(line)
        if (
            book_progress_match is not None
            and book_progress_match.group(1) == "Read"
            and (
                self.peta_makebook_active
                or self.busy_action in {"manual_peta_shock", "manual_peta_shock_latest", "manual_peta_read", "auto_enqueue"}
            )
        ):
            return "peta"
        if (
            "peta_shock" in lower
            or "p command" in lower
            or "[petacommanddone]" in lower
            or "[petareaddone]" in lower
            or "[petanextdone]" in lower
            or "[petarefutationdone]" in lower
            or "[petaunsolveddone]" in lower
            or "[petaopponentdone]" in lower
            or PETA_MAKEBOOK_CONTEXT_RE.search(line)
            or PETA_MAKEBOOK_LINE_RE.search(line)
            or "peta shocked book" in lower
            or "peta_next" in lower
            or "peta_refutation" in lower
            or "peta_unsolved" in lower
            or "peta_opponent" in lower
            or "refutation step" in lower
            or "refutation progress" in lower
            or "unsolved progress" in lower
            or "opponent progress" in lower
            or "root sfen" in lower
            or "think_sfens" in lower
            or "write book path" in lower
        ):
            return "peta"
        if (
            "max_book_ply reached" in lower
            or "reached max_book_ply" in lower
            or "過去10分" in line
            or "all tasks completed" in lower
            or re.search(r"\[\d+\]\s+.+\s,\s*[0-9.]+", line)
        ):
            return "search"
        return "other"

    def send_command(self, command: str, origin: str = "GUI") -> bool:
        if not self.is_running() or self.process is None or self.process.stdin is None:
            if origin == "GUI":
                messagebox.showinfo("未起動", f"{self.bookminer_name} が起動していません。GUI を再起動してください。")
            else:
                self._append_log("task", f"[{origin}] {self.bookminer_name} is not running.\n")
            return False
        if not self.command_ready:
            if origin == "GUI":
                messagebox.showinfo("起動中", f"{self.bookminer_name} の起動処理が終わるまで待ってください。")
            else:
                self._append_log("task", f"[{origin}] {self.bookminer_name} is not ready for commands.\n")
            return False
        self._append_log("other", f"\n[{origin}] > {command}\n")
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            if origin == "GUI":
                messagebox.showerror("送信失敗", str(exc))
            else:
                self._append_log("task", f"[{origin}] send failed: {exc}\n")
            return False
        return True

    def save_gui_settings(self) -> bool:
        data = {
            "default_eval_diff": self.default_eval_diff.get(),
            "default_max_step": self.default_max_step.get(),
            "peta_next_eval_diff": self.peta_next_eval_diff.get(),
            "peta_refutation_eval_diff": self.peta_refutation_eval_diff.get(),
            "peta_next_max_step": self.peta_next_max_step.get(),
            "peta_refutation_max_step": self.peta_refutation_max_step.get(),
            "peta_refutation_eval_refu": self.peta_refutation_eval_refu.get(),
            "peta_next_book_extend_ply": self.peta_next_book_extend_ply.get(),
            "peta_refutation_book_extend_ply": self.peta_refutation_book_extend_ply.get(),
            "peta_unsolved_book_extend_ply": self.peta_unsolved_book_extend_ply.get(),
            "peta_unsolved_eval_drop_limit": self.peta_unsolved_eval_drop_limit.get(),
            "peta_unsolved_max_step": self.peta_unsolved_max_step.get(),
            "peta_opponent_eval_diff": self.peta_opponent_eval_diff.get(),
            "peta_opponent_max_step": self.peta_opponent_max_step.get(),
            "peta_opponent_book_extend_ply": self.peta_opponent_book_extend_ply.get(),
            "eval_limit": self.eval_limit.get(),
            "game_ply_limit": self.game_ply_limit.get(),
            "enqueue_book_extend_ply": self.enqueue_book_extend_ply.get(),
            "peta_next_ply_limit": self.peta_next_ply_limit.get(),
            "peta_refutation_ply_limit": self.peta_refutation_ply_limit.get(),
            "peta_unsolved_ply_limit": self.peta_unsolved_ply_limit.get(),
            "peta_opponent_ply_limit": self.peta_opponent_ply_limit.get(),
            "peta_next_eval_limit": self.peta_next_eval_limit.get(),
            "peta_refutation_eval_limit": self.peta_refutation_eval_limit.get(),
            "peta_unsolved_eval_limit": self.peta_unsolved_eval_limit.get(),
            "peta_opponent_eval_limit": self.peta_opponent_eval_limit.get(),
            "auto_step2_peta_next": "1" if self.auto_step2_peta_next_enabled.get() else "0",
            "auto_step2_peta_refutation": "1" if self.auto_step2_peta_refutation_enabled.get() else "0",
            "auto_step2_peta_unsolved": "1" if self.auto_step2_peta_unsolved_enabled.get() else "0",
            "auto_step2_peta_opponent": "1" if self.auto_step2_peta_opponent_enabled.get() else "0",
            "auto_enqueue_threshold": self.auto_enqueue_threshold.get(),
            "log_view_mode": normalize_log_view_mode(LOG_VIEW_MODE_KEYS.get(self.log_view_mode.get())),
            "task_list_mode": "1" if self.task_list_mode_enabled.get() else "0",
            "step2_collapsed": "1" if self.step2_collapsed.get() else "0",
        }
        try:
            with open(GUI_SETTINGS_PATH, "wb") as f:
                pickle.dump(data, f)
        except (OSError, pickle.PickleError) as exc:
            self._append_log("other", f"[GUI] settings save failed: {exc}\n")
            return False

        self._append_log("other", f"[GUI] settings saved: {GUI_SETTINGS_PATH.name}\n")
        return True

    def send_peta_shock(self) -> bool:
        if not self._begin_manual_action("manual_peta_shock"):
            return False
        if self.send_command("p"):
            return True
        self.busy_action = None
        self._update_buttons()
        return False

    def send_peta_shock_latest(self) -> bool:
        if not self._begin_manual_action("manual_peta_shock_latest"):
            return False
        if self.send_command("pl"):
            return True
        self.busy_action = None
        self._update_buttons()
        return False

    def send_peta_read(self) -> bool:
        if not self._begin_manual_action("manual_peta_read"):
            return False
        if self.send_command("r"):
            return True
        self.busy_action = None
        self._update_buttons()
        return False

    def send_backup(self) -> bool:
        if not self._begin_manual_action("manual_backup"):
            return False
        if self.send_command("w"):
            return True
        self.busy_action = None
        self._update_buttons()
        return False

    def send_enqueue(self, auto: bool = False) -> bool:
        default_command = self._build_default_settings_command(auto)
        if default_command is None:
            return False
        if not auto and not self._begin_manual_action("manual_enqueue"):
            return False
        origin = "AUTO" if auto else "GUI"
        if not self.send_command(default_command, origin=origin):
            if not auto:
                if self.enqueue_pending:
                    self.enqueue_pending = False
                else:
                    self.busy_action = None
                self._update_buttons()
            return False
        if self.send_command("e", origin=origin):
            return True
        if not auto:
            if self.enqueue_pending:
                self.enqueue_pending = False
            else:
                self.busy_action = None
            self._update_buttons()
        return False

    def send_peta_next(self, auto: bool = False) -> bool:
        eval_diff = self._get_step2_int_token(
            self.peta_next_eval_diff,
            self.default_eval_diff,
            "peta next eval diff",
            auto,
            non_negative=True,
        )
        if eval_diff is None:
            return False
        max_step = self._get_step2_int_token(
            self.peta_next_max_step,
            self.default_max_step,
            "peta next max step",
            auto,
            positive=True,
        )
        if max_step is None:
            return False
        game_ply_limit = self._get_step2_int_token(
            self.peta_next_ply_limit,
            self.game_ply_limit,
            "peta next game ply limit",
            auto,
            positive=True,
        )
        if game_ply_limit is None:
            return False
        book_extend_ply = self._get_step2_int_token(
            self.peta_next_book_extend_ply,
            self.enqueue_book_extend_ply,
            "peta next book extend ply",
            auto,
            non_negative=True,
        )
        if book_extend_ply is None:
            return False
        eval_limit = self._get_step2_int_token(
            self.peta_next_eval_limit,
            self.eval_limit,
            "peta next eval_limit",
            auto,
            non_negative=True,
        )
        if eval_limit is None:
            return False
        default_command = self._build_default_settings_command(auto)
        if default_command is None:
            return False
        if not auto and not self._begin_manual_action("manual_peta_next"):
            return False
        origin = "AUTO" if auto else "GUI"
        if not self.send_command(default_command, origin=origin):
            if not auto:
                self.busy_action = None
                self._update_buttons()
            return False
        if self.send_command(
            f"pn {eval_diff} {max_step} {game_ply_limit} {book_extend_ply} {eval_limit}",
            origin=origin,
        ):
            return True
        if not auto:
            self.busy_action = None
            self._update_buttons()
        return False

    def send_peta_refutation(self, auto: bool = False) -> bool:
        eval_diff = self._get_step2_int_token(
            self.peta_refutation_eval_diff,
            self.default_eval_diff,
            "peta refutation eval diff",
            auto,
            non_negative=True,
        )
        if eval_diff is None:
            return False
        eval_refutation_margin = self._get_optional_int_token(
            self.peta_refutation_eval_refu,
            "peta refutation eval refu.",
            auto,
            non_negative=True,
        )
        if eval_refutation_margin is None:
            return False
        max_step = self._get_step2_int_token(
            self.peta_refutation_max_step,
            self.default_max_step,
            "peta refutation max step",
            auto,
            positive=True,
        )
        if max_step is None:
            return False
        game_ply_limit = self._get_step2_int_token(
            self.peta_refutation_ply_limit,
            self.game_ply_limit,
            "peta refutation game ply limit",
            auto,
            positive=True,
        )
        if game_ply_limit is None:
            return False
        book_extend_ply = self._get_step2_int_token(
            self.peta_refutation_book_extend_ply,
            self.enqueue_book_extend_ply,
            "peta refutation book extend ply",
            auto,
            non_negative=True,
        )
        if book_extend_ply is None:
            return False
        eval_limit = self._get_step2_int_token(
            self.peta_refutation_eval_limit,
            self.eval_limit,
            "peta refutation eval_limit",
            auto,
            non_negative=True,
        )
        if eval_limit is None:
            return False
        default_command = self._build_default_settings_command(auto)
        if default_command is None:
            return False
        if not auto and not self._begin_manual_action("manual_peta_refutation"):
            return False
        origin = "AUTO" if auto else "GUI"
        if not self.send_command(default_command, origin=origin):
            if not auto:
                self.busy_action = None
                self._update_buttons()
            return False
        if self.send_command(f"pr {eval_refutation_margin} {eval_diff} {max_step} {game_ply_limit} {book_extend_ply} {eval_limit}", origin=origin):
            return True
        if not auto:
            self.busy_action = None
            self._update_buttons()
        return False

    def _build_default_settings_command(self, auto: bool = False) -> str | None:
        specs = [
            (self.default_eval_diff, "default eval_diff", GUI_SETTING_DEFAULTS["default_eval_diff"], False, True),
            (self.default_max_step, "default max step", GUI_SETTING_DEFAULTS["default_max_step"], True, False),
            (self.game_ply_limit, "default game ply limit", GUI_SETTING_DEFAULTS["game_ply_limit"], True, False),
            (self.enqueue_book_extend_ply, "default book extend ply", GUI_SETTING_DEFAULTS["enqueue_book_extend_ply"], False, True),
            (self.eval_limit, "default eval_limit", GUI_SETTING_DEFAULTS["eval_limit"], False, True),
        ]
        tokens: list[str] = []
        for variable, label, fallback, positive, non_negative in specs:
            value = variable.get().strip()
            if not value or value.lower() == "none":
                value = fallback
            try:
                parsed = int(value)
            except ValueError:
                if auto:
                    self._append_log("task", f"[AUTO] {label} must be an integer.\n")
                else:
                    messagebox.showerror("入力エラー", f"{label} には整数を指定してください。")
                return None
            if positive and parsed <= 0:
                if auto:
                    self._append_log("task", f"[AUTO] {label} must be positive.\n")
                else:
                    messagebox.showerror("入力エラー", f"{label} には正の整数を指定してください。")
                return None
            if non_negative and parsed < 0:
                if auto:
                    self._append_log("task", f"[AUTO] {label} must be non-negative.\n")
                else:
                    messagebox.showerror("入力エラー", f"{label} には0以上の整数を指定してください。")
                return None
            tokens.append(str(parsed))
        return "sd " + " ".join(tokens)

    def _get_game_ply_limit(self, auto: bool = False) -> str | None:
        return self._get_optional_int_token(self.game_ply_limit, "game ply limit", auto, positive=True)

    def _get_step2_int_token(
        self,
        variable: tk.StringVar,
        default_variable: tk.StringVar,
        label: str,
        auto: bool = False,
        *,
        positive: bool = False,
        non_negative: bool = False,
    ) -> str | None:
        if variable.get().strip():
            return self._get_optional_int_token(
                variable,
                label,
                auto,
                positive=positive,
                non_negative=non_negative,
            )
        return self._get_optional_int_token(
            default_variable,
            f"default {label}",
            auto,
            positive=positive,
            non_negative=non_negative,
        )

    def _get_optional_int_token(
        self,
        variable: tk.StringVar,
        label: str,
        auto: bool = False,
        *,
        positive: bool = False,
        non_negative: bool = False,
    ) -> str | None:
        value = variable.get().strip()
        if not value or value.lower() == "none":
            return "None"
        try:
            parsed = int(value)
        except ValueError:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be an integer or empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には整数または空欄を指定してください。")
            return None
        if positive and parsed <= 0:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a positive integer or empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には1以上の整数または空欄を指定してください。")
            return None
        if non_negative and parsed < 0:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a non-negative integer or empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には0以上の整数または空欄を指定してください。")
            return None
        return str(parsed)

    def _get_optional_float_token(
        self,
        variable: tk.StringVar,
        label: str,
        auto: bool = False,
        *,
        non_negative: bool = False,
    ) -> str | None:
        value = variable.get().strip()
        if not value or value.lower() == "none":
            return "None"
        try:
            parsed = float(value)
        except ValueError:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a number or empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には数値または空欄を指定してください。")
            return None
        if non_negative and parsed < 0:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a non-negative number or empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には0以上の数値または空欄を指定してください。")
            return None
        return value

    def _get_positive_int(self, variable: tk.StringVar, label: str, auto: bool = False) -> str | None:
        value = variable.get().strip()
        if not value:
            if auto:
                self._append_log("task", f"[AUTO] {label} is empty.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} を指定してください。")
            return None
        try:
            parsed = int(value)
        except ValueError:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a positive integer.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には1以上の整数を指定してください。")
            return None
        if parsed <= 0:
            if auto:
                self._append_log("task", f"[AUTO] {label} must be a positive integer.\n")
            else:
                messagebox.showerror("入力エラー", f"{label} には1以上の整数を指定してください。")
            return None
        return str(parsed)

    def send_peta_unsolved(self, auto: bool = False) -> bool:
        eval_drop_limit = self._get_optional_int_token(
            self.peta_unsolved_eval_drop_limit,
            "peta unsolved eval_drop_limit",
            auto,
            non_negative=True,
        )
        if eval_drop_limit is None:
            return False
        max_step = self._get_step2_int_token(
            self.peta_unsolved_max_step,
            self.default_max_step,
            "peta unsolved max step",
            auto,
            positive=True,
        )
        if max_step is None:
            return False
        game_ply_limit = self._get_step2_int_token(
            self.peta_unsolved_ply_limit,
            self.game_ply_limit,
            "peta unsolved game ply limit",
            auto,
            positive=True,
        )
        if game_ply_limit is None:
            return False
        book_extend_ply = self._get_step2_int_token(
            self.peta_unsolved_book_extend_ply,
            self.enqueue_book_extend_ply,
            "peta unsolved book extend ply",
            auto,
            non_negative=True,
        )
        if book_extend_ply is None:
            return False
        eval_limit = self._get_step2_int_token(
            self.peta_unsolved_eval_limit,
            self.eval_limit,
            "peta unsolved eval_limit",
            auto,
            non_negative=True,
        )
        if eval_limit is None:
            return False
        default_command = self._build_default_settings_command(auto)
        if default_command is None:
            return False
        if not auto and not self._begin_manual_action("manual_peta_unsolved"):
            return False
        origin = "AUTO" if auto else "GUI"
        if not self.send_command(default_command, origin=origin):
            if not auto:
                self.busy_action = None
                self._update_buttons()
            return False
        if self.send_command(f"pu {eval_drop_limit} {max_step} {game_ply_limit} {book_extend_ply} {eval_limit}", origin=origin):
            return True
        if not auto:
            self.busy_action = None
            self._update_buttons()
        return False

    def send_peta_opponent(self, auto: bool = False) -> bool:
        eval_diff = self._get_step2_int_token(
            self.peta_opponent_eval_diff,
            self.default_eval_diff,
            "peta opponent eval_diff",
            auto,
            non_negative=True,
        )
        if eval_diff is None:
            return False
        max_step = self._get_step2_int_token(
            self.peta_opponent_max_step,
            self.default_max_step,
            "peta opponent max step",
            auto,
            positive=True,
        )
        if max_step is None:
            return False
        game_ply_limit = self._get_step2_int_token(
            self.peta_opponent_ply_limit,
            self.game_ply_limit,
            "peta opponent game ply limit",
            auto,
            positive=True,
        )
        if game_ply_limit is None:
            return False
        book_extend_ply = self._get_step2_int_token(
            self.peta_opponent_book_extend_ply,
            self.enqueue_book_extend_ply,
            "peta opponent book extend ply",
            auto,
            non_negative=True,
        )
        if book_extend_ply is None:
            return False
        eval_limit = self._get_step2_int_token(
            self.peta_opponent_eval_limit,
            self.eval_limit,
            "peta opponent eval_limit",
            auto,
            non_negative=True,
        )
        if eval_limit is None:
            return False
        default_command = self._build_default_settings_command(auto)
        if default_command is None:
            return False
        if not auto and not self._begin_manual_action("manual_peta_opponent"):
            return False
        origin = "AUTO" if auto else "GUI"
        if not self.send_command(default_command, origin=origin):
            if not auto:
                self.busy_action = None
                self._update_buttons()
            return False
        if self.send_command(f"po {eval_diff} {max_step} {game_ply_limit} {book_extend_ply} {eval_limit}", origin=origin):
            return True
        if not auto:
            self.busy_action = None
            self._update_buttons()
        return False

    def _append_log(self, key: str, text: str) -> None:
        logs = self.log_widgets.get(key) or self.log_widgets.get("other", [])
        for log in logs:
            log.configure(state="normal")
            log.insert("end", text)
            self._trim_log(log)
            log.see("end")
            log.configure(state="disabled")

    def _trim_log(self, log: scrolledtext.ScrolledText) -> None:
        line_count = int(log.index("end-1c").split(".", 1)[0])
        if line_count <= LOG_TRIM_THRESHOLD:
            return

        delete_to_line = line_count - LOG_MAX_LINES + 1
        log.delete("1.0", f"{delete_to_line}.0")

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _update_buttons(self) -> None:
        running = self.is_running()
        command_state = "normal" if running and self.command_ready else "disabled"

        if not hasattr(self, "next_button"):
            return

        command_enabled = command_state == "normal"
        enqueue_pending = getattr(self, "enqueue_pending", False)
        auto_enqueue_state = getattr(self, "auto_enqueue_state", AUTO_ENQUEUE_IDLE)
        peta_book_busy = (
            self.peta_makebook_active
            or enqueue_pending
            or self.busy_action in {
                "manual_peta_shock",
                "manual_peta_shock_latest",
                "manual_peta_read",
                "manual_peta_next",
                "manual_peta_refutation",
                "manual_peta_unsolved",
                "manual_peta_opponent",
                "auto_enqueue",
            }
        )
        any_busy = (
            self.busy_action is not None
            or enqueue_pending
            or self.peta_makebook_active
            or auto_enqueue_state != AUTO_ENQUEUE_IDLE
        )
        enqueue_allowed_during_peta = self.busy_action in {"manual_peta_shock", "manual_peta_shock_latest", "manual_peta_read"}

        def configure_state(name: str, state: str) -> None:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.configure(state=state)

        configure_state("peta_button", "normal" if command_enabled and not any_busy else "disabled")
        configure_state("peta_latest_button", "normal" if command_enabled and not any_busy else "disabled")
        configure_state("peta_read_button", "normal" if command_enabled and not any_busy else "disabled")
        configure_state("next_button", "normal" if command_enabled and not peta_book_busy else "disabled")
        configure_state("refutation_button", "normal" if command_enabled and not peta_book_busy else "disabled")
        configure_state("unsolved_button", "normal" if command_enabled and not peta_book_busy else "disabled")
        configure_state("opponent_button", "normal" if command_enabled and not peta_book_busy else "disabled")
        configure_state(
            "enqueue_button",
            (
                "normal"
                if command_enabled
                and not enqueue_pending
                and (self.busy_action is None or enqueue_allowed_during_peta)
                and auto_enqueue_state == AUTO_ENQUEUE_IDLE
                else "disabled"
            ),
        )
        configure_state("auto_check", "normal" if command_enabled and not any_busy else "disabled")
        configure_state("write_button", "normal" if command_enabled and not any_busy else "disabled")


def main() -> int:
    parser = argparse.ArgumentParser(description="BookMiner GUI")
    parser.add_argument("--shogidb", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cpp", "--bookminer-cpp", action="store_true", help="use BookMinerCpp.exe instead of BookMiner.py")
    args = parser.parse_args()

    root = tk.Tk()
    root.title("BookMiner GUI" + (" - C++" if args.cpp else ""))
    root.minsize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    gui = BookMinerGui(root, enable_shogidb=args.shogidb, use_cpp=args.cpp)
    configure_initial_window_size(root)

    def on_close() -> None:
        if not messagebox.askyesno(
            "終了確認",
            "BookMinerを終了させます。\n"
            "DBを保存するには [DB手動保存] ボタンを押してから終了させてください。\n"
            "本当に終了させますか？",
        ):
            return

        gui.save_gui_settings()
        if gui.is_running():
            if gui.process is not None:
                gui.process.terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
