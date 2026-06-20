#!/usr/bin/env python3
"""GUI wrapper for .db <-> .ybb conversion."""

from __future__ import annotations

import contextlib
import queue
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from convert_db_to_ybb import (
    DEFAULT_CHUNK_BYTES as DB_TO_YBB_CHUNK_BYTES,
    DEFAULT_CHUNK_POSITIONS as DB_TO_YBB_CHUNK_POSITIONS,
    DEFAULT_MAX_OPEN_RUNS as DB_TO_YBB_MAX_OPEN_RUNS,
    convert_db_to_ybb,
    make_work_dir as make_db_to_ybb_work_dir,
)
from convert_ybb_to_db import (
    DEFAULT_CHUNK_BYTES as YBB_TO_DB_CHUNK_BYTES,
    DEFAULT_CHUNK_POSITIONS as YBB_TO_DB_CHUNK_POSITIONS,
    DEFAULT_MAX_OPEN_RUNS as YBB_TO_DB_MAX_OPEN_RUNS,
    convert_ybb_to_db,
    make_work_dir as make_ybb_to_db_work_dir,
)
from YaneuraOuBookLib import cleanup_work_dir, resolve_ybb_input, ybb_path_from_output


DEFAULT_TMP_DIR = Path(tempfile.gettempdir()) / "yaneuraou-book-converter"


class GuiLogWriter:
    def __init__(self, log) -> None:  # type:ignore[no-untyped-def]
        self.log = log
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line:
                self.log(line)
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.log(self.buffer)
            self.buffer = ""


def normalize_output_path(path: Path, direction: str) -> Path:
    if direction == "db_to_ybb":
        return ybb_path_from_output(path)
    if path.suffix.lower() == ".db":
        return path
    return path.with_suffix(path.suffix + ".db") if path.suffix else path.with_suffix(".db")


def default_output_path(input_path: Path) -> Path:
    suffix = input_path.suffix.lower()
    if suffix == ".db":
        return input_path.with_suffix(".ybb")
    if suffix == ".ybb":
        return input_path.with_suffix(".db")
    return input_path.with_name(f"{input_path.name}.converted")


def conversion_direction(input_path: Path) -> str:
    suffix = input_path.suffix.lower()
    if suffix == ".db":
        return "db_to_ybb"
    if suffix == ".ybb":
        return "ybb_to_db"
    raise ValueError("入力ファイルは .db または .ybb を指定してください。")


def direction_label(direction: str) -> str:
    if direction == "db_to_ybb":
        return ".db -> .ybb"
    if direction == "ybb_to_db":
        return ".ybb -> .db"
    return "未選択"


def run_conversion(
    *,
    input_path: Path,
    output_path: Path,
    tmp_dir: Path,
    include_depth: bool,
    keep_temp: bool,
    log,
) -> Path:  # type:ignore[no-untyped-def]
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    tmp_dir = tmp_dir.resolve()
    direction = conversion_direction(input_path)
    output_path = normalize_output_path(output_path, direction)

    if not input_path.is_file():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {input_path}")
    if output_path == input_path:
        raise ValueError("入力ファイルと出力ファイルが同じです。別の出力先を指定してください。")

    tmp_dir_existed = tmp_dir.exists()
    log(f"input: {input_path}")
    log(f"output: {output_path}")
    log(f"direction: {direction_label(direction)}")
    log(f"temp dir: {tmp_dir}")

    if direction == "db_to_ybb":
        work_dir = make_db_to_ybb_work_dir(tmp_dir)
        log(f"work dir: {work_dir}")
        try:
            convert_db_to_ybb(
                input_path,
                output_path,
                work_dir,
                DB_TO_YBB_CHUNK_POSITIONS,
                DB_TO_YBB_CHUNK_BYTES,
                DB_TO_YBB_MAX_OPEN_RUNS,
                include_depth,
            )
        finally:
            if keep_temp:
                log(f"keep temp: {work_dir}")
            else:
                cleanup_work_dir(work_dir, tmp_dir, tmp_dir_existed)
        return ybb_path_from_output(output_path)

    resolve_ybb_input(input_path)
    work_dir = make_ybb_to_db_work_dir(tmp_dir)
    log(f"work dir: {work_dir}")
    try:
        convert_ybb_to_db(
            input_path,
            output_path,
            work_dir,
            YBB_TO_DB_CHUNK_POSITIONS,
            YBB_TO_DB_CHUNK_BYTES,
            YBB_TO_DB_MAX_OPEN_RUNS,
        )
    finally:
        if keep_temp:
            log(f"keep temp: {work_dir}")
        else:
            cleanup_work_dir(work_dir, tmp_dir, tmp_dir_existed)
    return output_path


class BookConverterGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YaneuraOu Book Converter")
        self.geometry("860x560")
        self.minsize(720, 460)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.direction_var = tk.StringVar(value="未選択")
        self.tmp_dir_var = tk.StringVar(value=str(DEFAULT_TMP_DIR))
        self.include_depth_var = tk.BooleanVar(value=True)
        self.keep_temp_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="ready")
        self.message_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self._build_widgets()
        self.after(100, self._poll_messages)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        form = ttk.Frame(self, padding=10)
        form.grid(row=0, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="入力ファイル (.db / .ybb)").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(form, text="選択", command=self._choose_input).grid(row=0, column=2)

        ttk.Label(form, text="出力ファイル").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(form, text="選択", command=self._choose_output).grid(row=1, column=2, pady=(6, 0))

        ttk.Label(form, text="変換方向").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Label(form, textvariable=self.direction_var).grid(row=2, column=1, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(form, text="一時フォルダ").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.tmp_dir_var).grid(row=3, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(form, text="選択", command=self._choose_tmp_dir).grid(row=3, column=2, pady=(6, 0))

        options = ttk.Frame(form)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.include_depth_check = ttk.Checkbutton(
            options,
            text=".db -> .ybb で depth を保存",
            variable=self.include_depth_var,
        )
        self.include_depth_check.pack(side="left")
        ttk.Checkbutton(
            options,
            text="一時ファイルを残す",
            variable=self.keep_temp_var,
        ).pack(side="left", padx=(18, 0))

        action = ttk.Frame(form)
        action.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.convert_button = ttk.Button(action, text="変換", command=self._start_conversion)
        self.convert_button.pack(side="left")
        ttk.Label(action, textvariable=self.status_var).pack(side="left", padx=(12, 0))

        log_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", font=("Consolas", 10))
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

    def _choose_input(self) -> None:
        path = filedialog.askopenfilename(
            title="入力ファイルを選択",
            filetypes=[
                ("YaneuraOu books", "*.db *.ybb"),
                ("YaneuraOu DB", "*.db"),
                ("YaneuraOu binary book", "*.ybb"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        input_path = Path(path)
        self.input_var.set(str(input_path))
        self.output_var.set(str(default_output_path(input_path)))
        self._refresh_direction()

    def _choose_output(self) -> None:
        input_text = self.input_var.get().strip()
        if not input_text:
            messagebox.showerror("入力エラー", "先に入力ファイルを選択してください。")
            return
        try:
            direction = conversion_direction(Path(input_text))
        except ValueError as exc:
            messagebox.showerror("入力エラー", str(exc))
            return

        initial = self.output_var.get()
        default_ext = ".ybb" if direction == "db_to_ybb" else ".db"
        filetypes = (
            [("YaneuraOu binary book", "*.ybb"), ("All files", "*.*")]
            if direction == "db_to_ybb"
            else [("YaneuraOu DB", "*.db"), ("All files", "*.*")]
        )
        path = filedialog.asksaveasfilename(
            title="出力ファイルを選択",
            initialfile=Path(initial).name if initial else "",
            initialdir=str(Path(initial).parent) if initial else "",
            defaultextension=default_ext,
            filetypes=filetypes,
        )
        if path:
            self.output_var.set(str(normalize_output_path(Path(path), direction)))

    def _choose_tmp_dir(self) -> None:
        path = filedialog.askdirectory(title="一時フォルダを選択")
        if path:
            self.tmp_dir_var.set(path)

    def _refresh_direction(self) -> None:
        try:
            direction = conversion_direction(Path(self.input_var.get()))
        except ValueError:
            self.direction_var.set("未選択")
            self.include_depth_check.configure(state="disabled")
            return
        self.direction_var.set(direction_label(direction))
        self.include_depth_check.configure(state="normal" if direction == "db_to_ybb" else "disabled")

    def _start_conversion(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.input_var.get().strip() or not self.output_var.get().strip():
            messagebox.showerror("入力エラー", "入力ファイルと出力ファイルを指定してください。")
            return

        try:
            input_path = Path(self.input_var.get())
            direction = conversion_direction(input_path)
            output_path = normalize_output_path(Path(self.output_var.get()), direction)
            tmp_dir = Path(self.tmp_dir_var.get() or DEFAULT_TMP_DIR)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("入力エラー", str(exc))
            return

        if output_path.exists() and not messagebox.askyesno("上書き確認", f"上書きしますか？\n{output_path}"):
            return

        self._refresh_direction()
        self.log_text.delete("1.0", "end")
        self.convert_button.configure(state="disabled")
        self.status_var.set("running")

        def log(text: str) -> None:
            self.message_queue.put(("log", text))

        def worker() -> None:
            start = time.time()
            writer = GuiLogWriter(log)
            try:
                with contextlib.redirect_stdout(writer):
                    result = run_conversion(
                        input_path=input_path,
                        output_path=output_path,
                        tmp_dir=tmp_dir,
                        include_depth=self.include_depth_var.get(),
                        keep_temp=self.keep_temp_var.get(),
                        log=log,
                    )
                writer.flush()
            except Exception as exc:
                writer.flush()
                self.message_queue.put(("error", str(exc)))
            else:
                elapsed = time.time() - start
                self.message_queue.put(("done", f"{result}\n{elapsed:.1f} sec"))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _poll_messages(self) -> None:
        while True:
            try:
                kind, text = self.message_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(text)
            elif kind == "error":
                self._log(f"ERROR: {text}")
                self.status_var.set("failed")
                self.convert_button.configure(state="normal")
                messagebox.showerror("変換失敗", text)
            elif kind == "done":
                self._log(f"done: {text}")
                self.status_var.set("done")
                self.convert_button.configure(state="normal")
                messagebox.showinfo("変換完了", f"出力しました。\n{text}")
        self.after(100, self._poll_messages)

    def _log(self, text: str) -> None:
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")


def main() -> int:
    app = BookConverterGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
