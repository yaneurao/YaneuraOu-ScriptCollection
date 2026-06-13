#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from datetime import date, datetime
import pickle
import queue
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


BASE_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = BASE_DIR / "scripts"
SETTINGS_PATH = BASE_DIR / "kif-manager-settings.pickle"
SETTINGS_VERSION = 1
EXTRACT_DEFAULT_OUTPUT_FILE = "think_sfens.txt"
BOOKMINER_EXTRACT_OUTPUT_FILE = str((BASE_DIR.parent / "BookMiner" / "book" / "think_sfens.txt").resolve())
WCSC_DEFAULT_OUTPUT_DIR = "downloaded-kif/wcsc"
WCSC_OLD_DEFAULT_OUTPUT_DIR = "downloaded-kif"
FLOODGATE_DEFAULT_OUTPUT_DIR = "downloaded-kif/floodgate"
LOG_MAX_LINES = 1000
LOG_TRIM_THRESHOLD = 1200
sys.path.insert(0, str(SCRIPTS_DIR))

from floodgate_kif_downloader_core import (  # noqa: E402
    FloodgateDownloadError,
    FloodgateDownloadJob,
    FloodgateDownloadStats,
    download_floodgate_kif,
    validate_year,
)
from denryu_kif_downloader_core import (  # noqa: E402
    DENRYU_DEFAULT_OUTPUT_DIR,
    DenryuDownloadJob,
    DenryuDownloadStats,
    DenryuTournamentOption,
    download_denryu_kif,
    denryu_tournament_key,
    fallback_denryu_tournament_options,
    fetch_denryu_tournament_options,
)
from kif_extractor_common import Stats, parse_date_value, run_extractor  # noqa: E402
from wcsc_kif_downloader_core import (  # noqa: E402
    DownloadError,
    WcscDownloadJob,
    WcscDownloadStats,
    download_wcsc_kif,
    normalize_wcsc_name,
)
from shogidb2_kif_downloader import (  # noqa: E402
    SHOGIDB2_DEFAULT_OUTPUT_DIR,
    ShogiDb2DownloadJob,
    ShogiDb2DownloadStats,
    ShogiDb2TournamentOption,
    download_shogidb2_kif,
    fetch_shogidb2_tournament_options,
    normalize_tournament_url,
)


def parse_download_interval(value: str) -> float:
    raw = value.strip()
    bypass_limit = raw.startswith("!")
    if bypass_limit:
        raw = raw[1:].strip()

    try:
        interval = float(raw)
    except ValueError as exc:
        raise ValueError("アクセス間隔(秒)は数値で指定してください。") from exc

    if interval < 0:
        raise ValueError("アクセス間隔(秒)は 0 以上を指定してください。")
    if interval < 2 and not bypass_limit:
        raise ValueError("アクセス間隔(秒)は 2 以上を指定してください。")
    return interval


@dataclass(frozen=True)
class ExtractorKind:
    key: str
    title: str
    description: str
    folder_help: str
    has_rating: bool = False
    year_source: str | None = None
    default_input_dir: str = ""
    default_min_rating: str = ""


@dataclass(frozen=True)
class DownloadKind:
    key: str
    title: str
    description: str
    implemented: bool = False


@dataclass(frozen=True)
class ExtractJob:
    kind: ExtractorKind
    input_dir: Path
    output_path: Path
    both_player_list: Path | None
    either_player_list: Path | None
    min_rating: float | None
    losing_player_min_rating: float | None
    start_year: int | None
    end_year: int | None
    start_date: date | None
    end_date: date | None
    wcsc_finalists_only: bool
    reversal_threshold: int | None
    exclude_handicap: bool
    require_rating: bool
    log_target_files: bool
    verbose: bool


EXTRACTORS = (
    ExtractorKind(
        "floodgate",
        "floodgate",
        "floodgateの棋譜ファイルから条件に該当する棋譜を抽出します。",
        "floodgateの棋譜ファイルが配置されているフォルダを指定してください。",
        True,
        None,
        "downloaded-kif/floodgate",
        "4000",
    ),
    ExtractorKind(
        "wcsc",
        "WCSC",
        "WCSCの棋譜ファイルから条件に該当する棋譜を抽出します。",
        "WCSCの棋譜ファイルが配置されているフォルダを指定してください。",
        False,
        "wcsc",
        "downloaded-kif/wcsc",
    ),
    ExtractorKind(
        "denryu",
        "電竜戦",
        "電竜戦の棋譜ファイルから条件に該当する棋譜を抽出します。",
        "電竜戦の棋譜ファイルが配置されているフォルダを指定してください。",
    ),
    ExtractorKind(
        "other",
        "その他",
        "任意の棋譜ファイルから条件に該当する棋譜を抽出します。",
        "kif/csa/csv/kifu形式の棋譜ファイルが配置されているフォルダを指定してください。",
    ),
)

DOWNLOADERS = (
    DownloadKind(
        "floodgate",
        "floodgate",
        "floodgateの年別棋譜アーカイブをダウンロードします。",
        True,
    ),
    DownloadKind(
        "wcsc",
        "WCSC",
        "WCSCの棋譜リストから棋譜ファイルをダウンロードする設定を行います。",
        True,
    ),
    DownloadKind(
        "denryu",
        "電竜戦",
        "電竜戦の一括棋譜アーカイブ、またはlive中継ページから棋譜をダウンロードします。",
        True,
    ),
)

SHOGIDB2_DOWNLOADER = DownloadKind(
    "shogidb2",
    "shogidb2",
    "shogidb2の棋戦ページからKIF形式の棋譜をダウンロードします。",
    True,
)


class QueueWriter:
    def __init__(self, log_queue: queue.Queue[tuple[str, str]], prefix: str) -> None:
        self.log_queue = log_queue
        self.prefix = prefix

    def write(self, text: str) -> int:
        if text:
            for line in text.splitlines(True):
                self.log_queue.put(("log", f"[{self.prefix}] {line}"))
        return len(text)

    def flush(self) -> None:
        pass


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.window: tk.Toplevel | None = None
        self.pinned = False

        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide_if_not_pinned)
        widget.bind("<Button-1>", self._toggle)

    def _show(self, _event: tk.Event | None = None) -> None:
        if self.window is not None:
            return

        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
            wraplength=420,
        )
        label.pack()
        label.bind("<Button-1>", self._hide)

    def _hide(self, _event: tk.Event | None = None) -> None:
        self.pinned = False
        if self.window is not None:
            self.window.destroy()
            self.window = None

    def _hide_if_not_pinned(self, event: tk.Event | None = None) -> None:
        if not self.pinned:
            self._hide(event)

    def _toggle(self, event: tk.Event | None = None) -> None:
        if self.window is not None and self.pinned:
            self._hide(event)
            return
        self.pinned = True
        self._show(event)


class ExtractorPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: ExtractorKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        self.input_dir = tk.StringVar(value=kind.default_input_dir)
        self.output_path = tk.StringVar(value=EXTRACT_DEFAULT_OUTPUT_FILE)
        self.both_player_list = tk.StringVar()
        self.either_player_list = tk.StringVar()
        self.min_rating = tk.StringVar(value=kind.default_min_rating)
        self.losing_player_min_rating = tk.StringVar(value="4000" if kind.key == "floodgate" else "")
        self.start_year = tk.StringVar()
        self.end_year = tk.StringVar()
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()
        self.wcsc_finalists_only = tk.BooleanVar(value=False)
        self.reversal_enabled = tk.BooleanVar(value=False)
        self.reversal_threshold = tk.StringVar(value="400")
        self.exclude_handicap = tk.BooleanVar(value=False)

        self.columnconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        description = ttk.Label(self, text=self.kind.description, wraplength=680, justify="left")
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        row = 1
        row = self._path_row(
            row,
            "入力フォルダ",
            self.input_dir,
            self._browse_input_dir,
            self.kind.folder_help,
        )
        row = self._path_row(
            row,
            "出力ファイル",
            self.output_path,
            self._browse_output_path,
            "抽出した棋譜をUSIのpositionコマンド形式で保存するファイルを指定してください。",
        )
        if self.kind.year_source is not None:
            row = self._text_row(
                row,
                "開始年",
                self.start_year,
                "抽出対象にする最初の年を指定してください。\n"
                "空欄なら下限なしです。",
                width=10,
            )
            row = self._text_row(
                row,
                "終了年",
                self.end_year,
                "抽出対象にする最後の年を指定してください。\n"
                "空欄なら上限なしです。\n"
                "WCSO1はWCSC30扱い、つまり2020年として扱います。",
                width=10,
            )
        if self.kind.key == "floodgate":
            row = self._text_row(
                row,
                "開始日",
                self.start_date,
                "floodgate棋譜の対局日で絞り込みます。\n"
                "YYYY-MM-DD または YYYY/MM/DD で指定してください。\n"
                "月日を1桁で書いても構いません。\n"
                "年だけの場合は、その年の1月1日として扱います。\n"
                "空欄なら下限なしです。",
                width=14,
            )
            row = self._text_row(
                row,
                "終了日",
                self.end_date,
                "floodgate棋譜の対局日で絞り込みます。\n"
                "YYYY-MM-DD または YYYY/MM/DD で指定してください。\n"
                "月日を1桁で書いても構いません。\n"
                "年だけの場合は、その年の12月31日として扱います。\n"
                "空欄なら上限なしです。",
                width=14,
            )
        row = self._path_row(
            row,
            "both-player-list",
            self.both_player_list,
            self._browse_both_player_list,
            "先手と後手の両方がこのリストに一致する棋譜を抽出対象にします。\n"
            "either-player-listと両方指定した場合は、どちらかの条件を満たせば抽出します。",
        )
        row = self._path_row(
            row,
            "either-player-list",
            self.either_player_list,
            self._browse_either_player_list,
            "先手または後手の少なくとも片方がこのリストに一致する棋譜を抽出対象にします。\n"
            "both-player-listと両方指定した場合は、どちらかの条件を満たせば抽出します。",
        )

        if self.kind.key in {"wcsc", "denryu"}:
            if self.kind.key == "wcsc":
                finalist_help = (
                    "同じWCSC大会内の決勝棋譜からプレイヤー名を集め、\n"
                    "そのどちらかが登場する棋譜だけを抽出します。"
                )
            else:
                finalist_help = (
                    "同じ電竜戦本戦内の決勝リーグ/A級棋譜からプレイヤー名を集め、\n"
                    "そのどちらかが登場する予選・決勝の棋譜だけを抽出します。"
                )
            self._label_with_help(
                row,
                "決勝に出場したソフトの棋譜のみ抽出",
                finalist_help,
            )
            ttk.Checkbutton(self, variable=self.wcsc_finalists_only).grid(row=row, column=1, sticky="w", pady=6)
            row += 1

        if self.kind.has_rating:
            row = self._text_row(
                row,
                "min-rating",
                self.min_rating,
                "指定期間内に一度でもこのrating以上になったプレイヤー同士の棋譜だけを抽出します。\n"
                "空欄にするとratingでは絞り込みません。\n"
                "ratingが見つからないプレイヤーは、この条件の対象になりません。",
                width=14,
            )
        if self.kind.key == "floodgate":
            row = self._losing_player_rating_row(row)

        row = self._reversal_row(row)
        if self.kind.key == "other":
            row = self._exclude_handicap_row(row)

    def _path_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)

        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="参照", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=3, sticky="e", padx=(6, 0), pady=6
        )
        return row + 1

    def _label_with_help(self, row: int, label: str, help_text: str) -> None:
        label_frame = ttk.Frame(self)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(label_frame, text=label).pack(side="left")
        help_label = ttk.Label(label_frame, text=" ❓", cursor="question_arrow")
        help_label.pack(side="left")
        Tooltip(help_label, help_text)

    def _text_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
        *,
        width: int,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)
        return row + 1

    def _losing_player_rating_row(self, row: int) -> int:
        self._label_with_help(
            row,
            "負けた棋譜",
            "指定期間内に一度でもこのrating以上になったプレイヤーが負けた棋譜を追加します。\n"
            "相手のratingは問いません。\n"
            "空欄にするとこの追加条件は無効です。",
        )
        frame = ttk.Frame(self)
        frame.grid(row=row, column=1, columnspan=3, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.losing_player_min_rating, width=8).pack(side="left")
        ttk.Label(frame, text="以上のプレイヤーが負けた棋譜も追加する").pack(side="left", padx=(4, 0))
        return row + 1

    def _reversal_row(self, row: int) -> int:
        self._label_with_help(
            row,
            "逆転棋譜",
            "片方のプレイヤー自身が出力した評価値が一度この絶対値以上になり、\n"
            "その後、同じプレイヤーの出力評価値が0をまたいだ棋譜だけを抽出します。\n"
            "評価値コメントが見つからない棋譜は、この条件を有効にした場合は除外されます。",
        )
        frame = ttk.Frame(self)
        frame.grid(row=row, column=1, columnspan=3, sticky="w", pady=6)
        ttk.Checkbutton(frame, variable=self.reversal_enabled).pack(side="left")
        ttk.Label(frame, text="評価値").pack(side="left", padx=(6, 4))
        ttk.Entry(frame, textvariable=self.reversal_threshold, width=8).pack(side="left")
        ttk.Label(frame, text="から逆転した棋譜").pack(side="left", padx=(4, 0))
        return row + 1

    def _exclude_handicap_row(self, row: int) -> int:
        self._label_with_help(
            row,
            "駒落ちの棋譜を除く",
            "初期局面が平手ではなく、かつ盤上と持駒の合計が40枚未満の棋譜を除外します。\n"
            "この条件に該当しない非平手局面は、sfen形式のposition文字列として出力します。",
        )
        ttk.Checkbutton(self, variable=self.exclude_handicap).grid(row=row, column=1, sticky="w", pady=6)
        return row + 1

    def _browse_input_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._initial_dir(self.input_dir.get()))
        if selected:
            self.input_dir.set(selected)

    def _browse_output_path(self) -> None:
        selected = filedialog.asksaveasfilename(
            initialdir=self._initial_dir(self.output_path.get()),
            defaultextension=".txt",
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if selected:
            self.output_path.set(selected)

    def _browse_both_player_list(self) -> None:
        selected = filedialog.askopenfilename(
            initialdir=self._initial_dir(self.both_player_list.get()),
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if selected:
            self.both_player_list.set(selected)

    def _browse_either_player_list(self) -> None:
        selected = filedialog.askopenfilename(
            initialdir=self._initial_dir(self.either_player_list.get()),
            filetypes=(("Text files", "*.txt"), ("All files", "*.*")),
        )
        if selected:
            self.either_player_list.set(selected)

    def _initial_dir(self, value: str) -> str:
        if not value:
            return str(BASE_DIR)
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(BASE_DIR)

    def has_required_paths(self) -> bool:
        return bool(self.input_dir.get().strip() and self.output_path.get().strip())

    def build_job(self, verbose: bool, log_target_files: bool) -> ExtractJob:
        input_value = self.input_dir.get().strip()
        output_value = self.output_path.get().strip()

        if not input_value:
            raise ValueError("入力フォルダを指定してください。")
        if not output_value:
            raise ValueError("出力ファイルを指定してください。")

        input_dir = Path(input_value).expanduser()
        output_path = Path(output_value).expanduser()

        if not input_dir.is_dir():
            raise ValueError(f"入力フォルダが見つかりません: {input_dir}")
        if output_path.exists() and output_path.is_dir():
            raise ValueError(f"出力先がフォルダです: {output_path}")

        both_player_list = self._optional_file(self.both_player_list.get().strip(), "both-player-list")
        either_player_list = self._optional_file(self.either_player_list.get().strip(), "either-player-list")
        min_rating = self._parse_min_rating()
        losing_player_min_rating = self._parse_losing_player_min_rating()
        start_year = self._parse_year(self.start_year.get().strip(), "開始年")
        end_year = self._parse_year(self.end_year.get().strip(), "終了年")
        start_date = self._parse_date(self.start_date.get().strip(), "開始日", year_only_month_day=(1, 1))
        end_date = self._parse_date(self.end_date.get().strip(), "終了日", year_only_month_day=(12, 31))
        reversal_threshold = self._parse_reversal_threshold()
        if start_year is not None and end_year is not None and start_year > end_year:
            raise ValueError("開始年は終了年以下を指定してください。")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("開始日は終了日以下を指定してください。")

        return ExtractJob(
            self.kind,
            input_dir,
            output_path,
            both_player_list,
            either_player_list,
            min_rating,
            losing_player_min_rating,
            start_year,
            end_year,
            start_date,
            end_date,
            self.wcsc_finalists_only.get(),
            reversal_threshold,
            self.kind.key == "other" and self.exclude_handicap.get(),
            self.kind.has_rating and min_rating is not None,
            log_target_files,
            verbose,
        )

    def _optional_file(self, value: str, label: str) -> Path | None:
        if not value:
            return None
        path = Path(value).expanduser()
        if not path.is_file():
            raise ValueError(f"{label} が見つかりません: {path}")
        return path

    def _parse_min_rating(self) -> float | None:
        if not self.kind.has_rating:
            return None
        value = self.min_rating.get().strip()
        if not value:
            return None
        try:
            rating = float(value)
        except ValueError as exc:
            raise ValueError(f"min-rating が数値ではありません: {value}") from exc
        if rating < 0:
            raise ValueError("min-rating は 0 以上を指定してください。")
        return rating

    def _parse_losing_player_min_rating(self) -> float | None:
        if self.kind.key != "floodgate":
            return None
        value = self.losing_player_min_rating.get().strip()
        if not value:
            return None
        try:
            rating = float(value)
        except ValueError as exc:
            raise ValueError(f"負けた棋譜のratingが数値ではありません: {value}") from exc
        if rating < 0:
            raise ValueError("負けた棋譜のratingは 0 以上を指定してください。")
        return rating

    def _parse_reversal_threshold(self) -> int | None:
        if not self.reversal_enabled.get():
            return None
        value = self.reversal_threshold.get().strip()
        if not value:
            raise ValueError("逆転棋譜の評価値を指定してください。")
        try:
            threshold = int(value)
        except ValueError as exc:
            raise ValueError(f"逆転棋譜の評価値は整数で指定してください: {value}") from exc
        if threshold <= 0:
            raise ValueError("逆転棋譜の評価値は 1 以上を指定してください。")
        return threshold

    def _parse_year(self, value: str, label: str) -> int | None:
        if self.kind.year_source is None or not value:
            return None
        try:
            year = int(value)
        except ValueError as exc:
            raise ValueError(f"{label} は整数で指定してください: {value}") from exc
        if year < 1:
            raise ValueError(f"{label} は1以上を指定してください。")
        return year

    def _parse_date(
        self,
        value: str,
        label: str,
        *,
        year_only_month_day: tuple[int, int],
    ) -> date | None:
        if self.kind.key != "floodgate" or not value:
            return None
        return parse_date_value(value, label, year_only_month_day=year_only_month_day)

    def settings(self) -> dict[str, str]:
        return {
            "input_dir": self.input_dir.get(),
            "output_path": self.output_path.get(),
            "both_player_list": self.both_player_list.get(),
            "either_player_list": self.either_player_list.get(),
            "min_rating": self.min_rating.get(),
            "losing_player_min_rating": self.losing_player_min_rating.get(),
            "start_year": self.start_year.get(),
            "end_year": self.end_year.get(),
            "start_date": self.start_date.get(),
            "end_date": self.end_date.get(),
            "wcsc_finalists_only": self.wcsc_finalists_only.get(),
            "reversal_enabled": self.reversal_enabled.get(),
            "reversal_threshold": self.reversal_threshold.get(),
            "exclude_handicap": self.exclude_handicap.get(),
        }

    def apply_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        self.input_dir.set(str(settings.get("input_dir", self.kind.default_input_dir) or self.kind.default_input_dir))
        self.output_path.set(str(settings.get("output_path", EXTRACT_DEFAULT_OUTPUT_FILE) or EXTRACT_DEFAULT_OUTPUT_FILE))
        self.both_player_list.set(str(settings.get("both_player_list", settings.get("filtered_player_list", ""))))
        self.either_player_list.set(str(settings.get("either_player_list", "")))
        self.min_rating.set(str(settings.get("min_rating", self.kind.default_min_rating) or self.kind.default_min_rating))
        default_losing_player_min_rating = "4000" if self.kind.key == "floodgate" else ""
        self.losing_player_min_rating.set(
            str(settings.get("losing_player_min_rating", default_losing_player_min_rating))
        )
        self.start_year.set(str(settings.get("start_year", "")))
        self.end_year.set(str(settings.get("end_year", "")))
        self.start_date.set(str(settings.get("start_date", "")))
        self.end_date.set(str(settings.get("end_date", "")))
        self.wcsc_finalists_only.set(bool(settings.get("wcsc_finalists_only", False)))
        self.reversal_enabled.set(bool(settings.get("reversal_enabled", False)))
        self.reversal_threshold.set(str(settings.get("reversal_threshold", "400") or "400"))
        self.exclude_handicap.set(bool(settings.get("exclude_handicap", False)))


class DownloadPlaceholderPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: DownloadKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        ttk.Label(self, text=kind.description, wraplength=680, justify="left").grid(
            row=0, column=0, sticky="w"
        )

    def settings(self) -> dict[str, str]:
        return {}

    def apply_settings(self, settings: object) -> None:
        _ = settings


class FloodgateDownloadPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: DownloadKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        self.year = tk.StringVar(value=str(datetime.now().year))
        self.output_dir = tk.StringVar(value=FLOODGATE_DEFAULT_OUTPUT_DIR)

        self.columnconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        description = ttk.Label(self, text=self.kind.description, wraplength=680, justify="left")
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        row = 1
        self._text_row(
            row,
            "対局年",
            self.year,
            "2008以降の年を指定してください。\n"
            "今年のものは、前日までの棋譜がダウンロードされます。",
            width=10,
        )
        row += 1
        self._path_row(
            row,
            "出力フォルダ",
            self.output_dir,
            self._browse_output_dir,
            "ダウンロードした wdoorYYYY.7z を保存するフォルダを指定してください。\n"
            "既存ファイルとサーバー上のサイズが同じならダウンロードを省略します。\n"
            "デフォルトでは downloaded-kif/floodgate に保存します。",
        )

    def _label_with_help(self, row: int, label: str, help_text: str) -> None:
        label_frame = ttk.Frame(self)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(label_frame, text=label).pack(side="left")
        help_label = ttk.Label(label_frame, text=" ❓", cursor="question_arrow")
        help_label.pack(side="left")
        Tooltip(help_label, help_text)

    def _text_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
        *,
        width: int,
    ) -> None:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)

    def _path_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="参照", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=3, sticky="e", padx=(6, 0), pady=6
        )
        return row + 1

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._initial_dir(self.output_dir.get()))
        if selected:
            self.output_dir.set(selected)

    def _initial_dir(self, value: str) -> str:
        if not value:
            return str(BASE_DIR)
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(BASE_DIR)

    def build_job(self) -> FloodgateDownloadJob:
        value = self.year.get().strip()
        try:
            year = int(value)
        except ValueError as exc:
            raise ValueError("対局年は2008以降の整数で指定してください。") from exc
        try:
            year = validate_year(year)
        except FloodgateDownloadError as exc:
            raise ValueError(str(exc)) from exc

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            raise ValueError("出力フォルダを指定してください。")
        output_path = Path(output_dir).expanduser()
        if output_path.exists() and not output_path.is_dir():
            raise ValueError(f"出力フォルダがファイルです: {output_path}")

        return FloodgateDownloadJob(year, output_path)

    def settings(self) -> dict[str, str]:
        return {
            "year": self.year.get(),
            "output_dir": self.output_dir.get(),
        }

    def apply_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        self.year.set(str(settings.get("year", datetime.now().year) or datetime.now().year))
        self.output_dir.set(str(settings.get("output_dir", FLOODGATE_DEFAULT_OUTPUT_DIR) or FLOODGATE_DEFAULT_OUTPUT_DIR))


class WcscDownloadPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: DownloadKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        self.tournament = tk.StringVar(value="WCSC36")
        self.output_dir = tk.StringVar(value=WCSC_DEFAULT_OUTPUT_DIR)
        self.interval = tk.StringVar(value="10")
        self.overwrite = tk.BooleanVar(value=False)
        self.use_live_page = tk.BooleanVar(value=False)

        self.columnconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        description = ttk.Label(self, text=self.kind.description, wraplength=680, justify="left")
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        row = 1
        self._text_row(
            row,
            "大会名",
            self.tournament,
            "WCSC36のように大会名を指定してください。\n"
            "WCSC30は中止になりましたが、オンライン大会として開催されました。\n"
            "WCSO1と指定すれば、その棋譜をダウンロードできます。\n"
            "通常は公式棋譜アーカイブページからダウンロードします。\n"
            "live中継ページから取得したい場合だけチェックを入れてください。\n"
            "WCSC16以前は公式LZHアーカイブをダウンロードして展開します。\n"
            "lhafileで展開できない古い形式は7-Zip等の外部コマンドを使用します。",
            width=16,
        )
        row += 1
        row = self._path_row(
            row,
            "出力フォルダ",
            self.output_dir,
            self._browse_output_dir,
            "棋譜ファイルを保存する親フォルダを指定してください。\n"
            "デフォルトでは downloaded-kif/wcsc を指定します。\n"
            "実際にはこの配下に wcsc36 や wcso1 のような大会名フォルダを作成します。\n"
            "ZIP/7zは前回展開時と同じサイズなら再ダウンロードを省略します。\n"
            "WCSC16以前はこの大会名フォルダ内にLZHの中身を展開します。",
        )
        ttk.Checkbutton(
            self,
            text="live中継ページからダウンロード",
            variable=self.use_live_page,
        ).grid(row=row, column=1, sticky="w", pady=6)
        row += 1
        self._text_row(
            row,
            "アクセス間隔(秒)",
            self.interval,
            "連続アクセスでサーバーに負荷をかけないため、\n"
            "1ファイルにアクセスするごとの待ち時間を秒単位で指定します。\n"
            "2 以上を指定してください。",
            width=10,
        )
        row += 1

        ttk.Checkbutton(self, text="既存ファイルを上書き", variable=self.overwrite).grid(
            row=row, column=1, sticky="w", pady=6
        )

    def _label_with_help(self, row: int, label: str, help_text: str) -> None:
        label_frame = ttk.Frame(self)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(label_frame, text=label).pack(side="left")
        help_label = ttk.Label(label_frame, text=" ❓", cursor="question_arrow")
        help_label.pack(side="left")
        Tooltip(help_label, help_text)

    def _text_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
        *,
        width: int,
    ) -> None:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)

    def _path_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="参照", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=3, sticky="e", padx=(6, 0), pady=6
        )
        return row + 1

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._initial_dir(self.output_dir.get()))
        if selected:
            self.output_dir.set(selected)

    def _initial_dir(self, value: str) -> str:
        if not value:
            return str(BASE_DIR)
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(BASE_DIR)

    def build_job(self) -> WcscDownloadJob:
        tournament = self.tournament.get().strip()
        try:
            normalized_tournament, _base_url = normalize_wcsc_name(tournament)
        except DownloadError as exc:
            raise ValueError(str(exc)) from exc

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            raise ValueError("出力フォルダを指定してください。")
        output_root = Path(output_dir).expanduser()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"出力フォルダがファイルです: {output_root}")

        interval = parse_download_interval(self.interval.get())

        return WcscDownloadJob(
            normalized_tournament,
            output_root,
            interval,
            overwrite=self.overwrite.get(),
            use_live_page=self.use_live_page.get(),
        )

    def settings(self) -> dict[str, object]:
        return {
            "tournament": self.tournament.get(),
            "output_dir": self.output_dir.get(),
            "interval": self.interval.get(),
            "overwrite": self.overwrite.get(),
            "use_live_page": self.use_live_page.get(),
        }

    def apply_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        self.tournament.set(str(settings.get("tournament", "WCSC36") or "WCSC36"))
        output_dir = str(settings.get("output_dir", WCSC_DEFAULT_OUTPUT_DIR) or WCSC_DEFAULT_OUTPUT_DIR)
        if output_dir == WCSC_OLD_DEFAULT_OUTPUT_DIR:
            output_dir = WCSC_DEFAULT_OUTPUT_DIR
        self.output_dir.set(output_dir)
        self.interval.set(str(settings.get("interval", "10") or "10"))
        self.overwrite.set(bool(settings.get("overwrite", False)))
        self.use_live_page.set(bool(settings.get("use_live_page", False)))


class DenryuDownloadPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: DownloadKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        self.tournament_choice = tk.StringVar()
        self.tournament_url = tk.StringVar()
        self.output_dir = tk.StringVar(value=DENRYU_DEFAULT_OUTPUT_DIR)
        self.interval = tk.StringVar(value="10")
        self.overwrite = tk.BooleanVar(value=False)
        self.use_live_page = tk.BooleanVar(value=False)
        self.archive_url = ""
        self.tournament_options: list[DenryuTournamentOption] = []
        self.option_by_display: dict[str, DenryuTournamentOption] = {}

        self.columnconfigure(1, weight=1)
        self._build()
        self._set_tournament_options(fallback_denryu_tournament_options())

    def _build(self) -> None:
        description = ttk.Label(self, text=self.kind.description, wraplength=680, justify="left")
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        row = 1
        self._label_with_help(
            row,
            "大会",
            "公式リンク集に掲載されている既存大会を選択できます。\n"
            "一覧更新を押すと、電竜戦の公式リンク集から最新の大会一覧を取得します。",
        )
        self.tournament_combo = ttk.Combobox(self, textvariable=self.tournament_choice, state="readonly")
        self.tournament_combo.grid(row=row, column=1, sticky="ew", pady=6)
        self.tournament_combo.bind("<<ComboboxSelected>>", self._on_tournament_selected)
        ttk.Button(self, text="一覧更新", command=self.refresh_tournaments).grid(
            row=row, column=2, sticky="e", padx=(8, 0), pady=6
        )
        row += 1

        row = self._url_row(
            row,
            "大会URL",
            self.tournament_url,
            "大会のliveページURL、公式アーカイブZIPのURL、\n"
            "公式アーカイブ7zのURL、\n"
            "または dr6_production のような大会キーを指定できます。\n"
            "コンボボックスで大会を選ぶと自動で設定されます。",
        )
        row = self._path_row(
            row,
            "出力フォルダ",
            self.output_dir,
            self._browse_output_dir,
            "棋譜ファイルを保存する親フォルダを指定してください。\n"
            "デフォルトでは downloaded-kif/denryu を指定します。\n"
            "実際にはこの配下に dr6_production のような大会キーのフォルダを作成します。",
        )
        self._label_with_help(
            row,
            "live中継ページからダウンロード",
            "チェックなしなら、大会ページや公式リンク集から一括ZIP/7zを探して展開します。\n"
            "チェックありなら、live中継ページの kifulist.txt から個別CSAを取得します。\n"
            "ZIP/7zは前回展開時と同じサイズなら再ダウンロードを省略します。\n"
            "大会終了前で一括ZIP/7zが未公開のときはこちらを使います。",
        )
        ttk.Checkbutton(self, variable=self.use_live_page).grid(row=row, column=1, sticky="w", pady=6)
        row += 1
        self._text_row(
            row,
            "アクセス間隔(秒)",
            self.interval,
            "連続アクセスでサーバーに負荷をかけないため、\n"
            "1ファイルにアクセスするごとの待ち時間を秒単位で指定します。\n"
            "2 以上を指定してください。",
            width=10,
        )
        row += 1
        self._label_with_help(
            row,
            "既存ファイルを上書き",
            "既に同名の棋譜ファイルがある場合に上書きします。\n"
            "チェックなしでも、既存ファイルとアーカイブ内のサイズが違う場合は置き換えます。",
        )
        ttk.Checkbutton(self, variable=self.overwrite).grid(row=row, column=1, sticky="w", pady=6)

    def _label_with_help(self, row: int, label: str, help_text: str) -> None:
        label_frame = ttk.Frame(self)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(label_frame, text=label).pack(side="left")
        help_label = ttk.Label(label_frame, text=" ❓", cursor="question_arrow")
        help_label.pack(side="left")
        Tooltip(help_label, help_text)

    def _text_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
        *,
        width: int,
    ) -> None:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)

    def _url_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=2, sticky="e", padx=(8, 0), pady=6
        )
        return row + 1

    def _path_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="参照", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=3, sticky="e", padx=(6, 0), pady=6
        )
        return row + 1

    def refresh_tournaments(self) -> None:
        current_key = self._selected_or_typed_key()
        try:
            options = fetch_denryu_tournament_options()
        except Exception as exc:
            messagebox.showerror(
                "一覧更新失敗",
                f"公式リンク集から大会一覧を取得できませんでした。\n{exc}",
                parent=self,
            )
            return
        self._set_tournament_options(options, preferred_key=current_key)

    def _set_tournament_options(
        self,
        options: list[DenryuTournamentOption],
        preferred_key: str | None = None,
    ) -> None:
        if not options:
            return
        self.tournament_options = options
        self.option_by_display = {option.display_name: option for option in options}
        values = list(self.option_by_display)
        self.tournament_combo.configure(values=values)

        key = preferred_key or self._selected_or_typed_key()
        selected = next((option for option in options if option.key == key), None)
        if selected is None:
            selected = options[0]
        self.tournament_choice.set(selected.display_name)
        self._apply_option(selected)

    def _selected_or_typed_key(self) -> str | None:
        option = self.option_by_display.get(self.tournament_choice.get())
        if option is not None:
            return option.key
        url = self.tournament_url.get().strip()
        if not url:
            return None
        try:
            return denryu_tournament_key(url)
        except Exception:
            return None

    def _on_tournament_selected(self, _event: tk.Event | None = None) -> None:
        option = self.option_by_display.get(self.tournament_choice.get())
        if option is not None:
            self._apply_option(option)

    def _apply_option(self, option: DenryuTournamentOption) -> None:
        self.archive_url = option.archive_url
        if option.archive_url and self.use_live_page.get():
            self.use_live_page.set(False)
        if option.preferred_url:
            self.tournament_url.set(option.preferred_url)

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._initial_dir(self.output_dir.get()))
        if selected:
            self.output_dir.set(selected)

    def _initial_dir(self, value: str) -> str:
        if not value:
            return str(BASE_DIR)
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(BASE_DIR)

    def build_job(self) -> DenryuDownloadJob:
        source_url = self.tournament_url.get().strip()
        if not source_url:
            raise ValueError("大会URLを指定してください。")

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            raise ValueError("出力フォルダを指定してください。")
        output_root = Path(output_dir).expanduser()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"出力フォルダがファイルです: {output_root}")

        interval = parse_download_interval(self.interval.get())

        archive_url = ""
        option = self._option_for_source(source_url)
        if option is not None:
            try:
                if denryu_tournament_key(source_url) == option.key:
                    archive_url = option.archive_url
            except Exception:
                archive_url = ""
        use_live_page = self.use_live_page.get()

        return DenryuDownloadJob(
            source_url,
            output_root,
            interval,
            overwrite=self.overwrite.get(),
            use_live_page=use_live_page,
            archive_url=archive_url,
        )

    def settings(self) -> dict[str, object]:
        return {
            "tournament_choice": self.tournament_choice.get(),
            "tournament_url": self.tournament_url.get(),
            "output_dir": self.output_dir.get(),
            "interval": self.interval.get(),
            "overwrite": self.overwrite.get(),
            "use_live_page": self.use_live_page.get(),
        }

    def apply_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        choice = str(settings.get("tournament_choice", ""))
        url = str(settings.get("tournament_url", ""))
        if choice in self.option_by_display:
            self.tournament_choice.set(choice)
            self._apply_option(self.option_by_display[choice])
        elif url:
            self.archive_url = ""
            self.tournament_url.set(url)
        self.output_dir.set(str(settings.get("output_dir", DENRYU_DEFAULT_OUTPUT_DIR) or DENRYU_DEFAULT_OUTPUT_DIR))
        self.interval.set(str(settings.get("interval", "10") or "10"))
        self.overwrite.set(bool(settings.get("overwrite", False)))
        self.use_live_page.set(bool(settings.get("use_live_page", False)))

    def _option_for_source(self, source_url: str) -> DenryuTournamentOption | None:
        selected = self.option_by_display.get(self.tournament_choice.get())
        if selected is None:
            return None
        try:
            source_key = denryu_tournament_key(source_url)
        except Exception:
            return selected
        if selected.key == source_key:
            return selected
        return next((option for option in self.tournament_options if option.key == source_key), None)


class ShogiDb2DownloadPane(ttk.Frame):
    def __init__(self, master: tk.Misc, kind: DownloadKind) -> None:
        super().__init__(master, padding=12)
        self.kind = kind
        self.tournament_choice = tk.StringVar()
        self.tournament_url = tk.StringVar()
        self.output_dir = tk.StringVar(value=SHOGIDB2_DEFAULT_OUTPUT_DIR)
        self.start_page = tk.StringVar(value="1")
        self.end_page = tk.StringVar(value="1")
        self.interval = tk.StringVar(value="2")
        self.stop_after_skipped = tk.StringVar(value="")
        self.page_load_error_skip_limit = tk.StringVar(value="")
        self.overwrite = tk.BooleanVar(value=False)
        self.tournament_options: list[ShogiDb2TournamentOption] = []
        self.option_by_display: dict[str, ShogiDb2TournamentOption] = {}

        self.columnconfigure(1, weight=1)
        self._build()
        self._set_tournament_options([])

    def _build(self) -> None:
        description = ttk.Label(self, text=self.kind.description, wraplength=680, justify="left")
        description.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        row = 1
        self._label_with_help(
            row,
            "棋戦",
            "shogidb2の棋戦一覧から選択できます。\n"
            "一覧更新を押すと、shogidb2から最新の棋戦一覧を取得します。",
        )
        self.tournament_combo = ttk.Combobox(self, textvariable=self.tournament_choice, state="readonly")
        self.tournament_combo.grid(row=row, column=1, sticky="ew", pady=6)
        self.tournament_combo.bind("<<ComboboxSelected>>", self._on_tournament_selected)
        ttk.Button(self, text="一覧更新", command=self.refresh_tournaments).grid(
            row=row, column=2, sticky="e", padx=(8, 0), pady=6
        )
        row += 1

        row = self._url_row(
            row,
            "棋戦URL",
            self.tournament_url,
            "shogidb2の棋戦URL、または棋戦名を指定できます。\n"
            "コンボボックスで棋戦を選ぶと自動で設定されます。",
        )
        row = self._path_row(
            row,
            "出力フォルダ",
            self.output_dir,
            self._browse_output_dir,
            "KIFファイルを保存する親フォルダを指定してください。\n"
            "実際にはこの配下に棋戦名のフォルダを作成します。",
        )
        self._text_row(
            row,
            "開始ページ",
            self.start_page,
            "ダウンロードを開始するページ番号を指定してください。",
            width=10,
        )
        row += 1
        self._text_row(
            row,
            "終了ページ",
            self.end_page,
            "ダウンロードを終了するページ番号を指定してください。\n"
            "空欄なら、棋譜が見つからないページまで進みます。",
            width=10,
        )
        row += 1
        self._text_row(
            row,
            "アクセス間隔(秒)",
            self.interval,
            "連続アクセスでサーバーに負荷をかけないため、\n"
            "1アクセスごとの待ち時間を秒単位で指定します。\n"
            "2 以上を指定してください。",
            width=10,
        )
        row += 1
        self._skipped_stop_row(row)
        row += 1
        self._page_error_skip_row(row)
        row += 1
        self._label_with_help(
            row,
            "既存ファイルを上書き",
            "既に同名のKIFファイルがある場合に上書きします。\n"
            "チェックなしなら既存ファイルはスキップします。",
        )
        ttk.Checkbutton(self, variable=self.overwrite).grid(row=row, column=1, sticky="w", pady=6)

    def _label_with_help(self, row: int, label: str, help_text: str) -> None:
        label_frame = ttk.Frame(self)
        label_frame.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Label(label_frame, text=label).pack(side="left")
        help_label = ttk.Label(label_frame, text=" ❓", cursor="question_arrow")
        help_label.pack(side="left")
        Tooltip(help_label, help_text)

    def _text_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
        *,
        width: int,
    ) -> None:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable, width=width).grid(row=row, column=1, sticky="w", pady=6)

    def _skipped_stop_row(self, row: int) -> None:
        self._label_with_help(
            row,
            "skipped停止",
            "既存ファイルのためスキップした件数が指定数に達したら停止します。\n"
            "空欄ならこの設定は無効です。",
        )
        frame = ttk.Frame(self)
        frame.grid(row=row, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="skippedが").pack(side="left")
        ttk.Entry(frame, textvariable=self.stop_after_skipped, width=10).pack(side="left", padx=(6, 6))
        ttk.Label(frame, text="件に達したら停止").pack(side="left")

    def _page_error_skip_row(self, row: int) -> None:
        self._label_with_help(
            row,
            "page skip",
            "棋戦ページ自体の読み込みエラーが続く場合に、指定回数までは次ページへ進みます。\n"
            "正常に読み込めたらskipカウントは0に戻ります。\n"
            "空欄ならこの設定は無効です。",
        )
        frame = ttk.Frame(self)
        frame.grid(row=row, column=1, sticky="w", pady=6)
        ttk.Label(frame, text="page読み込みエラーは").pack(side="left")
        ttk.Entry(frame, textvariable=self.page_load_error_skip_limit, width=10).pack(side="left", padx=(6, 6))
        ttk.Label(frame, text="回まではskipする").pack(side="left")

    def _url_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=2, sticky="e", padx=(8, 0), pady=6
        )
        return row + 1

    def _path_row(
        self,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
        help_text: str,
    ) -> int:
        self._label_with_help(row, label, help_text)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(self, text="参照", command=command).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=6)
        ttk.Button(self, text="消去", command=lambda: variable.set("")).grid(
            row=row, column=3, sticky="e", padx=(6, 0), pady=6
        )
        return row + 1

    def refresh_tournaments(self) -> None:
        current = self._selected_or_typed_name()
        try:
            options = fetch_shogidb2_tournament_options()
        except Exception as exc:
            messagebox.showerror(
                "一覧更新失敗",
                f"shogidb2から棋戦一覧を取得できませんでした。\n{exc}",
                parent=self,
            )
            return
        self._set_tournament_options(options, preferred_name=current)

    def _set_tournament_options(
        self,
        options: list[ShogiDb2TournamentOption],
        preferred_name: str | None = None,
    ) -> None:
        self.tournament_options = options
        self.option_by_display = {option.display_name: option for option in options}
        self.tournament_combo.configure(values=list(self.option_by_display))
        if not options:
            return

        selected = None
        if preferred_name is not None:
            selected = next((option for option in options if option.name == preferred_name), None)
        if selected is None:
            selected = options[0]
        self.tournament_choice.set(selected.display_name)
        self._apply_option(selected)

    def _selected_or_typed_name(self) -> str | None:
        option = self.option_by_display.get(self.tournament_choice.get())
        if option is not None:
            return option.name
        source = self.tournament_url.get().strip()
        if not source:
            return None
        try:
            name, _url = normalize_tournament_url(source)
        except Exception:
            return None
        return name

    def _on_tournament_selected(self, _event: tk.Event | None = None) -> None:
        option = self.option_by_display.get(self.tournament_choice.get())
        if option is not None:
            self._apply_option(option)

    def _apply_option(self, option: ShogiDb2TournamentOption) -> None:
        self.tournament_url.set(option.url)

    def _browse_output_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._initial_dir(self.output_dir.get()))
        if selected:
            self.output_dir.set(selected)

    def _initial_dir(self, value: str) -> str:
        if not value:
            return str(BASE_DIR)
        path = Path(value).expanduser()
        if path.is_dir():
            return str(path)
        if path.parent.exists():
            return str(path.parent)
        return str(BASE_DIR)

    def build_job(self) -> ShogiDb2DownloadJob:
        source = self.tournament_url.get().strip()
        if not source:
            raise ValueError("棋戦URL、または棋戦名を指定してください。")

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            raise ValueError("出力フォルダを指定してください。")
        output_root = Path(output_dir).expanduser()
        if output_root.exists() and not output_root.is_dir():
            raise ValueError(f"出力フォルダがファイルです: {output_root}")

        start_page = self._parse_page(self.start_page.get(), "開始ページ")
        end_page_text = self.end_page.get().strip()
        end_page = self._parse_page(end_page_text, "終了ページ") if end_page_text else None
        if end_page is not None and end_page < start_page:
            raise ValueError("終了ページは開始ページ以上を指定してください。")

        return ShogiDb2DownloadJob(
            tournament_url=source,
            output_root=output_root,
            start_page=start_page,
            end_page=end_page,
            interval=parse_download_interval(self.interval.get()),
            overwrite=self.overwrite.get(),
            stop_after_skipped=self._parse_optional_positive_int(
                self.stop_after_skipped.get(),
                "skipped停止件数",
            ),
            page_load_error_skip_limit=self._parse_optional_positive_int(
                self.page_load_error_skip_limit.get(),
                "page読み込みエラーskip回数",
            ),
        )

    def _parse_page(self, value: str, label: str) -> int:
        try:
            page = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{label}は整数で指定してください。") from exc
        if page < 1:
            raise ValueError(f"{label}は1以上を指定してください。")
        return page

    def _parse_optional_positive_int(self, value: str, label: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
        except ValueError as exc:
            raise ValueError(f"{label}は整数で指定してください。") from exc
        if number < 1:
            raise ValueError(f"{label}は1以上を指定してください。")
        return number

    def settings(self) -> dict[str, object]:
        return {
            "tournament_choice": self.tournament_choice.get(),
            "tournament_url": self.tournament_url.get(),
            "output_dir": self.output_dir.get(),
            "start_page": self.start_page.get(),
            "end_page": self.end_page.get(),
            "interval": self.interval.get(),
            "stop_after_skipped": self.stop_after_skipped.get(),
            "page_load_error_skip_limit": self.page_load_error_skip_limit.get(),
            "overwrite": self.overwrite.get(),
        }

    def apply_settings(self, settings: object) -> None:
        if not isinstance(settings, dict):
            return
        choice = str(settings.get("tournament_choice", ""))
        url = str(settings.get("tournament_url", ""))
        if choice in self.option_by_display:
            self.tournament_choice.set(choice)
            self._apply_option(self.option_by_display[choice])
        elif url:
            self.tournament_url.set(url)
        self.output_dir.set(str(settings.get("output_dir", SHOGIDB2_DEFAULT_OUTPUT_DIR) or SHOGIDB2_DEFAULT_OUTPUT_DIR))
        self.start_page.set(str(settings.get("start_page", "1") or "1"))
        self.end_page.set(str(settings.get("end_page", "1")))
        self.interval.set(str(settings.get("interval", "2") or "2"))
        self.stop_after_skipped.set(str(settings.get("stop_after_skipped", "") or ""))
        self.page_load_error_skip_limit.set(str(settings.get("page_load_error_skip_limit", "") or ""))
        self.overwrite.set(bool(settings.get("overwrite", False)))


class KifManager(tk.Tk):
    def __init__(self, *, enable_shogidb: bool = False, from_bookminer: bool = False) -> None:
        super().__init__()
        self.title("KIF Manager")
        self.geometry("920x660")
        self.minsize(780, 560)
        self.from_bookminer = from_bookminer

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.running = False
        self.running_action: str | None = None
        self.cancel_event = threading.Event()
        self.verbose = tk.BooleanVar(value=False)
        self.log_target_files = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="待機中")
        self.extract_panes: dict[str, ExtractorPane] = {}
        self.bookminer_original_output_paths: dict[str, str] = {}
        self.download_panes: dict[
            str,
            DownloadPlaceholderPane | FloodgateDownloadPane | WcscDownloadPane | DenryuDownloadPane | ShogiDb2DownloadPane,
        ] = {}
        self.download_kinds = (*DOWNLOADERS, SHOGIDB2_DOWNLOADER) if enable_shogidb else DOWNLOADERS

        self._build()
        self._load_settings()
        if self.from_bookminer:
            self._apply_bookminer_output_path()
        self.after(100, self._poll_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=1)

        self.main_notebook = ttk.Notebook(self)
        self.main_notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))

        self.extract_tab = ttk.Frame(self.main_notebook)
        self.extract_tab.columnconfigure(0, weight=1)
        self.extract_tab.rowconfigure(0, weight=1)
        self.extract_notebook = ttk.Notebook(self.extract_tab)
        self.extract_notebook.grid(row=0, column=0, sticky="nsew")

        for kind in EXTRACTORS:
            pane = ExtractorPane(self.extract_notebook, kind)
            self.extract_panes[kind.key] = pane
            self.extract_notebook.add(pane, text=kind.title)

        self.download_tab = ttk.Frame(self.main_notebook)
        self.download_tab.columnconfigure(0, weight=1)
        self.download_tab.rowconfigure(0, weight=1)
        self.download_notebook = ttk.Notebook(self.download_tab)
        self.download_notebook.grid(row=0, column=0, sticky="nsew")

        for kind in self.download_kinds:
            if kind.key == "floodgate":
                pane = FloodgateDownloadPane(self.download_notebook, kind)
            elif kind.key == "wcsc":
                pane = WcscDownloadPane(self.download_notebook, kind)
            elif kind.key == "denryu":
                pane = DenryuDownloadPane(self.download_notebook, kind)
            elif kind.key == "shogidb2":
                pane = ShogiDb2DownloadPane(self.download_notebook, kind)
            else:
                pane = DownloadPlaceholderPane(self.download_notebook, kind)
            self.download_panes[kind.key] = pane
            self.download_notebook.add(pane, text=kind.title)

        self.main_notebook.add(self.extract_tab, text="棋譜抽出")
        self.main_notebook.add(self.download_tab, text="棋譜のダウンロード")
        self.main_notebook.bind("<<NotebookTabChanged>>", self._update_action_controls)
        self.extract_notebook.bind("<<NotebookTabChanged>>", self._update_action_controls)
        self.download_notebook.bind("<<NotebookTabChanged>>", self._update_action_controls)

        lower = ttk.Frame(self, padding=(10, 0, 10, 10))
        lower.grid(row=1, column=0, sticky="nsew")
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(1, weight=1)

        controls = ttk.Frame(lower)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        controls.columnconfigure(3, weight=1)

        self.action_button = ttk.Button(controls, text="抽出", command=self._run_current_action)
        self.action_button.grid(row=0, column=0, padx=(0, 16))

        self.verbose_check = ttk.Checkbutton(controls, text="verbose", variable=self.verbose)
        self.verbose_check.grid(row=0, column=1, padx=(0, 16))
        self.log_target_files_check = ttk.Checkbutton(
            controls,
            text="抽出対象のファイルをログに出力",
            variable=self.log_target_files,
        )
        self.log_target_files_check.grid(row=0, column=2, padx=(0, 16))
        ttk.Label(controls, textvariable=self.status).grid(row=0, column=3, sticky="e")

        log_frame = ttk.Frame(lower)
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=14, wrap="none", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=y_scroll.set)

        x_scroll = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log_text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.log_text.configure(xscrollcommand=x_scroll.set)

        self._update_action_controls()

    def _run_current_action(self) -> None:
        if self.running:
            if self.running_action == "download":
                self._request_stop()
                return
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        if self._selected_main_key() == "extract":
            self._extract_current()
            return
        self._download_current()

    def _request_stop(self) -> None:
        if self.cancel_event.is_set():
            return
        self.cancel_event.set()
        self.status.set("停止要求中")
        self._put_log("[KIF Manager] stop requested\n")
        self._set_buttons_enabled(False)

    def _extract_current(self) -> None:
        pane = self._current_extract_pane()
        if pane is None:
            return
        try:
            self._start_jobs([pane.build_job(self.verbose.get(), self.log_target_files.get())])
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc), parent=self)

    def _download_current(self) -> None:
        pane = self._current_download_pane()
        if pane is None:
            return
        if isinstance(pane, FloodgateDownloadPane):
            try:
                job = pane.build_job()
            except ValueError as exc:
                messagebox.showerror("入力エラー", str(exc), parent=self)
                return
            self._start_floodgate_download(job)
            return
        if isinstance(pane, WcscDownloadPane):
            try:
                job = pane.build_job()
            except ValueError as exc:
                messagebox.showerror("入力エラー", str(exc), parent=self)
                return
            self._start_wcsc_download(job)
            return
        if isinstance(pane, DenryuDownloadPane):
            try:
                job = pane.build_job()
            except ValueError as exc:
                messagebox.showerror("入力エラー", str(exc), parent=self)
                return
            self._start_denryu_download(job)
            return
        if isinstance(pane, ShogiDb2DownloadPane):
            try:
                job = pane.build_job()
            except ValueError as exc:
                messagebox.showerror("入力エラー", str(exc), parent=self)
                return
            self._start_shogidb2_download(job)
            return
        self.status.set("このダウンロード画面は未実装")
        messagebox.showinfo("未実装", "このダウンロード画面はまだ未実装です。", parent=self)

    def _current_extract_pane(self) -> ExtractorPane | None:
        selected = self.extract_notebook.select()
        if not selected:
            return None
        return self.nametowidget(selected)  # type: ignore[return-value]

    def _current_download_pane(
        self,
    ) -> DownloadPlaceholderPane | FloodgateDownloadPane | WcscDownloadPane | DenryuDownloadPane | ShogiDb2DownloadPane | None:
        selected = self.download_notebook.select()
        if not selected:
            return None
        return self.nametowidget(selected)  # type: ignore[return-value]

    def _selected_main_key(self) -> str:
        selected = self.main_notebook.select()
        if selected == str(self.download_tab):
            return "download"
        return "extract"

    def _start_jobs(self, jobs: list[ExtractJob]) -> None:
        if self.running:
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        self.running = True
        self.running_action = "extract"
        self.cancel_event.clear()
        self._save_settings()
        self._set_buttons_enabled(False)
        self._clear_log()
        self.status.set("実行中")

        worker = threading.Thread(target=self._worker, args=(jobs,), daemon=True)
        worker.start()

    def _start_floodgate_download(self, job: FloodgateDownloadJob) -> None:
        if self.running:
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        self.running = True
        self.running_action = "download"
        self.cancel_event.clear()
        self._save_settings()
        self._set_buttons_enabled(False)
        self._clear_log()
        self.status.set("ダウンロード中")

        worker = threading.Thread(target=self._floodgate_download_worker, args=(job,), daemon=True)
        worker.start()

    def _start_wcsc_download(self, job: WcscDownloadJob) -> None:
        if self.running:
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        self.running = True
        self.running_action = "download"
        self.cancel_event.clear()
        self._save_settings()
        self._set_buttons_enabled(False)
        self._clear_log()
        self.status.set("ダウンロード中")

        worker = threading.Thread(target=self._wcsc_download_worker, args=(job,), daemon=True)
        worker.start()

    def _start_denryu_download(self, job: DenryuDownloadJob) -> None:
        if self.running:
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        self.running = True
        self.running_action = "download"
        self.cancel_event.clear()
        self._save_settings()
        self._set_buttons_enabled(False)
        self._clear_log()
        self.status.set("ダウンロード中")

        worker = threading.Thread(target=self._denryu_download_worker, args=(job,), daemon=True)
        worker.start()

    def _start_shogidb2_download(self, job: ShogiDb2DownloadJob) -> None:
        if self.running:
            messagebox.showinfo("実行中", "現在の処理が終わるまで待ってください。", parent=self)
            return

        self.running = True
        self.running_action = "download"
        self.cancel_event.clear()
        self._save_settings()
        self._set_buttons_enabled(False)
        self._clear_log()
        self.status.set("ダウンロード中")

        worker = threading.Thread(target=self._shogidb2_download_worker, args=(job,), daemon=True)
        worker.start()

    def _worker(self, jobs: list[ExtractJob]) -> None:
        failed = False
        for job in jobs:
            self._put_log(f"[{job.kind.title}] start\n")
            self._put_log(f"[{job.kind.title}] input  : {job.input_dir}\n")
            self._put_log(f"[{job.kind.title}] output : {job.output_path}\n")
            if job.both_player_list is not None:
                self._put_log(f"[{job.kind.title}] both   : {job.both_player_list}\n")
            if job.either_player_list is not None:
                self._put_log(f"[{job.kind.title}] either : {job.either_player_list}\n")
            if job.require_rating:
                self._put_log(f"[{job.kind.title}] rating : {job.min_rating}\n")
            if job.losing_player_min_rating is not None:
                self._put_log(f"[{job.kind.title}] losing-player rating : {job.losing_player_min_rating}\n")
            if job.start_year is not None or job.end_year is not None:
                self._put_log(
                    f"[{job.kind.title}] years  : "
                    f"{job.start_year if job.start_year is not None else '*'}-"
                    f"{job.end_year if job.end_year is not None else '*'}\n"
                )
            if job.start_date is not None or job.end_date is not None:
                self._put_log(
                    f"[{job.kind.title}] dates  : "
                    f"{job.start_date.isoformat() if job.start_date is not None else '*'}-"
                    f"{job.end_date.isoformat() if job.end_date is not None else '*'}\n"
                )
            if job.wcsc_finalists_only:
                self._put_log(f"[{job.kind.title}] finalists only: True\n")
            if job.reversal_threshold is not None:
                self._put_log(f"[{job.kind.title}] reversal threshold: {job.reversal_threshold}\n")
            if job.exclude_handicap:
                self._put_log(f"[{job.kind.title}] exclude handicap games\n")
            if job.log_target_files:
                self._put_log(f"[{job.kind.title}] log target files: True\n")

            try:
                with contextlib.redirect_stderr(QueueWriter(self.log_queue, job.kind.title)):
                    stats = run_extractor(
                        job.input_dir,
                        job.output_path,
                        job.both_player_list,
                        job.either_player_list,
                        job.min_rating,
                        source_kind=job.kind.key if job.kind.key in {"floodgate", "wcsc", "denryu"} else None,
                        start_year=job.start_year,
                        end_year=job.end_year,
                        start_date=job.start_date,
                        end_date=job.end_date,
                        wcsc_finalists_only=job.wcsc_finalists_only,
                        reversal_threshold=job.reversal_threshold,
                        exclude_handicap=job.exclude_handicap,
                        allow_non_startpos=job.kind.key == "other",
                        require_rating=job.require_rating,
                        losing_player_min_rating=job.losing_player_min_rating,
                        log_target_files=job.log_target_files,
                        verbose=job.verbose,
                    )
            except Exception as exc:
                failed = True
                self._put_log(f"[{job.kind.title}] failed: {exc}\n")
                continue

            self._put_log(f"[{job.kind.title}] {self._stats_text(stats)}\n")
            self._put_log(f"[{job.kind.title}] done\n")

        self.log_queue.put(("done", "失敗あり" if failed else "完了"))

    def _floodgate_download_worker(self, job: FloodgateDownloadJob) -> None:
        self._put_log("[floodgate] start\n")
        self._put_log(f"[floodgate] year      : {job.year}\n")
        self._put_log(f"[floodgate] output dir: {job.output_dir}\n")

        try:
            stats = download_floodgate_kif(
                job,
                log=lambda text: self._put_log(f"[floodgate] {text}"),
                should_stop=self.cancel_event.is_set,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self._put_log("[floodgate] stopped\n")
                self.log_queue.put(("done", "停止"))
                return
            self._put_log(f"[floodgate] failed: {exc}\n")
            self.log_queue.put(("done", "失敗あり"))
            return

        self._put_log(f"[floodgate] {self._floodgate_download_stats_text(stats)}\n")
        self._finish_download_worker("floodgate")

    def _wcsc_download_worker(self, job: WcscDownloadJob) -> None:
        self._put_log("[WCSC] start\n")
        self._put_log(f"[WCSC] tournament : {job.tournament}\n")
        self._put_log(f"[WCSC] output root: {job.output_root}\n")
        self._put_log(f"[WCSC] interval   : {job.interval}\n")
        self._put_log(f"[WCSC] overwrite  : {job.overwrite}\n")
        self._put_log(f"[WCSC] live page  : {job.use_live_page}\n")

        try:
            stats = download_wcsc_kif(
                job,
                log=lambda text: self._put_log(f"[WCSC] {text}"),
                should_stop=self.cancel_event.is_set,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self._put_log("[WCSC] stopped\n")
                self.log_queue.put(("done", "停止"))
                return
            self._put_log(f"[WCSC] failed: {exc}\n")
            self.log_queue.put(("done", "失敗あり"))
            return

        self._put_log(f"[WCSC] {self._wcsc_download_stats_text(stats)}\n")
        self._finish_download_worker("WCSC")

    def _denryu_download_worker(self, job: DenryuDownloadJob) -> None:
        self._put_log("[電竜戦] start\n")
        self._put_log(f"[電竜戦] source url : {job.source_url}\n")
        self._put_log(f"[電竜戦] output root: {job.output_root}\n")
        self._put_log(f"[電竜戦] interval   : {job.interval}\n")
        self._put_log(f"[電竜戦] overwrite  : {job.overwrite}\n")
        self._put_log(f"[電竜戦] live page  : {job.use_live_page}\n")

        try:
            stats = download_denryu_kif(
                job,
                log=lambda text: self._put_log(f"[電竜戦] {text}"),
                should_stop=self.cancel_event.is_set,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self._put_log("[電竜戦] stopped\n")
                self.log_queue.put(("done", "停止"))
                return
            self._put_log(f"[電竜戦] failed: {exc}\n")
            self.log_queue.put(("done", "失敗あり"))
            return

        self._put_log(f"[電竜戦] {self._denryu_download_stats_text(stats)}\n")
        self._finish_download_worker("電竜戦")

    def _shogidb2_download_worker(self, job: ShogiDb2DownloadJob) -> None:
        self._put_log("[shogidb2] start\n")
        self._put_log(f"[shogidb2] tournament : {job.tournament_url}\n")
        self._put_log(f"[shogidb2] output root: {job.output_root}\n")
        self._put_log(
            f"[shogidb2] pages      : {job.start_page}-"
            f"{job.end_page if job.end_page is not None else '*'}\n"
        )
        self._put_log(f"[shogidb2] interval   : {job.interval}\n")
        self._put_log(f"[shogidb2] overwrite  : {job.overwrite}\n")
        self._put_log(
            f"[shogidb2] stop skip  : "
            f"{job.stop_after_skipped if job.stop_after_skipped is not None else 'disabled'}\n"
        )
        self._put_log(
            f"[shogidb2] page skip  : "
            f"{job.page_load_error_skip_limit if job.page_load_error_skip_limit is not None else 'disabled'}\n"
        )

        try:
            stats = download_shogidb2_kif(
                job,
                log=lambda text: self._put_log(f"[shogidb2] {text}"),
                should_stop=self.cancel_event.is_set,
            )
        except Exception as exc:
            if self.cancel_event.is_set():
                self._put_log("[shogidb2] stopped\n")
                self.log_queue.put(("done", "停止"))
                return
            self._put_log(f"[shogidb2] failed: {exc}\n")
            self.log_queue.put(("done", "失敗あり"))
            return

        self._put_log(f"[shogidb2] {self._shogidb2_download_stats_text(stats)}\n")
        self._finish_download_worker("shogidb2")

    def _finish_download_worker(self, prefix: str) -> None:
        if self.cancel_event.is_set():
            self._put_log(f"[{prefix}] stopped\n")
            self.log_queue.put(("done", "停止"))
            return
        self._put_log(f"[{prefix}] done\n")
        self.log_queue.put(("done", "完了"))

    def _stats_text(self, stats: Stats) -> str:
        return (
            f"scanned={stats.scanned} selected={stats.selected} "
            f"skipped_year={stats.skipped_year} skipped_date={stats.skipped_date} "
            f"skipped_finalist={stats.skipped_finalist} "
            f"skipped_name={stats.skipped_name} skipped_rating={stats.skipped_rating} "
            f"skipped_reversal={stats.skipped_reversal} "
            f"skipped_handicap={stats.skipped_handicap} "
            f"skipped_parse={stats.skipped_parse} skipped_duplicate={stats.skipped_duplicate}"
        )

    def _floodgate_download_stats_text(self, stats: FloodgateDownloadStats) -> str:
        return (
            f"year={stats.year} skipped={stats.skipped} bytes={stats.bytes_written} "
            f"destination={stats.destination}"
        )

    def _wcsc_download_stats_text(self, stats: WcscDownloadStats) -> str:
        return (
            f"tournament={stats.tournament} found={stats.found} "
            f"downloaded={stats.downloaded} skipped={stats.skipped} "
            f"output={stats.output_dir}"
        )

    def _denryu_download_stats_text(self, stats: DenryuDownloadStats) -> str:
        return (
            f"mode={stats.mode} tournament={stats.tournament} found={stats.found} "
            f"downloaded={stats.downloaded} skipped={stats.skipped} "
            f"output={stats.output_dir}"
        )

    def _shogidb2_download_stats_text(self, stats: ShogiDb2DownloadStats) -> str:
        return (
            f"tournament={stats.tournament} pages={stats.pages_scanned} found={stats.found} "
            f"downloaded={stats.downloaded} skipped={stats.skipped} failed={stats.failed} "
            f"page_load_errors={stats.page_load_errors} "
            f"output={stats.output_dir}"
        )

    def _put_log(self, text: str) -> None:
        self.log_queue.put(("log", text))

    def _poll_log_queue(self) -> None:
        while True:
            try:
                kind, text = self.log_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._append_log(text)
            elif kind == "done":
                self.running = False
                self.running_action = None
                self.cancel_event.clear()
                self._set_buttons_enabled(True)
                self._update_action_controls()
                self.status.set(text)

        self.after(100, self._poll_log_queue)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self._trim_log()
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _trim_log(self) -> None:
        line_count = int(self.log_text.index("end-1c").split(".", 1)[0])
        if line_count <= LOG_TRIM_THRESHOLD:
            return

        delete_to_line = line_count - LOG_MAX_LINES + 1
        self.log_text.delete("1.0", f"{delete_to_line}.0")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_buttons_enabled(self, enabled: bool) -> None:
        if not enabled and self.running_action == "download":
            if self.cancel_event.is_set():
                self.action_button.configure(text="停止中...", state="disabled")
            else:
                self.action_button.configure(text="停止", state="normal")
        else:
            state = "normal" if enabled else "disabled"
            self.action_button.configure(state=state)
        extract_option_state = "normal" if enabled and self._selected_main_key() == "extract" else "disabled"
        self.verbose_check.configure(state=extract_option_state)
        self.log_target_files_check.configure(state=extract_option_state)

    def _update_action_controls(self, _event: tk.Event | None = None) -> None:
        if self.running:
            self._set_buttons_enabled(False)
            return

        if self._selected_main_key() == "extract":
            self.action_button.configure(text="抽出", state="normal")
            self.verbose_check.configure(state="normal")
            self.log_target_files_check.configure(state="normal")
            if self.status.get() in {
                "floodgateダウンロード画面",
                "WCSCダウンロード画面",
                "電竜戦ダウンロード画面",
                "shogidb2ダウンロード画面",
                "このダウンロード画面は未実装",
            }:
                self.status.set("待機中")
            return

        self.action_button.configure(text="ダウンロード", state="normal")
        self.verbose_check.configure(state="disabled")
        self.log_target_files_check.configure(state="disabled")
        if isinstance(self._current_download_pane(), FloodgateDownloadPane):
            self.status.set("floodgateダウンロード画面")
        elif isinstance(self._current_download_pane(), WcscDownloadPane):
            self.status.set("WCSCダウンロード画面")
        elif isinstance(self._current_download_pane(), DenryuDownloadPane):
            self.status.set("電竜戦ダウンロード画面")
        elif isinstance(self._current_download_pane(), ShogiDb2DownloadPane):
            self.status.set("shogidb2ダウンロード画面")
        else:
            self.status.set("このダウンロード画面は未実装")

    def _load_settings(self) -> None:
        settings_path = SETTINGS_PATH
        try:
            with settings_path.open("rb") as f:
                settings = pickle.load(f)
        except FileNotFoundError:
            return
        except Exception as exc:
            self.status.set(f"設定読み込み失敗: {exc}")
            return

        if not isinstance(settings, dict):
            return
        if settings.get("version") != SETTINGS_VERSION:
            return

        self.verbose.set(bool(settings.get("verbose", False)))
        self.log_target_files.set(bool(settings.get("log_target_files", False)))
        extract_panes = settings.get("extract_panes", settings.get("panes", {}))
        if isinstance(extract_panes, dict):
            for key, pane in self.extract_panes.items():
                pane.apply_settings(extract_panes.get(key))

        download_panes = settings.get("download_panes", {})
        if isinstance(download_panes, dict):
            for key, pane in self.download_panes.items():
                pane.apply_settings(download_panes.get(key))

        selected_extract_key = settings.get("selected_extract_tab", settings.get("selected_tab"))
        if isinstance(selected_extract_key, str) and selected_extract_key in self.extract_panes:
            self.extract_notebook.select(self.extract_panes[selected_extract_key])

        selected_download_key = settings.get("selected_download_tab")
        if isinstance(selected_download_key, str) and selected_download_key in self.download_panes:
            self.download_notebook.select(self.download_panes[selected_download_key])

        selected_main_key = settings.get("selected_main_tab")
        if selected_main_key == "download":
            self.main_notebook.select(self.download_tab)
        else:
            self.main_notebook.select(self.extract_tab)
        self._update_action_controls()

    def _apply_bookminer_output_path(self) -> None:
        for pane in self.extract_panes.values():
            self.bookminer_original_output_paths[pane.kind.key] = pane.output_path.get()
            pane.output_path.set(BOOKMINER_EXTRACT_OUTPUT_FILE)

    def _save_settings(self) -> None:
        selected_extract_pane = self._current_extract_pane()
        selected_extract_key = selected_extract_pane.kind.key if selected_extract_pane is not None else ""
        selected_download_pane = self._current_download_pane()
        selected_download_key = selected_download_pane.kind.key if selected_download_pane is not None else ""
        extract_pane_settings = {key: pane.settings() for key, pane in self.extract_panes.items()}
        if self.from_bookminer:
            for key, output_path in self.bookminer_original_output_paths.items():
                pane_settings = extract_pane_settings.get(key)
                if pane_settings is not None:
                    pane_settings["output_path"] = output_path

        settings = {
            "version": SETTINGS_VERSION,
            "verbose": self.verbose.get(),
            "log_target_files": self.log_target_files.get(),
            "selected_main_tab": self._selected_main_key(),
            "selected_extract_tab": selected_extract_key,
            "selected_download_tab": selected_download_key,
            "extract_panes": extract_pane_settings,
            "download_panes": {key: pane.settings() for key, pane in self.download_panes.items()},
        }

        try:
            with SETTINGS_PATH.open("wb") as f:
                pickle.dump(settings, f)
        except Exception as exc:
            self.status.set(f"設定保存失敗: {exc}")

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno("終了", "処理中です。終了しますか？", parent=self):
                return
        self._save_settings()
        self.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="KIF Manager")
    parser.add_argument("--shogidb", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--from_bookminer",
        action="store_true",
        help="force extractor output file to BookMiner/book/think_sfens.txt",
    )
    args = parser.parse_args()

    app = KifManager(enable_shogidb=args.shogidb, from_bookminer=args.from_bookminer)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
