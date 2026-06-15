#!/usr/bin/env python3
"""GUI wrapper for peta-shock and .ybb conversion."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPT_COLLECTION_DIR = SCRIPT_DIR.parent
DEFAULT_ENGINE_PATH = SCRIPT_COLLECTION_DIR / "BookMiner" / "YO-MATERIAL.exe"
DEFAULT_CONVERTER_PATH = SCRIPT_DIR / "convert_db_to_ybb.py"
PETA_PROGRESS_INTERVAL = 30


class PetaShockError(RuntimeError):
    pass


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}-peta.ybb")


def normalize_ybb_output(path: Path) -> Path:
    if path.suffix.lower() == ".ybb":
        return path
    return path.with_suffix(path.suffix + ".ybb") if path.suffix else path.with_suffix(".ybb")


def quote_for_log(path: Path | str) -> str:
    text = str(path)
    return f'"{text}"' if " " in text else text


def create_source_link_or_copy(source: Path, alias: Path, log) -> None:  # type:ignore[no-untyped-def]
    try:
        os.link(source, alias)
        log(f"source link: {quote_for_log(alias)}")
        return
    except OSError as exc:
        log(f"hardlink failed, copy source instead: {exc}")
    shutil.copy2(source, alias)
    log(f"source copy: {quote_for_log(alias)}")


def stream_process(
    command: list[str],
    *,
    cwd: Path,
    log,
    input_lines: list[str] | None = None,
    progress_label: str = "process",
) -> None:  # type:ignore[no-untyped-def]
    log(f"run: {' '.join(quote_for_log(part) for part in command)}")
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input_lines is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    output_queue: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                output_queue.put(line.rstrip("\r\n"))
        finally:
            output_queue.put(None)

    threading.Thread(target=read_output, daemon=True).start()

    if input_lines is not None:
        assert process.stdin is not None
        try:
            for line in input_lines:
                process.stdin.write(line + "\n")
            process.stdin.flush()
        except OSError as exc:
            log(f"stdin write failed: {exc}")
        finally:
            process.stdin.close()

    start_time = time.time()
    last_progress_time = start_time
    output_done = False

    while True:
        try:
            line = output_queue.get(timeout=1)
            if line is None:
                output_done = True
            elif line:
                log(line)
        except queue.Empty:
            pass

        now = time.time()
        if now - last_progress_time >= PETA_PROGRESS_INTERVAL:
            log(f"[{progress_label}] running... elapsed {int(now - start_time)}s")
            last_progress_time = now

        if process.poll() is not None and output_done and output_queue.empty():
            break

    return_code = process.wait()
    if return_code != 0:
        raise PetaShockError(f"{progress_label} failed. return code = {return_code}")


def run_peta_shock(
    *,
    input_path: Path,
    output_path: Path,
    engine_path: Path,
    converter_path: Path,
    log,
) -> None:  # type:ignore[no-untyped-def]
    input_path = input_path.resolve()
    output_path = normalize_ybb_output(output_path.resolve())
    engine_path = engine_path.resolve()
    converter_path = converter_path.resolve()

    if input_path.suffix.lower() not in (".db", ".ybb"):
        raise PetaShockError("input must be .db or .ybb")
    if not input_path.is_file():
        raise PetaShockError(f"input file not found: {input_path}")
    if not engine_path.is_file():
        raise PetaShockError(f"YO-MATERIAL.exe not found: {engine_path}")
    if input_path.suffix.lower() == ".db" and not converter_path.is_file():
        raise PetaShockError(f"convert_db_to_ybb.py not found: {converter_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path == output_path:
        raise PetaShockError("input and output paths must be different")

    work_dir = Path(tempfile.mkdtemp(prefix="peta-shock-gui-", dir=input_path.parent))
    log(f"work dir: {quote_for_log(work_dir)}")
    try:
        source_alias = work_dir / f"source{input_path.suffix.lower()}"
        create_source_link_or_copy(input_path, source_alias, log)

        if input_path.suffix.lower() == ".db":
            peta_db = work_dir / "peta.db"
            run_engine_peta_shock(
                engine_path=engine_path,
                book_dir=work_dir,
                source_name=source_alias.name,
                output_name=peta_db.name,
                log=log,
            )
            if not peta_db.is_file() or peta_db.stat().st_size == 0:
                raise PetaShockError(f"peta-shock .db was not created: {peta_db}")
            convert_tmp = work_dir / "convert-tmp"
            run_db_to_ybb_converter(
                converter_path=converter_path,
                input_db=peta_db,
                output_ybb=output_path,
                tmp_dir=convert_tmp,
                log=log,
            )
        else:
            peta_ybb = work_dir / "peta.ybb"
            run_engine_peta_shock(
                engine_path=engine_path,
                book_dir=work_dir,
                source_name=source_alias.name,
                output_name=peta_ybb.name,
                log=log,
            )
            if not peta_ybb.is_file() or peta_ybb.stat().st_size == 0:
                raise PetaShockError(f"peta-shock .ybb was not created: {peta_ybb}")
            if output_path.exists():
                output_path.unlink()
            os.replace(peta_ybb, output_path)
            log(f"wrote: {quote_for_log(output_path)}")
    except Exception:
        log(f"keep work dir for inspection: {quote_for_log(work_dir)}")
        raise
    else:
        shutil.rmtree(work_dir, ignore_errors=True)
        log("cleaned work dir")


def run_engine_peta_shock(
    *,
    engine_path: Path,
    book_dir: Path,
    source_name: str,
    output_name: str,
    log,
) -> None:  # type:ignore[no-untyped-def]
    makebook_command = f"makebook peta_shock {source_name} {output_name}"
    commands = [
        f"setoption name BookDir value {book_dir}",
        "setoption name BookFile value no_book",
        "setoption name FlippedBook value true",
        "setoption name USI_Hash value 1",
        makebook_command,
        "quit",
    ]
    log("start peta_shock makebook")
    log(f"engine: {quote_for_log(engine_path)}")
    log(f"BookDir: {quote_for_log(book_dir)}")
    log(f"command: {makebook_command}")
    stream_process(
        [str(engine_path)],
        cwd=engine_path.parent,
        input_lines=commands,
        progress_label="peta_shock",
        log=log,
    )


def run_db_to_ybb_converter(
    *,
    converter_path: Path,
    input_db: Path,
    output_ybb: Path,
    tmp_dir: Path,
    log,
) -> None:  # type:ignore[no-untyped-def]
    command = [
        sys.executable,
        str(converter_path),
        str(input_db),
        str(output_ybb),
        "--tmp-dir",
        str(tmp_dir),
    ]
    log("start db_to_ybb conversion")
    stream_process(command, cwd=converter_path.parent, progress_label="db_to_ybb", log=log)
    if not output_ybb.is_file() or output_ybb.stat().st_size == 0:
        raise PetaShockError(f".ybb was not created: {output_ybb}")


class PetaShockGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("peta_shock GUI")
        self.geometry("900x620")
        self.minsize(760, 520)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.engine_var = tk.StringVar(value=str(DEFAULT_ENGINE_PATH))
        self.converter_var = tk.StringVar(value=str(DEFAULT_CONVERTER_PATH))
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

        ttk.Label(form, text="入力定跡 (.db / .ybb)").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(form, text="選択", command=self._choose_input).grid(row=0, column=2)

        ttk.Label(form, text="出力 .ybb").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(form, text="選択", command=self._choose_output).grid(row=1, column=2, pady=(6, 0))

        ttk.Label(form, text="YO-MATERIAL.exe").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.engine_var).grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(form, text="選択", command=self._choose_engine).grid(row=2, column=2, pady=(6, 0))

        ttk.Label(form, text="convert_db_to_ybb.py").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.converter_var).grid(row=3, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(form, text="選択", command=self._choose_converter).grid(row=3, column=2, pady=(6, 0))

        action = ttk.Frame(form)
        action.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
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
            title="入力定跡を選択",
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

    def _choose_output(self) -> None:
        initial = self.output_var.get()
        path = filedialog.asksaveasfilename(
            title="出力 .ybb を選択",
            initialfile=Path(initial).name if initial else "",
            initialdir=str(Path(initial).parent) if initial else "",
            defaultextension=".ybb",
            filetypes=[("YaneuraOu binary book", "*.ybb"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(str(normalize_ybb_output(Path(path))))

    def _choose_engine(self) -> None:
        path = filedialog.askopenfilename(
            title="YO-MATERIAL.exe を選択",
            initialdir=str(DEFAULT_ENGINE_PATH.parent),
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.engine_var.set(path)

    def _choose_converter(self) -> None:
        path = filedialog.askopenfilename(
            title="convert_db_to_ybb.py を選択",
            initialdir=str(SCRIPT_DIR),
            filetypes=[("Python", "*.py"), ("All files", "*.*")],
        )
        if path:
            self.converter_var.set(path)

    def _start_conversion(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.input_var.get().strip() or not self.output_var.get().strip():
            messagebox.showerror("入力エラー", "入力定跡と出力先を指定してください。")
            return
        try:
            input_path = Path(self.input_var.get())
            output_path = normalize_ybb_output(Path(self.output_var.get()))
            engine_path = Path(self.engine_var.get())
            converter_path = Path(self.converter_var.get())
        except (TypeError, ValueError) as exc:
            messagebox.showerror("入力エラー", str(exc))
            return

        if output_path.exists() and not messagebox.askyesno("上書き確認", f"上書きしますか？\n{output_path}"):
            return

        self.log_text.delete("1.0", "end")
        self.convert_button.configure(state="disabled")
        self.status_var.set("running")

        def worker() -> None:
            try:
                run_peta_shock(
                    input_path=input_path,
                    output_path=output_path,
                    engine_path=engine_path,
                    converter_path=converter_path,
                    log=lambda text: self.message_queue.put(("log", text)),
                )
            except Exception as exc:
                self.message_queue.put(("error", str(exc)))
            else:
                self.message_queue.put(("done", str(output_path)))

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
    app = PetaShockGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
