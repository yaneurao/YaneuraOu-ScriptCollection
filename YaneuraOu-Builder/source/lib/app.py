from __future__ import annotations

from copy import deepcopy
import os
import pickle
import shlex
import subprocess
import threading
import tkinter as tk
from pathlib import Path, PureWindowsPath
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from .planner import create_plan, validate_plan
from .presets import (
    DEFAULT_DISABLED_RELEASE_EDITIONS,
    PRESET_LABELS,
    PRESET_NAMES,
    RELEASE_EDITIONS,
    create_preset,
)
from .script_writer import resolve_run_root, write_build_run
from .toolchains import WINARM_CROSS_COMPILER, WIN32_CROSS_COMPILER
from .versioning import package_version_from_engine_version


CPU_OPTIONS = {
    "win64": ("SSE41", "SSE42", "AVX2", "ZEN1", "ZEN2", "AVXVNNI", "AVX512", "AVX512VNNI"),
    "win32": ("SSE41", "SSE42", "AVX2", "ZEN1", "ZEN2", "AVXVNNI", "AVX512", "AVX512VNNI"),
    "winarm": ("ARMV8", "ARMV8_DOTPROD"),
    "mac": ("APPLEM1", "APPLEAVX2", "APPLESSE42"),
}

PLATFORM_LABEL_TO_KEY = {
    "Windows x64": "win64",
    "Windows x86": "win32",
    "Windows arm": "winarm",
    "macOS": "mac",
}
PLATFORM_KEY_TO_LABEL = {value: key for key, value in PLATFORM_LABEL_TO_KEY.items()}
PLATFORM_LABELS = tuple(PLATFORM_LABEL_TO_KEY.keys())
BUILD_TARGET = "tournament"
FIXED_BUILD_JOBS = 8
COMPILER_OPTIONS = (WINARM_CROSS_COMPILER, "clang++", "g++", "x86_64-w64-mingw32-g++-posix")
SPSA_MODES = ("none", "tune", "apply")
DEFAULT_MSYS2_ROOT = r"C:\msys64"
STANDARD_VARIANTS = (
    {"name": "DEV", "extra_cppflags": []},
    {"name": "Git", "extra_cppflags": []},
)
STANDARD_VARIANT_NAMES = {str(variant["name"]) for variant in STANDARD_VARIANTS}
MSYS2_SHELL_BY_PLATFORM = {
    "win64": "mingw64.exe",
    "win32": "mingw64.exe",
    "winarm": "mingw64.exe",
}
MSYS2_SYSTEM_BY_PLATFORM = {
    "win64": "MINGW64",
    "win32": "MINGW64",
    "winarm": "MINGW64",
}
GUI_SETTINGS_PATH = Path(__file__).resolve().parents[1] / "yobuild_gui.pickle"
GUI_SETTINGS_VERSION = 2
NORMAL_WINDOW_GEOMETRY = "1280x820"
NORMAL_WINDOW_MINSIZE = (1080, 700)
COMPACT_WINDOW_GEOMETRY = "1040x620"
COMPACT_WINDOW_MINSIZE = (820, 520)
NORMAL_EDITION_HEIGHT = 420
COMPACT_EDITION_HEIGHT = 250
RELEASE_SETTING_TOOLTIPS = {
    "run_name": "生成するrunフォルダ名の元になる名前です。Preset名とは別に、出力runの識別に使います。",
    "run_root": "生成したscript、recipe.json、plan.json、manifest.jsonを置く親フォルダです。",
    "engine_version": "エンジンに埋め込むバージョン文字列です。成果物名に使うPackage versionは V9.41 から V941 のように自動生成されます。",
    "platform": "scriptを生成する対象環境です。選択したplatformに応じてCPU target候補が切り替わります。",
    "source_folder": "ビルド対象のYaneuraOu sourceフォルダです。Makefileがあるsourceディレクトリを指定します。",
    "compiler": "makeに渡すCOMPILERです。Windows armではx64 MSYS2上のaarch64 cross clangを自動使用します。",
    "engine_name": "makeに渡すENGINE_NAMEです。生成エンジンの表示名やビルド設定に使います。",
    "common_cpp_flags": "すべてのvariantとeditionに共通で渡すEXTRA_CPPFLAGSです。空白区切りで指定します。",
    "msys2_root": "MSYS2のインストール先です。Windows x64/x86/armではmingw64.exeをこの下から自動選択します。Windows x86はi686 cross clangを使います。",
    "create_package": "script生成時に頒布用パッケージ作成処理も含めるかを指定します。",
    "package_format": "作成するパッケージ形式です。現在は7zのみ対応しています。",
    "package_exclude": "パッケージ作成時に除外する名前です。空白区切りで指定します。",
    "spsa_mode": "SPSAの前処理を行うかを指定します。noneなら通常ビルドのみです。",
    "spsa_tune_py": "SPSA tune/applyを実行するtune.pyのパスです。",
    "spsa_param_lib": "SPSAで使うParamLib.pyのパスです。",
    "spsa_tune_file": "SPSAの入力.tuneファイルです。",
    "spsa_params_file": "SPSAの入力.paramsファイルです。",
}
class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget, height: int = 220):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, height=height, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_inner_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def set_height(self, height: int) -> None:
        self.canvas.configure(height=height)


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
        x = self.widget.winfo_rootx() + 18
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
            wraplength=520,
        )
        label.pack()

    def hide(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            self.window.destroy()
            self.window = None


class WideStringDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Widget, title: str, prompt: str, initialvalue: str = "", width: int = 54) -> None:
        self.prompt = prompt
        self.initialvalue = initialvalue
        self.width = width
        self.entry: ttk.Entry | None = None
        self.result: str | None = None
        super().__init__(parent, title)

    def body(self, master: tk.Widget) -> tk.Widget | None:
        ttk.Label(master, text=self.prompt).grid(row=0, column=0, sticky="w", padx=4, pady=(4, 6))
        self.entry = ttk.Entry(master, width=self.width)
        self.entry.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.entry.insert(0, self.initialvalue)
        self.entry.select_range(0, "end")
        master.columnconfigure(0, weight=1)
        return self.entry

    def apply(self) -> None:
        if self.entry is not None:
            self.result = self.entry.get()


def _ask_wide_string(
    parent: tk.Widget,
    title: str,
    prompt: str,
    *,
    initialvalue: str = "",
    width: int = 54,
) -> str | None:
    dialog = WideStringDialog(parent, title, prompt, initialvalue=initialvalue, width=width)
    return dialog.result


def load_gui_settings() -> tuple[dict[str, Any], list[str]]:
    messages: list[str] = []
    path = GUI_SETTINGS_PATH
    if path.is_file():
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
        except (OSError, pickle.PickleError, EOFError, AttributeError, TypeError) as exc:
            messages.append(f"settings load failed: {path}: {exc}")
        else:
            if isinstance(data, dict):
                preset_count = len(data.get("presets", {})) if isinstance(data.get("presets"), dict) else 0
                messages.append(f"Loaded settings: {path} ({preset_count} presets)")
                return data, messages
            messages.append(f"settings ignored: {path}: root object is not dict")
    else:
        messages.append(f"settings not found: {path}")
    messages.append("No usable settings file was found. Initializing default presets.")
    return {}, messages


def save_gui_settings(data: dict[str, Any]) -> None:
    GUI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GUI_SETTINGS_PATH, "wb") as f:
        pickle.dump(data, f)


class BuildGui(tk.Tk):
    def __init__(self, yobuild_root: Path):
        super().__init__()
        self.yobuild_root = yobuild_root
        self.title("YO-Build")

        self.gui_settings, self.startup_messages = load_gui_settings()
        self.compact_window = bool(self.gui_settings.get("compact_window", False))
        self._apply_window_geometry()
        self.user_presets = self._load_user_presets(self.gui_settings.get("presets"))
        self._ensure_presets()
        self.preset_values = self._preset_names()
        selected_preset = str(self.gui_settings.get("current_preset", "release-all"))
        if selected_preset not in self.preset_values:
            selected_preset = self.preset_values[0]
        self.current_preset_name = selected_preset
        self.recipe: dict[str, Any] = self._preset_recipe(selected_preset)
        self.form_recipe_kind = str(self.recipe.get("kind", "release_all"))
        self.plan: list[dict[str, Any]] = []
        self.run_active = False
        self.run_mode = ""

        self.cpu_vars: dict[str, tk.BooleanVar] = {}
        self.variant_rows: list[dict[str, Any]] = []
        self.edition_rows: list[dict[str, Any]] = []
        self.write_scripts_button: ttk.Button | None = None
        self.run_msys2_button: ttk.Button | None = None
        self.run_direct_button: ttk.Button | None = None
        self.window_size_button: ttk.Button | None = None

        self._build_widgets()
        self._apply_compact_layout()
        self._load_recipe_into_form()
        for message in self.startup_messages:
            self._log(message)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_user_presets(self, value: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        presets: dict[str, dict[str, Any]] = {}
        for name, recipe in value.items():
            preset_name = str(name).strip()
            if preset_name and isinstance(recipe, dict):
                presets[preset_name] = deepcopy(recipe)
        return presets

    def _default_presets(self) -> dict[str, dict[str, Any]]:
        return {name: create_preset(name, self.yobuild_root) for name in PRESET_NAMES}

    def _ensure_presets(self) -> None:
        if not self.user_presets:
            self.user_presets = self._default_presets()

    def _preset_names(self) -> tuple[str, ...]:
        self._ensure_presets()
        default_names = [name for name in PRESET_NAMES if name in self.user_presets]
        custom_names = sorted(name for name in self.user_presets if name not in PRESET_NAMES)
        return tuple(default_names) + tuple(custom_names)

    def _preset_recipe(self, name: str) -> dict[str, Any]:
        self._ensure_presets()
        recipe = self.user_presets.get(name)
        if isinstance(recipe, dict):
            return deepcopy(recipe)
        fallback_name = self._preset_names()[0]
        return deepcopy(self.user_presets[fallback_name])

    def _refresh_preset_box(self, selected: str | None = None) -> None:
        self.preset_values = self._preset_names()
        self.preset_box.configure(values=self.preset_values)
        if selected not in self.preset_values:
            selected = self.preset_values[0]
        self.current_preset_name = str(selected)
        self.preset_var.set(self.current_preset_name)

    def _save_current_preset_from_form(self, preset_name: str | None = None) -> bool:
        recipe = self._read_recipe_from_form_silent()
        if recipe is None:
            return False
        name = preset_name or self.current_preset_name
        if not name:
            return False
        self.recipe = recipe
        self.user_presets[name] = deepcopy(recipe)
        return True

    def _save_gui_settings(self, *, update_current_preset: bool = True) -> bool:
        if update_current_preset:
            if not self._save_current_preset_from_form():
                self._log(f"settings save warning: current preset was not updated from form: {self.current_preset_name}")
        data = {
            "schema_version": GUI_SETTINGS_VERSION,
            "current_preset": self.current_preset_name,
            "compact_window": self.compact_window,
            "presets": self.user_presets,
        }
        try:
            save_gui_settings(data)
        except OSError as exc:
            self._log(f"settings save failed: {exc}")
            return False
        return True

    def _on_close(self) -> None:
        self._save_gui_settings()
        self.destroy()

    def _apply_window_geometry(self) -> None:
        if self.compact_window:
            self.geometry(COMPACT_WINDOW_GEOMETRY)
            self.minsize(*COMPACT_WINDOW_MINSIZE)
        else:
            self.geometry(NORMAL_WINDOW_GEOMETRY)
            self.minsize(*NORMAL_WINDOW_MINSIZE)

    def _apply_compact_layout(self) -> None:
        if hasattr(self, "edition_scroller"):
            self.edition_scroller.set_height(COMPACT_EDITION_HEIGHT if self.compact_window else NORMAL_EDITION_HEIGHT)
        if hasattr(self, "release_scroller"):
            self.release_scroller.set_height(420 if self.compact_window else 620)
        if self.window_size_button is not None:
            self.window_size_button.configure(text="Normal Window" if self.compact_window else "Small Window")

    def _toggle_window_size(self) -> None:
        self.compact_window = not self.compact_window
        self._apply_window_geometry()
        self._apply_compact_layout()
        self._save_gui_settings(update_current_preset=False)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        self.recipe_tab = ttk.Frame(self.notebook, padding=10)
        self.log_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.recipe_tab, text="Recipe")
        self.notebook.add(self.log_tab, text="Logs")

        self._build_recipe_tab()
        self._build_log_tab()

    def _build_recipe_tab(self) -> None:
        self.recipe_tab.columnconfigure(0, weight=1)
        self.recipe_tab.rowconfigure(1, weight=1)

        top = ttk.Frame(self.recipe_tab)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(2, weight=0)

        ttk.Label(top, text="Preset").grid(row=0, column=0, sticky="w")
        self.preset_var = tk.StringVar(value=self.current_preset_name)
        self.preset_box = ttk.Combobox(
            top,
            textvariable=self.preset_var,
            values=self.preset_values,
            state="readonly",
            width=54,
        )
        self.preset_box.grid(row=0, column=1, padx=(6, 10), sticky="w")
        self.preset_box.bind("<<ComboboxSelected>>", lambda _event: self._select_preset())

        self.window_size_button = ttk.Button(top, command=self._toggle_window_size)
        self.window_size_button.grid(row=0, column=2, sticky="e")

        actions = ttk.Frame(top)
        actions.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Label(actions, text="Preset :").grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        preset_actions = ttk.Frame(actions)
        preset_actions.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Button(preset_actions, text="New Preset", command=self._new_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_actions, text="Clone Preset", command=self._clone_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_actions, text="Rename Preset", command=self._rename_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_actions, text="Delete Preset", command=self._delete_preset).pack(side="left")

        ttk.Label(actions, text="Build :").grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        build_actions = ttk.Frame(actions)
        build_actions.grid(row=1, column=1, sticky="w", pady=2)
        self.write_scripts_button = ttk.Button(build_actions, text="Write Script", command=self._write_scripts)
        self.write_scripts_button.pack(side="left", padx=(0, 6))
        self.run_msys2_button = ttk.Button(build_actions, text="Run with MSYS2", command=self._run_scripts_with_msys2)
        self.run_msys2_button.pack(side="left", padx=(0, 6))
        self.run_direct_button = ttk.Button(build_actions, text="Run Direct", command=self._run_scripts_direct)
        self.run_direct_button.pack(side="left")

        self.release_scroller = ScrollableFrame(self.recipe_tab)
        self.release_scroller.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.release_scroller.inner.columnconfigure(0, weight=1)

        self.release_page = ttk.Frame(self.release_scroller.inner, padding=10)
        self.release_page.grid(row=0, column=0, sticky="nsew")
        self._build_release_page()

    def _build_release_page(self) -> None:
        self.release_page.columnconfigure(0, weight=1)
        self.release_page.rowconfigure(1, weight=1)

        general = ttk.LabelFrame(self.release_page, text="Release settings", padding=8)
        general.grid(row=0, column=0, sticky="ew")
        for column in (1, 3):
            general.columnconfigure(column, weight=1)

        self.name_var = tk.StringVar()
        self.run_root_var = tk.StringVar()
        self.release_version_var = tk.StringVar()
        self.release_platform_var = tk.StringVar()
        self.release_source_var = tk.StringVar()
        self.release_compiler_var = tk.StringVar()
        self.release_engine_name_var = tk.StringVar()
        self.release_common_flags_var = tk.StringVar()
        self.msys2_root_var = tk.StringVar()
        self.package_enabled_var = tk.BooleanVar()
        self.package_format_var = tk.StringVar()
        self.package_exclude_var = tk.StringVar()
        self.spsa_mode_var = tk.StringVar()
        self.spsa_tune_py_var = tk.StringVar()
        self.spsa_param_lib_var = tk.StringVar()
        self.spsa_tune_file_var = tk.StringVar()
        self.spsa_params_file_var = tk.StringVar()

        self._entry(
            general,
            0,
            "Run name",
            self.name_var,
            width=42,
            columnspan=3,
            tooltip=RELEASE_SETTING_TOOLTIPS["run_name"],
        )
        self._path_entry(
            general,
            1,
            "Run root",
            self.run_root_var,
            "dir",
            columnspan=3,
            tooltip=RELEASE_SETTING_TOOLTIPS["run_root"],
        )
        self._entry(
            general,
            2,
            "Engine version",
            self.release_version_var,
            width=20,
            tooltip=RELEASE_SETTING_TOOLTIPS["engine_version"],
        )
        platform_combo = self._combo(
            general,
            3,
            "Platform",
            self.release_platform_var,
            PLATFORM_LABELS,
            width=18,
            state="readonly",
            tooltip=RELEASE_SETTING_TOOLTIPS["platform"],
        )
        platform_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_release_platform_changed())
        self._path_entry(
            general,
            4,
            "YaneuraOu source folder",
            self.release_source_var,
            "dir",
            columnspan=3,
            tooltip=RELEASE_SETTING_TOOLTIPS["source_folder"],
        )
        self._combo(
            general,
            5,
            "Compiler",
            self.release_compiler_var,
            COMPILER_OPTIONS,
            width=24,
            tooltip=RELEASE_SETTING_TOOLTIPS["compiler"],
        )
        self._entry(
            general,
            6,
            "Engine name",
            self.release_engine_name_var,
            width=28,
            tooltip=RELEASE_SETTING_TOOLTIPS["engine_name"],
        )
        self._entry(
            general,
            7,
            "Common CPP flags",
            self.release_common_flags_var,
            width=72,
            columnspan=3,
            tooltip=RELEASE_SETTING_TOOLTIPS["common_cpp_flags"],
        )
        self._path_entry(
            general,
            8,
            "MSYS2 root",
            self.msys2_root_var,
            "dir",
            columnspan=3,
            tooltip=RELEASE_SETTING_TOOLTIPS["msys2_root"],
        )

        package = ttk.Frame(general)
        package.grid(row=9, column=1, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Checkbutton(package, text="Create package", variable=self.package_enabled_var).pack(side="left")
        self._pack_info_icon(package, RELEASE_SETTING_TOOLTIPS["create_package"])
        ttk.Label(package, text="Format").pack(side="left", padx=(14, 4))
        self._pack_info_icon(package, RELEASE_SETTING_TOOLTIPS["package_format"], padx=(0, 4))
        ttk.Combobox(package, textvariable=self.package_format_var, values=("7z",), state="readonly", width=8).pack(side="left")
        ttk.Label(package, text="Exclude").pack(side="left", padx=(14, 4))
        self._pack_info_icon(package, RELEASE_SETTING_TOOLTIPS["package_exclude"], padx=(0, 4))
        ttk.Entry(package, textvariable=self.package_exclude_var, width=32).pack(side="left")

        spsa_box = ttk.LabelFrame(general, text="SPSA preprocessing", padding=8)
        spsa_box.grid(row=10, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        spsa_box.columnconfigure(1, weight=1)
        self.spsa_box = spsa_box
        self._combo(
            spsa_box,
            0,
            "Mode",
            self.spsa_mode_var,
            SPSA_MODES,
            width=12,
            state="readonly",
            tooltip=RELEASE_SETTING_TOOLTIPS["spsa_mode"],
        ).bind("<<ComboboxSelected>>", lambda _event: self._on_spsa_mode_changed())
        self._path_entry(
            spsa_box,
            1,
            "tune.py",
            self.spsa_tune_py_var,
            "file",
            tooltip=RELEASE_SETTING_TOOLTIPS["spsa_tune_py"],
        )
        self._path_entry(
            spsa_box,
            2,
            "ParamLib.py",
            self.spsa_param_lib_var,
            "file",
            tooltip=RELEASE_SETTING_TOOLTIPS["spsa_param_lib"],
        )
        self._path_entry(
            spsa_box,
            3,
            ".tune file",
            self.spsa_tune_file_var,
            "file",
            tooltip=RELEASE_SETTING_TOOLTIPS["spsa_tune_file"],
        )
        self._path_entry(
            spsa_box,
            4,
            ".params file",
            self.spsa_params_file_var,
            "file",
            tooltip=RELEASE_SETTING_TOOLTIPS["spsa_params_file"],
        )

        matrix = ttk.Frame(self.release_page)
        matrix.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        matrix.columnconfigure(0, weight=1)
        matrix.columnconfigure(1, weight=2)
        matrix.rowconfigure(1, weight=1)

        left = ttk.Frame(matrix)
        left.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)

        variant_box = ttk.LabelFrame(left, text="Variants", padding=8)
        variant_box.grid(row=0, column=0, sticky="ew")
        variant_box.columnconfigure(1, weight=1)
        ttk.Label(variant_box, text="Use").grid(row=0, column=0, sticky="w")
        ttk.Label(variant_box, text="Name").grid(row=0, column=1, sticky="w")
        ttk.Label(variant_box, text="Extra CPP flags").grid(row=0, column=2, sticky="w")
        self.variant_box = variant_box
        self.variant_frame = variant_box

        cpu_box = ttk.LabelFrame(left, text="CPU targets", padding=8)
        cpu_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        cpu_box.columnconfigure(0, weight=1)
        self.cpu_frame = cpu_box

        edition_box = ttk.LabelFrame(matrix, text="Editions", padding=8)
        edition_box.grid(row=0, column=1, rowspan=2, sticky="nsew")
        edition_box.columnconfigure(0, weight=1)
        edition_box.rowconfigure(1, weight=1)
        self.edition_box = edition_box

        edition_buttons = ttk.Frame(edition_box)
        edition_buttons.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(edition_buttons, text="Enable all", command=lambda: self._set_all_editions(True)).pack(side="left")
        ttk.Button(edition_buttons, text="Disable all", command=lambda: self._set_all_editions(False)).pack(side="left", padx=(6, 0))

        self.edition_scroller = ScrollableFrame(edition_box, height=420)
        self.edition_scroller.grid(row=1, column=0, sticky="nsew")

    def _build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(self.log_tab, wrap="word", font=("Consolas", 10))
        yscroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

    def _entry(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        variable: tk.StringVar,
        *,
        width: int,
        columnspan: int = 1,
        tooltip: str | None = None,
    ) -> ttk.Entry:
        self._field_label(parent, row, label, tooltip)
        entry = ttk.Entry(parent, textvariable=variable, width=width)
        entry.grid(row=row, column=1, columnspan=columnspan, sticky="ew", pady=4)
        return entry

    def _combo(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
        *,
        width: int,
        state: str | None = None,
        tooltip: str | None = None,
    ) -> ttk.Combobox:
        self._field_label(parent, row, label, tooltip)
        combo = ttk.Combobox(parent, textvariable=variable, values=values, width=width)
        if state is not None:
            combo.configure(state=state)
        combo.grid(row=row, column=1, sticky="w", pady=4)
        return combo

    def _path_entry(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        variable: tk.StringVar,
        mode: str,
        *,
        columnspan: int = 1,
        tooltip: str | None = None,
    ) -> None:
        self._field_label(parent, row, label, tooltip)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, columnspan=columnspan, sticky="ew", pady=4)
        command = {
            "dir": lambda: self._browse_dir(variable),
            "file": lambda: self._browse_file(variable),
            "save": lambda: self._browse_save(variable),
        }[mode]
        ttk.Button(parent, text="Browse", command=command).grid(row=row, column=2 + columnspan - 1, sticky="w", padx=(6, 0))

    def _field_label(self, parent: tk.Widget, row: int, label: str, tooltip: str | None = None) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Label(frame, text=label).pack(side="left")
        self._pack_info_icon(frame, tooltip)

    def _pack_info_icon(
        self,
        parent: tk.Widget,
        tooltip: str | None,
        *,
        padx: tuple[int, int] = (4, 0),
    ) -> None:
        if not tooltip:
            return
        icon = ttk.Label(parent, text="❔", cursor="hand2")
        icon.pack(side="left", padx=padx)
        Tooltip(icon, tooltip)

    def _select_preset(self) -> None:
        name = self.preset_var.get().strip()
        if not name or name == self.current_preset_name:
            return
        if not self._save_current_preset_from_form(self.current_preset_name):
            self._log(f"Preset switch warning: current preset was not saved: {self.current_preset_name}")
        self.current_preset_name = name
        self.recipe = self._preset_recipe(name)
        self.plan = []
        self._load_recipe_into_form()
        self._clear_plan()
        self._save_gui_settings(update_current_preset=False)
        self._log(f"Loaded preset: {PRESET_LABELS.get(name, name)}")

    def _new_preset(self) -> None:
        recipe = self._read_recipe_from_form()
        if recipe is None:
            return
        name = _ask_wide_string(
            self,
            "New Preset",
            "Preset name:",
            initialvalue=str(recipe.get("name", self.current_preset_name)),
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("New Preset", "Preset name is required.")
            return
        if name in self.user_presets:
            if not messagebox.askyesno("New Preset", f"Overwrite preset '{name}'?"):
                return
        recipe = deepcopy(recipe)
        recipe["name"] = name
        self.user_presets[name] = recipe
        self.recipe = deepcopy(recipe)
        self.plan = []
        self._refresh_preset_box(name)
        self._load_recipe_into_form()
        self._clear_plan()
        self._save_gui_settings(update_current_preset=False)
        self._log(f"Saved preset: {name}")

    def _clone_preset(self) -> None:
        source_name = self.current_preset_name
        source_recipe = self._read_recipe_from_form()
        if source_recipe is None:
            return
        name = _ask_wide_string(
            self,
            "Clone Preset",
            "New preset name:",
            initialvalue=self._default_clone_preset_name(source_name),
        )
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("Clone Preset", "Preset name is required.")
            return
        if name in self.user_presets:
            messagebox.showerror("Clone Preset", f"Preset '{name}' already exists.")
            return

        recipe = deepcopy(source_recipe)
        recipe["name"] = name
        self.user_presets[name] = recipe
        self.recipe = deepcopy(recipe)
        self.plan = []
        self._refresh_preset_box(name)
        self._load_recipe_into_form()
        self._clear_plan()
        self._save_gui_settings(update_current_preset=False)
        self._log(f"Cloned preset: {source_name} -> {name}")

    def _default_clone_preset_name(self, source_name: str) -> str:
        base = f"{source_name}-copy"
        name = base
        index = 2
        existing = set(self.user_presets)
        while name in existing:
            name = f"{base}-{index}"
            index += 1
        return name

    def _rename_preset(self) -> None:
        old_name = self.current_preset_name
        if old_name not in self.user_presets:
            messagebox.showinfo("Rename Preset", f"'{old_name}' has no saved preset in {GUI_SETTINGS_PATH.name}.")
            return
        recipe = self._read_recipe_from_form()
        if recipe is None:
            return
        new_name = _ask_wide_string(
            self,
            "Rename Preset",
            "New preset name:",
            initialvalue=old_name,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            messagebox.showerror("Rename Preset", "Preset name is required.")
            return
        if new_name == old_name:
            return
        if new_name in self.user_presets:
            messagebox.showerror("Rename Preset", f"Preset '{new_name}' already exists.")
            return

        recipe = deepcopy(recipe)
        recipe["name"] = new_name
        del self.user_presets[old_name]
        self.user_presets[new_name] = recipe
        self.recipe = deepcopy(recipe)
        self.plan = []
        self._refresh_preset_box(new_name)
        self._load_recipe_into_form()
        self._clear_plan()
        self._save_gui_settings(update_current_preset=False)
        self._log(f"Renamed preset: {old_name} -> {new_name}")

    def _delete_preset(self) -> None:
        name = self.current_preset_name
        if name not in self.user_presets:
            messagebox.showinfo("Delete Preset", f"'{name}' has no saved preset in {GUI_SETTINGS_PATH.name}.")
            return
        if not messagebox.askyesno("Delete Preset", f"Delete preset '{name}'?"):
            return
        del self.user_presets[name]
        self._ensure_presets()
        selected = self._preset_names()[0]
        self.recipe = self._preset_recipe(selected)
        self.plan = []
        self._refresh_preset_box(selected)
        self._load_recipe_into_form()
        self._clear_plan()
        self._save_gui_settings(update_current_preset=False)
        self._log(f"Deleted preset: {name}")

    def _load_recipe_into_form(self) -> None:
        if self.recipe.get("kind") == "bookminer_cpp":
            self._load_bookminer_cpp_form(self.recipe)
            return
        recipe = self._release_recipe_for_form(self.recipe)
        self._load_release_form(recipe)

    def _release_recipe_for_form(self, recipe: dict[str, Any]) -> dict[str, Any]:
        if recipe.get("kind") == "release_all":
            return recipe
        if recipe.get("kind") in {"single_build", "spsa_build"}:
            return self._single_recipe_as_release(recipe)
        return create_preset("release-all", self.yobuild_root)

    def _load_bookminer_cpp_form(self, recipe: dict[str, Any]) -> None:
        platforms = recipe.get("platforms", ["win64"])
        platform_name = str(platforms[0]) if platforms else "win64"
        bookminer_cpp_source_dir = str(recipe.get("bookminer_cpp_source_dir", "")).strip()
        if not bookminer_cpp_source_dir:
            detected = _bookminer_cpp_source_dir(self.yobuild_root)
            if detected is not None:
                bookminer_cpp_source_dir = str(detected)
        self.form_recipe_kind = "bookminer_cpp"
        self.name_var.set(str(recipe.get("name", "")))
        self.run_root_var.set(_normalized_run_root_text(recipe.get("run_root", self.yobuild_root / "runs"), self.yobuild_root))
        self.release_version_var.set(str(recipe.get("version", "")))
        self.release_compiler_var.set(str(recipe.get("compiler", "clang++")))
        self.release_engine_name_var.set("BookMinerCpp")
        self.release_common_flags_var.set(_join_flags(recipe.get("common_cppflags", [])))
        self.msys2_root_var.set(_msys2_root_from_recipe(recipe))
        self.package_enabled_var.set(False)
        self.package_format_var.set("7z")
        self.package_exclude_var.set("obj")
        self.spsa_mode_var.set("none")
        self.spsa_tune_py_var.set("")
        self.spsa_param_lib_var.set("")
        self.spsa_tune_file_var.set("")
        self.spsa_params_file_var.set("")
        self.release_platform_var.set(PLATFORM_KEY_TO_LABEL.get(platform_name, "Windows x64"))
        self.release_source_var.set(str(recipe.get("source_dir", "")))
        self._apply_platform_compiler_defaults(platform_name)
        self._rebuild_variant_rows([])
        self._rebuild_cpu_rows(recipe.get("cpus", {}), platform_name)
        self._rebuild_edition_rows([])
        self._apply_recipe_kind_layout("bookminer_cpp")

    def _single_recipe_as_release(self, recipe: dict[str, Any]) -> dict[str, Any]:
        source_dir = str(recipe.get("source_dir", ""))
        target_cpu = str(recipe.get("target_cpu", "AVX2"))
        platform_name = _platform_for_single_recipe(source_dir, target_cpu)
        source_key = "win" if "win" in platform_name else platform_name
        edition = str(recipe.get("edition", ""))
        artifact_prefix = _path_stem(str(recipe.get("output_path", "")))
        if not artifact_prefix:
            artifact_prefix = "YO-MATERIAL" if "MATERIAL" in edition else edition or "YaneuraOu"
        spsa = {
            "mode": "none",
            "tune_py": "",
            "param_lib": "",
            "tune_file": "",
            "params_file": "",
        }
        if recipe.get("kind") == "spsa_build":
            spsa = {
                "mode": str(recipe.get("tune_mode", "apply")),
                "tune_py": str(recipe.get("tune_py", "")),
                "param_lib": str(recipe.get("param_lib", "")),
                "tune_file": str(recipe.get("tune_file", "")),
                "params_file": str(recipe.get("params_file", "")),
            }
        return {
            "schema_version": 1,
            "kind": "release_all",
            "name": str(recipe.get("name", "release-build")),
            "run_root": str(recipe.get("run_root", self.yobuild_root / "runs")),
            "version": str(recipe.get("version", "")),
            "package_version": package_version_from_engine_version(str(recipe.get("version", ""))),
            "source_dirs": {source_key: source_dir},
            "platforms": [platform_name],
            "variants": [{"name": "Git", "extra_cppflags": [], "enabled": True}],
            "target": BUILD_TARGET,
            "compiler": str(recipe.get("compiler", "clang++")),
            "jobs": FIXED_BUILD_JOBS,
            "engine_name": str(recipe.get("engine_name", "YaneuraOu")),
            "common_cppflags": list(recipe.get("common_cppflags", [])),
            "material_level": recipe.get("material_level"),
            "msys2_root": _msys2_root_from_recipe(recipe),
            "cpus": {platform_name: [target_cpu]},
            "editions": [
                {
                    "edition": edition,
                    "artifact_prefix": artifact_prefix,
                    "enabled": True,
                }
            ],
            "package": {
                "enabled": False,
                "format": "7z",
                "exclude": ["obj"],
            },
            "spsa": spsa,
        }

    def _recipe_platform(self, recipe: dict[str, Any]) -> str:
        for platform_name in recipe.get("platforms", []):
            platform_text = str(platform_name)
            if platform_text in PLATFORM_KEY_TO_LABEL:
                return platform_text
        return "win64"

    def _release_platform_key(self) -> str:
        label = self.release_platform_var.get().strip()
        return PLATFORM_LABEL_TO_KEY.get(label, "win64")

    def _on_release_platform_changed(self) -> None:
        platform_name = self._release_platform_key()
        self._rebuild_cpu_rows({}, platform_name)
        self._apply_platform_compiler_defaults(platform_name)

    def _on_spsa_mode_changed(self) -> None:
        return

    def _apply_platform_compiler_defaults(self, platform_name: str) -> None:
        if platform_name == "winarm":
            self.release_compiler_var.set(WINARM_CROSS_COMPILER)
        elif platform_name == "win32":
            self.release_compiler_var.set(WIN32_CROSS_COMPILER)
        elif self.release_compiler_var.get().strip() in {WINARM_CROSS_COMPILER, WIN32_CROSS_COMPILER}:
            self.release_compiler_var.set("clang++")

    def _load_release_form(self, recipe: dict[str, Any]) -> None:
        release_recipe = recipe
        self.form_recipe_kind = str(release_recipe.get("kind", "release_all"))
        source_dirs = release_recipe.get("source_dirs", {})
        package = release_recipe.get("package", {})
        spsa = release_recipe.get("spsa", {})

        self.name_var.set(str(release_recipe.get("name", "")))
        self.run_root_var.set(_normalized_run_root_text(release_recipe.get("run_root", self.yobuild_root / "runs"), self.yobuild_root))
        self.release_version_var.set(str(release_recipe.get("version", "")))
        self.release_compiler_var.set(str(release_recipe.get("compiler", "clang++")))
        self.release_engine_name_var.set(str(release_recipe.get("engine_name", "YaneuraOu")))
        self.release_common_flags_var.set(_join_flags(release_recipe.get("common_cppflags", [])))
        self.msys2_root_var.set(_msys2_root_from_recipe(release_recipe))
        self.package_enabled_var.set(bool(package.get("enabled", True)))
        self.package_format_var.set(str(package.get("format", "7z")))
        self.package_exclude_var.set(_join_flags(package.get("exclude", ["obj"])))
        self.spsa_mode_var.set(str(spsa.get("mode", "none")))
        self.spsa_tune_py_var.set(str(spsa.get("tune_py", "")))
        self.spsa_param_lib_var.set(str(spsa.get("param_lib", "")))
        self.spsa_tune_file_var.set(str(spsa.get("tune_file", "")))
        self.spsa_params_file_var.set(str(spsa.get("params_file", "")))

        platform_name = self._recipe_platform(release_recipe)
        self.release_platform_var.set(PLATFORM_KEY_TO_LABEL.get(platform_name, "Windows x64"))
        source_key = "win" if "win" in platform_name else platform_name
        self.release_source_var.set(str(source_dirs.get(platform_name) or source_dirs.get(source_key, "")))
        self._apply_platform_compiler_defaults(platform_name)

        self._rebuild_variant_rows(release_recipe.get("variants", []))
        self._rebuild_cpu_rows(release_recipe.get("cpus", {}), platform_name)
        self._rebuild_edition_rows(release_recipe.get("editions", []))
        self._apply_recipe_kind_layout(str(release_recipe.get("kind", "release_all")))

    def _apply_recipe_kind_layout(self, kind: str) -> None:
        bookminer_cpp = kind == "bookminer_cpp"
        if hasattr(self, "variant_box"):
            if bookminer_cpp:
                self.variant_box.grid_remove()
            else:
                self.variant_box.grid()
        if hasattr(self, "cpu_frame"):
            if bookminer_cpp:
                self.cpu_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 0))
            else:
                self.cpu_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        if hasattr(self, "edition_box"):
            if bookminer_cpp:
                self.edition_box.grid_remove()
            else:
                self.edition_box.grid()
        if hasattr(self, "spsa_box"):
            if bookminer_cpp:
                self.spsa_box.grid_remove()
            else:
                self.spsa_box.grid()

    def _rebuild_variant_rows(self, variants: list[dict[str, Any]]) -> None:
        for child in self.variant_frame.grid_slaves():
            row = int(child.grid_info().get("row", 0))
            if row > 0:
                child.destroy()
        self.variant_rows = []
        variants = _normalize_variants_for_form(variants)
        for row_index, variant in enumerate(variants, start=1):
            enabled = tk.BooleanVar(value=variant.get("enabled", True))
            variant_name = str(variant.get("name", ""))
            name = tk.StringVar(value=variant_name)
            flags = tk.StringVar(value=_join_flags(variant.get("extra_cppflags", [])))
            ttk.Checkbutton(self.variant_frame, variable=enabled).grid(row=row_index, column=0, sticky="w", pady=2)
            name_entry = ttk.Entry(
                self.variant_frame,
                textvariable=name,
                width=12,
                state="readonly" if variant_name in STANDARD_VARIANT_NAMES else "normal",
            )
            name_entry.grid(row=row_index, column=1, sticky="ew", padx=(0, 6), pady=2)
            ttk.Entry(self.variant_frame, textvariable=flags, width=34).grid(row=row_index, column=2, sticky="ew", pady=2)
            self.variant_rows.append({"enabled": enabled, "name": name, "flags": flags})

    def _rebuild_cpu_rows(self, cpus_by_platform: dict[str, Any], platform_name: str | None = None) -> None:
        for child in self.cpu_frame.winfo_children():
            child.destroy()
        self.cpu_vars = {}
        platform_name = platform_name or self._release_platform_key()
        label = PLATFORM_KEY_TO_LABEL.get(platform_name, platform_name)
        known = list(CPU_OPTIONS.get(platform_name, ()))
        has_selection = platform_name in cpus_by_platform
        selected = [str(cpu) for cpu in cpus_by_platform.get(platform_name, [])]
        if not selected and "win" in platform_name and "win" in cpus_by_platform:
            has_selection = True
            selected = [str(cpu) for cpu in cpus_by_platform.get("win", [])]
        if not has_selection:
            selected = list(known)
        for cpu in selected:
            if cpu not in known:
                known.append(cpu)

        header = ttk.Frame(self.cpu_frame)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(header, text=label).pack(side="left")
        ttk.Button(header, text="Enable All", command=lambda: self._set_all_cpus(True)).pack(side="left", padx=(12, 0))
        ttk.Button(header, text="Disable All", command=lambda: self._set_all_cpus(False)).pack(side="left", padx=(6, 0))
        row = ttk.Frame(self.cpu_frame)
        row.grid(row=1, column=0, sticky="ew")
        for cpu in known:
            var = tk.BooleanVar(value=cpu in selected)
            self.cpu_vars[cpu] = var
            ttk.Checkbutton(row, text=cpu, variable=var).pack(side="left", padx=(0, 8), pady=2)

    def _rebuild_edition_rows(self, editions: list[dict[str, Any]]) -> None:
        for child in self.edition_scroller.inner.winfo_children():
            child.destroy()
        self.edition_rows = []
        header = ttk.Frame(self.edition_scroller.inner)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=2)
        header.columnconfigure(2, weight=1)
        ttk.Label(header, text="Use", width=5).grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="YANEURAOU_EDITION").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Artifact prefix").grid(row=0, column=2, sticky="w")

        fixed_editions = _normalize_editions_for_form(editions)
        for row_index, edition in enumerate(fixed_editions, start=1):
            row = ttk.Frame(self.edition_scroller.inner)
            row.grid(row=row_index, column=0, sticky="ew", pady=2)
            row.columnconfigure(1, weight=2)
            row.columnconfigure(2, weight=1)
            enabled = tk.BooleanVar(value=edition.get("enabled", True))
            edition_name = str(edition.get("edition", ""))
            artifact_prefix = str(edition.get("artifact_prefix", ""))
            ttk.Checkbutton(row, variable=enabled).grid(row=0, column=0, sticky="w")
            ttk.Label(row, text=edition_name).grid(row=0, column=1, sticky="w", padx=(0, 6))
            ttk.Label(row, text=artifact_prefix).grid(row=0, column=2, sticky="w")
            self.edition_rows.append({"enabled": enabled, "edition": edition_name, "artifact": artifact_prefix})

    def _set_all_editions(self, enabled: bool) -> None:
        for row in self.edition_rows:
            row["enabled"].set(enabled)

    def _set_all_cpus(self, enabled: bool) -> None:
        for var in self.cpu_vars.values():
            var.set(enabled)

    def _read_recipe_from_form(self) -> dict[str, Any] | None:
        try:
            if self.form_recipe_kind == "bookminer_cpp":
                recipe = self._bookminer_cpp_recipe_from_form()
            else:
                recipe = self._release_recipe_from_form()
        except ValueError as exc:
            messagebox.showerror("Recipe error", str(exc))
            return None
        self.recipe = recipe
        return recipe

    def _base_recipe_from_form(self, kind: str, *, require_name: bool = True) -> dict[str, Any]:
        name = self.name_var.get().strip()
        if require_name and not name:
            raise ValueError("Run name is required.")
        return {
            "schema_version": 1,
            "kind": kind,
            "name": name,
            "run_root": _normalized_run_root_text(
                self.run_root_var.get().strip() or str(self.yobuild_root / "runs"),
                self.yobuild_root,
            ),
        }

    def _release_recipe_from_form(self) -> dict[str, Any]:
        recipe = self._base_recipe_from_form("release_all")
        platform_name = self._release_platform_key()
        source_key = "win" if "win" in platform_name else platform_name
        variants = []
        enabled_variants = 0
        for row in self.variant_rows:
            name = row["name"].get().strip()
            enabled = bool(row["enabled"].get())
            if enabled and not name:
                raise ValueError("Variant name is required for enabled rows.")
            if name:
                variants.append(
                    {
                        "name": name,
                        "extra_cppflags": _split_words(row["flags"].get()),
                        "enabled": enabled,
                    }
                )
                if enabled:
                    enabled_variants += 1
        if not enabled_variants:
            raise ValueError("At least one variant is required.")
        editions = []
        for row in self.edition_rows:
            edition = str(row["edition"]).strip()
            artifact = str(row["artifact"]).strip()
            if edition and artifact:
                editions.append(
                    {
                        "edition": edition,
                        "artifact_prefix": artifact,
                        "enabled": bool(row["enabled"].get()),
                    }
                )
        selected_cpus = [cpu for cpu, var in self.cpu_vars.items() if var.get()]
        if not selected_cpus:
            raise ValueError("At least one CPU target is required.")
        engine_version = _required(self.release_version_var.get(), "Engine version")
        recipe.update(
            {
                "version": engine_version,
                "package_version": package_version_from_engine_version(engine_version),
                "source_dirs": {source_key: _required(self.release_source_var.get(), "YaneuraOu source folder")},
                "platforms": [platform_name],
                "variants": variants,
                "target": BUILD_TARGET,
                "compiler": _required(self.release_compiler_var.get(), "Compiler"),
                "jobs": FIXED_BUILD_JOBS,
                "engine_name": self.release_engine_name_var.get().strip() or "YaneuraOu",
                "common_cppflags": _split_words(self.release_common_flags_var.get()),
                "msys2_root": self.msys2_root_var.get().strip() or DEFAULT_MSYS2_ROOT,
                "cpus": {platform_name: selected_cpus},
                "editions": editions,
                "package": {
                    "enabled": self.package_enabled_var.get(),
                    "format": self.package_format_var.get().strip() or "7z",
                    "exclude": _split_words(self.package_exclude_var.get()) or ["obj"],
                },
                "spsa": self._spsa_recipe_from_release_form(),
            }
        )
        return recipe

    def _bookminer_cpp_recipe_from_form(self) -> dict[str, Any]:
        recipe = self._base_recipe_from_form("bookminer_cpp", require_name=False)
        platform_name = self._release_platform_key()
        selected_cpus = [cpu for cpu, var in self.cpu_vars.items() if var.get()]
        bookminer_cpp_source_dir = str(self.recipe.get("bookminer_cpp_source_dir", "")).strip()
        if not bookminer_cpp_source_dir:
            detected = _bookminer_cpp_source_dir(self.yobuild_root)
            if detected is not None:
                bookminer_cpp_source_dir = str(detected)
        recipe.update(
            {
                "source_dir": self.release_source_var.get().strip(),
                "bookminer_cpp_source_dir": bookminer_cpp_source_dir,
                "version": self.release_version_var.get().strip(),
                "common_cppflags": _split_words(self.release_common_flags_var.get()),
                "platforms": [platform_name],
                "compiler": self.release_compiler_var.get().strip(),
                "jobs": FIXED_BUILD_JOBS,
                "msys2_root": self.msys2_root_var.get().strip() or DEFAULT_MSYS2_ROOT,
                "cpus": {platform_name: selected_cpus},
            }
        )
        return recipe

    def _spsa_recipe_from_release_form(self) -> dict[str, str]:
        mode = self.spsa_mode_var.get().strip() or "none"
        if mode not in SPSA_MODES:
            raise ValueError("SPSA mode must be none, tune, or apply.")
        if mode == "none":
            return {
                "mode": "none",
                "tune_py": "",
                "param_lib": "",
                "tune_file": "",
                "params_file": "",
            }
        return {
            "mode": mode,
            "tune_py": _required(self.spsa_tune_py_var.get(), "tune.py"),
            "param_lib": _required(self.spsa_param_lib_var.get(), "ParamLib.py"),
            "tune_file": _required(self.spsa_tune_file_var.get(), ".tune file"),
            "params_file": _required(self.spsa_params_file_var.get(), ".params file"),
        }

    def _create_plan_from_form(self) -> None:
        recipe = self._read_recipe_from_form()
        if recipe is None:
            return
        try:
            plan = create_plan(recipe)
            warnings = validate_plan(recipe, plan)
        except ValueError as exc:
            messagebox.showerror("Create plan failed", str(exc))
            return
        self.recipe = recipe
        self.plan = plan
        self._save_gui_settings()
        self._log(f"Created build plan: {len(plan)} jobs. Details will be written to plan.json.")
        for warning in warnings:
            self._log(f"warning: {warning}")

    def _write_scripts(self, *, show_message: bool = True) -> Path | None:
        self._create_plan_from_form()
        if not self.plan:
            return None
        try:
            run_dir = write_build_run(self.recipe, self.plan, self.yobuild_root)
        except OSError as exc:
            messagebox.showerror("Write scripts failed", str(exc))
            return None
        self._log(f"Wrote scripts: {run_dir}")
        self._log(f"Run helper: {run_dir / 'run-all'}")
        if show_message:
            messagebox.showinfo("Write scripts", f"Wrote scripts to:\n{run_dir}\n\nManual run:\ncd {run_dir} && ./run-all")
        return run_dir

    def _run_scripts_with_msys2(self) -> None:
        if self.run_active:
            return
        platform_name = self._release_platform_key()
        if "win" not in platform_name:
            messagebox.showerror("Run with MSYS2", "MSYS2 execution is only for Windows platforms.")
            return
        msys2_root = _msys2_root_from_legacy_shell(self.msys2_root_var.get().strip() or DEFAULT_MSYS2_ROOT)
        launcher_path = _msys2_shell_path(msys2_root, platform_name)
        bash_path = _msys2_bash_path(msys2_root)
        msystem = _msys2_msystem(platform_name)
        if not bash_path:
            messagebox.showerror("Run with MSYS2", "MSYS2 root is required.")
            return
        self._set_run_active(True, "msys2")
        self.notebook.select(self.log_tab)
        started = False
        try:
            run_dir = self._write_scripts(show_message=False)
            if run_dir is None:
                return
            scripts_dir = run_dir / "scripts"
            scripts = sorted(path for path in scripts_dir.iterdir() if path.is_file())
            if not scripts:
                messagebox.showerror("Run with MSYS2", f"No scripts were generated under:\n{scripts_dir}")
                return
            self._log(f"Starting MSYS2 run with: {launcher_path}")
            msystem_prefix = _msys2_msystem_prefix(platform_name)
            self._log(f"[run] executing via: {bash_path} (MSYSTEM={msystem})")
            self._log(f"[run] MSYS2 PATH prefix: {msystem_prefix}/bin")
            thread = threading.Thread(
                target=self._run_scripts_thread,
                args=(bash_path, msystem, msystem_prefix, scripts),
                daemon=True,
            )
            thread.start()
            started = True
        finally:
            if not started:
                self._set_run_active(False)

    def _run_scripts_direct(self) -> None:
        if self.run_active:
            return
        platform_name = self._release_platform_key()
        if "win" in platform_name:
            messagebox.showerror("Run Direct", "Direct execution is for macOS scripts. Use MSYS2 for Windows platforms.")
            return

        self._set_run_active(True, "direct")
        self.notebook.select(self.log_tab)
        started = False
        try:
            run_dir = self._write_scripts(show_message=False)
            if run_dir is None:
                return
            scripts_dir = run_dir / "scripts"
            scripts = sorted(path for path in scripts_dir.iterdir() if path.is_file())
            if not scripts:
                messagebox.showerror("Run Direct", f"No scripts were generated under:\n{scripts_dir}")
                return
            self._log(f"Starting direct run: {run_dir}")
            self._log(f"[run] zsh/manual helper: cd {run_dir} && ./run-all")
            thread = threading.Thread(target=self._run_scripts_direct_thread, args=(scripts,), daemon=True)
            thread.start()
            started = True
        finally:
            if not started:
                self._set_run_active(False)

    def _run_scripts_thread(self, bash_path: str, msystem: str, msystem_prefix: str, scripts: list[Path]) -> None:
        env = os.environ.copy()
        env["MSYSTEM"] = msystem
        env["MSYSTEM_PREFIX"] = msystem_prefix
        env.setdefault("CHERE_INVOKING", "1")
        path_prefix = f"export PATH={shlex.quote(msystem_prefix + '/bin')}:/usr/bin:$PATH; "
        self._run_script_subprocesses(
            scripts,
            lambda script: [bash_path, "-lc", path_prefix + f"bash {shlex.quote('./' + script.name)}"],
            cwd=lambda script: script.parent,
            env=env,
            completion_title="Run with MSYS2",
        )

    def _run_scripts_direct_thread(self, scripts: list[Path]) -> None:
        def command(script: Path) -> list[str]:
            mode = script.stat().st_mode
            script.chmod(mode | 0o111)
            return [str(script)]

        self._run_script_subprocesses(
            scripts,
            command,
            cwd=lambda script: script.parent,
            env=os.environ.copy(),
            completion_title="Run Direct",
        )

    def _run_script_subprocesses(
        self,
        scripts: list[Path],
        command_for_script: Any,
        *,
        cwd: Any,
        env: dict[str, str],
        completion_title: str,
    ) -> None:
        success = False
        error_message = ""
        try:
            for script in scripts:
                self.after(0, self._log, f"[run] {script}")
                try:
                    process = subprocess.Popen(
                        command_for_script(script),
                        cwd=cwd(script),
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError as exc:
                    error_message = str(exc)
                    self.after(0, self._log, f"[run] failed to start {script}: {exc}")
                    return
                assert process.stdout is not None
                for line in process.stdout:
                    self.after(0, self._log, line.rstrip())
                exit_code = process.wait()
                self.after(0, self._log, f"[run] exit code {exit_code}: {script.name}")
                if exit_code != 0:
                    error_message = f"{script.name} failed with exit code {exit_code}."
                    return
            success = True
            self.after(0, self._log, "[run] all scripts completed")
        except Exception as exc:
            error_message = str(exc)
            self.after(0, self._log, f"[run] unexpected error: {exc}")
        finally:
            self.after(0, self._finish_run, completion_title, success, error_message)

    def _set_run_active(self, active: bool, mode: str = "") -> None:
        self.run_active = active
        self.run_mode = mode if active else ""
        disabled_state = "disabled" if active else "normal"
        if self.write_scripts_button is not None:
            self.write_scripts_button.configure(state=disabled_state)
        if self.run_msys2_button is not None:
            self.run_msys2_button.configure(
                state=disabled_state,
                text="Running MSYS2..." if active and mode == "msys2" else "Run with MSYS2",
            )
        if self.run_direct_button is not None:
            self.run_direct_button.configure(
                state=disabled_state,
                text="Running Direct..." if active and mode == "direct" else "Run Direct",
            )

    def _finish_run(self, title: str, success: bool, error_message: str) -> None:
        self._set_run_active(False)
        if success:
            messagebox.showinfo(title, "All scripts completed.")
        elif error_message:
            messagebox.showerror(title, error_message)

    def _read_recipe_from_form_silent(self) -> dict[str, Any] | None:
        try:
            if self.form_recipe_kind == "bookminer_cpp":
                return self._bookminer_cpp_recipe_from_form()
            return self._release_recipe_from_form()
        except ValueError:
            return None

    def _browse_dir(self, variable: tk.StringVar) -> None:
        path = filedialog.askdirectory(initialdir=_initial_dir(variable.get(), self.yobuild_root))
        if path:
            variable.set(path)

    def _browse_file(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(initialdir=_initial_dir(variable.get(), self.yobuild_root))
        if path:
            variable.set(path)

    def _browse_save(self, variable: tk.StringVar) -> None:
        path = filedialog.asksaveasfilename(initialdir=_initial_dir(variable.get(), self.yobuild_root))
        if path:
            variable.set(path)

    def _clear_plan(self) -> None:
        self.plan = []

    def _log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")


def _required(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _msys2_root_from_recipe(recipe: dict[str, Any]) -> str:
    root = str(recipe.get("msys2_root", "")).strip()
    if root:
        return root
    legacy_shell = str(recipe.get("msys2_bash", "")).strip()
    if legacy_shell:
        return _msys2_root_from_legacy_shell(legacy_shell)
    return DEFAULT_MSYS2_ROOT


def _msys2_root_from_legacy_shell(shell_path: str) -> str:
    normalized = shell_path.replace("/", "\\").rstrip("\\")
    lower = normalized.lower()
    for suffix in (
        "\\usr\\bin\\bash.exe",
        "\\mingw64.exe",
        "\\mingw32.exe",
        "\\clangarm64.exe",
    ):
        if lower.endswith(suffix):
            return normalized[: -len(suffix)] or DEFAULT_MSYS2_ROOT
    if lower.endswith((".exe", ".cmd", ".bat")):
        return str(PureWindowsPath(normalized).parent)
    return shell_path


def _msys2_shell_path(msys2_root: str, platform_name: str) -> str:
    root = msys2_root.strip()
    if not root:
        return ""
    if root.lower().endswith((".exe", ".cmd", ".bat")):
        return root
    executable = MSYS2_SHELL_BY_PLATFORM.get(platform_name, MSYS2_SHELL_BY_PLATFORM["win64"])
    separator = "\\" if "\\" in root or ":" in root else "/"
    return root.rstrip("\\/") + separator + executable


def _msys2_bash_path(msys2_root: str) -> str:
    root = msys2_root.strip()
    if not root:
        return ""
    if root.lower().endswith("\\usr\\bin\\bash.exe") or root.lower().endswith("/usr/bin/bash.exe"):
        return root
    separator = "\\" if "\\" in root or ":" in root else "/"
    return root.rstrip("\\/") + separator + "usr" + separator + "bin" + separator + "bash.exe"


def _msys2_msystem(platform_name: str) -> str:
    return MSYS2_SYSTEM_BY_PLATFORM.get(platform_name, MSYS2_SYSTEM_BY_PLATFORM["win64"])


def _msys2_msystem_prefix(platform_name: str) -> str:
    return "/mingw64"


def _bookminer_cpp_source_dir(yobuild_root: Path) -> Path | None:
    candidates = (
        yobuild_root.parent / "BookMinerCpp" / "source",
        yobuild_root.parent / "YaneuraOu-ScriptCollection" / "BookMinerCpp" / "source",
        yobuild_root.parent.parent / "BookMinerCpp" / "source",
        yobuild_root.parent.parent / "YaneuraOu-ScriptCollection" / "BookMinerCpp" / "source",
    )
    for candidate in candidates:
        if (candidate / "Makefile").is_file():
            return candidate
    return None


def _normalize_variants_for_form(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    custom_variants: list[dict[str, Any]] = []
    for variant in variants:
        name = str(variant.get("name", ""))
        if name in STANDARD_VARIANT_NAMES and name not in by_name:
            by_name[name] = dict(variant)
        else:
            custom_variants.append(dict(variant))

    normalized: list[dict[str, Any]] = []
    for standard_variant in STANDARD_VARIANTS:
        name = str(standard_variant["name"])
        variant = by_name.get(name)
        if variant is None:
            variant = dict(standard_variant)
            variant["enabled"] = not variants and name == "Git"
        normalized.append(variant)
    normalized.extend(custom_variants)
    return normalized


def _normalize_editions_for_form(editions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    saved_by_edition: dict[str, dict[str, Any]] = {}
    for edition in editions:
        edition_name = str(edition.get("edition", ""))
        if edition_name and edition_name not in saved_by_edition:
            saved_by_edition[edition_name] = edition

    has_saved_editions = bool(saved_by_edition)
    fixed_editions: list[dict[str, Any]] = []
    for edition_name, artifact_prefix in RELEASE_EDITIONS:
        saved = saved_by_edition.get(edition_name)
        fixed_editions.append(
            {
                "edition": edition_name,
                "artifact_prefix": artifact_prefix,
                "enabled": bool(saved.get("enabled", True))
                if saved is not None
                else (not has_saved_editions and edition_name not in DEFAULT_DISABLED_RELEASE_EDITIONS),
            }
        )
    return fixed_editions


def _split_words(value: str) -> list[str]:
    if not value.strip():
        return []
    return shlex.split(value, posix=False)


def _join_flags(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, str):
        return values
    return " ".join(str(value) for value in values)


def _initial_dir(value: str, fallback: Path) -> str:
    if value:
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
    return str(fallback)


def _normalized_run_root_text(value: Any, yobuild_root: Path) -> str:
    text = str(value).strip()
    if not text:
        text = str(yobuild_root / "runs")
    return str(resolve_run_root(text, yobuild_root))


def _platform_for_single_recipe(source_dir: str, target_cpu: str) -> str:
    if target_cpu in CPU_OPTIONS["mac"] or source_dir.startswith("/Users/"):
        return "mac"
    if target_cpu in CPU_OPTIONS["winarm"]:
        return "winarm"
    return "win64"


def _path_stem(value: str) -> str:
    if not value:
        return ""
    name = value.replace("\\", "/").rstrip("/").split("/")[-1]
    if "." not in name:
        return name
    return ".".join(name.split(".")[:-1])


def run_gui(yobuild_root: Path) -> None:
    app = BuildGui(yobuild_root)
    app.mainloop()
