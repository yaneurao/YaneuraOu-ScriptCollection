#!/usr/bin/env python3
from __future__ import annotations

import os
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
KIF_MANAGER_SCRIPT = BASE_DIR.parent / "KifManager" / "kif-manager.py"
BOOK_PROGRESS_RE = re.compile(r"\[Book(Read|Write)(Start|Progress|Done)\]\s+(\d+)/(\d+|\?)")
TASK_QUEUE_PROGRESS_RE = re.compile(r"\[TaskQueue(Start|Progress|Done)\]\s+(\d+)/(\d+|\?)")
MINING_PROGRESS_RE = re.compile(r"\[MiningProgress\]\s+positions=(\d+)")
STEP_BUTTON_WIDTH = 12
LOG_MAX_LINES = 1000
LOG_TRIM_THRESHOLD = 1200
MINING_STATS_SAMPLE_INTERVAL_MS = 60 * 1000
MINING_STATS_WINDOW_SECONDS = 60 * 60


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
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=12)
        self.master = master
        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str | None] = queue.Queue()
        self.output_buffer = ""
        self.log_widgets: dict[str, scrolledtext.ScrolledText] = {}
        self.progress_labels = {
            "read": tk.StringVar(value="定跡読込: 待機中"),
            "write": tk.StringVar(value="定跡書込: 待機中"),
            "task": tk.StringVar(value="enqueue進捗: 待機中"),
        }
        self.progress_bars: dict[str, ttk.Progressbar] = {}
        self.mining_status = tk.StringVar(value="現在の局面数 - 局面    現在の採掘速度 - pos/day")
        self.latest_mining_positions: int | None = None
        self.mining_samples: list[tuple[float, int]] = []

        self.eval_diff = tk.StringVar(value="30")
        self.max_step = tk.StringVar()
        self.eval_limit = tk.StringVar(value="400")

        self.grid(sticky="nsew")
        self._build()
        self._poll_output()
        self._poll_mining_stats()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        bookminer_controls = ttk.Frame(top)
        bookminer_controls.grid(row=0, column=0, sticky="w")
        self.start_button = ttk.Button(bookminer_controls, text="BookMiner起動", command=self.start_process)
        self.start_button.pack(side="left")
        Tooltip(self.start_button, "BookMiner.py を子プロセスとして起動します。")
        self.quit_button = ttk.Button(bookminer_controls, text="BookMiner終了", command=lambda: self.send_command("q"))
        self.quit_button.pack(side="left", padx=(8, 0))
        Tooltip(self.quit_button, "`q` を送信し、book/backup/ に現在の定跡DBを書き出して終了します。")

        self.kif_manager_button = ttk.Button(top, text="棋譜抽出", command=self.start_kif_manager)
        self.kif_manager_button.grid(row=0, column=2, sticky="e")
        Tooltip(self.kif_manager_button, "KifManager を起動します。棋譜抽出結果は book/think_sfens.txt に保存してください。")

        commands = ttk.Frame(self)
        commands.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        commands.columnconfigure(8, weight=1)

        ttk.Label(commands, text="手順1.").grid(row=0, column=0, sticky="w", pady=3)
        peta_button = ttk.Button(
            commands,
            text="peta_shock",
            width=STEP_BUTTON_WIDTH,
            command=lambda: self.send_command("p"),
        )
        peta_button.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(peta_button, "`p` を送信します。定跡DBを書き出し、そのファイルを peta shock 化して読み込みます。")

        ttk.Label(commands, text="手順2.").grid(row=1, column=0, sticky="w", pady=3)
        next_button = ttk.Button(
            commands,
            text="peta_next",
            width=STEP_BUTTON_WIDTH,
            command=self.send_peta_next,
        )
        next_button.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(next_button, "`n eval_diff [max_step]` を送信します。peta shock 化した定跡から次に掘る leaf 局面を作ります。")
        ttk.Label(commands, text="eval_diff").grid(row=1, column=2, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.eval_diff, width=8).grid(row=1, column=3, sticky="w", pady=3)
        ttk.Label(commands, text="max step").grid(row=1, column=4, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.max_step, width=8).grid(row=1, column=5, sticky="w", pady=3)

        ttk.Label(commands, text="手順3.").grid(row=2, column=0, sticky="w", pady=3)
        enqueue_button = ttk.Button(
            commands,
            text="enqueue",
            width=STEP_BUTTON_WIDTH,
            command=self.send_think,
        )
        enqueue_button.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=3)
        Tooltip(enqueue_button, "`e eval_limit` を送信してから `t` を送信し、book/think_sfens.txt の局面を探索キューに積みます。")
        ttk.Label(commands, text="eval_limit").grid(row=2, column=2, sticky="w", padx=(12, 6), pady=3)
        ttk.Entry(commands, textvariable=self.eval_limit, width=8).grid(row=2, column=3, sticky="w", pady=3)

        write_button = ttk.Button(commands, text="定跡DBのbackup", command=lambda: self.send_command("w"))
        write_button.grid(row=2, column=9, sticky="e", padx=(16, 0), pady=3)
        Tooltip(write_button, "`w` を送信し、現在の定跡DBを book/backup/ に書き出します。")

        progress = ttk.Frame(self)
        progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress.columnconfigure(1, weight=1)
        self._add_progress_row(progress, 0, "read")
        self._add_progress_row(progress, 1, "write")
        self._add_progress_row(progress, 2, "task")
        ttk.Label(progress, textvariable=self.mining_status).grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(5, 0),
        )

        logs = ttk.PanedWindow(self, orient="vertical")
        logs.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self._add_log_pane(logs, "peta_next/peta_shockログ", "peta", 7)
        self._add_log_pane(logs, "タスク状況ログ", "task", 7)
        self._add_log_pane(logs, "探索ログ", "search", 10)
        self._add_log_pane(logs, "その他ログ", "other", 8)

        self._update_buttons()

    def _reset_progress(self) -> None:
        self.progress_labels["read"].set("定跡読込: 待機中")
        self.progress_labels["write"].set("定跡書込: 待機中")
        self.progress_labels["task"].set("enqueue進捗: 待機中")
        self.mining_status.set("現在の局面数 - 局面    現在の採掘速度 - pos/day")
        self.latest_mining_positions = None
        self.mining_samples.clear()
        for bar in self.progress_bars.values():
            bar.configure(maximum=1)
            bar["value"] = 0

    def _add_progress_row(self, parent: ttk.Frame, row: int, key: str) -> None:
        ttk.Label(parent, textvariable=self.progress_labels[key], width=28).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        bar = ttk.Progressbar(parent, mode="determinate", maximum=1, value=0)
        bar.grid(row=row, column=1, sticky="ew", pady=2)
        self.progress_bars[key] = bar

    def _add_log_pane(self, paned: ttk.PanedWindow, title: str, key: str, height: int) -> None:
        frame = ttk.Frame(paned, padding=(0, 0, 0, 4))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text=title).grid(row=0, column=0, sticky="w", pady=(0, 3))
        text = scrolledtext.ScrolledText(frame, wrap="word", height=height)
        text.grid(row=1, column=0, sticky="nsew")
        text.configure(state="disabled")
        self.log_widgets[key] = text
        paned.add(frame, weight=1)

    def start_process(self) -> None:
        if self.is_running():
            return
        if not BOOK_MINER_SCRIPT.is_file():
            messagebox.showerror("起動失敗", f"BookMiner.py が見つかりません: {BOOK_MINER_SCRIPT}")
            return

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(BOOK_MINER_SCRIPT), "--from_gui"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=BASE_DIR,
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
        self._append_log("other", f"$ {sys.executable} {BOOK_MINER_SCRIPT.name} --from_gui\n")
        threading.Thread(target=self._read_output, daemon=True).start()
        self._update_buttons()

    def start_kif_manager(self) -> None:
        if not KIF_MANAGER_SCRIPT.is_file():
            messagebox.showerror("起動失敗", f"KifManager が見つかりません: {KIF_MANAGER_SCRIPT}")
            return
        try:
            subprocess.Popen(
                [sys.executable, str(KIF_MANAGER_SCRIPT), "--from_bookminer"],
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
            self._append_log("other", f"\n[GUI] BookMiner.py exited. return code = {return_code}\n")
        self.process = None
        self._update_buttons()

    def _handle_output(self, text: str) -> None:
        self.output_buffer += text
        while "\n" in self.output_buffer:
            line, self.output_buffer = self.output_buffer.split("\n", 1)
            line = line + "\n"
            self._handle_progress_line(line)
            self._append_log(self._classify_log_line(line), line)

        if self.output_buffer.endswith("> "):
            self._append_log("other", self.output_buffer)
            self.output_buffer = ""

    def _flush_output_buffer(self) -> None:
        if self.output_buffer:
            self._handle_progress_line(self.output_buffer)
            self._append_log(self._classify_log_line(self.output_buffer), self.output_buffer)
            self.output_buffer = ""

    def _handle_progress_line(self, line: str) -> None:
        self._handle_book_progress_line(line)
        self._handle_task_queue_progress_line(line)
        self._handle_mining_progress_line(line)

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

    def _handle_task_queue_progress_line(self, line: str) -> None:
        match = TASK_QUEUE_PROGRESS_RE.search(line)
        if match is None:
            return

        phase, count_text, total_text = match.groups()
        count = int(count_text)
        total = None if total_text == "?" else int(total_text)

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
            self.mining_status.set("現在の局面数 - 局面    現在の採掘速度 - pos/day")
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
            f"現在の局面数 {positions:,} 局面    現在の採掘速度 {speed_text} pos/day"
        )

    def _classify_log_line(self, line: str) -> str:
        lower = line.lower()
        if (
            "[taskqueue" in lower
            or "[taskworker" in lower
            or "[miningprogress]" in lower
            or "put position commands" in lower
            or re.search(r"\(\d+\) read \d+ position commands", line)
        ):
            return "task"
        if (
            "peta_shock" in lower
            or "p command" in lower
            or "peta shocked book" in lower
            or "peta_next" in lower
            or "root sfen" in lower
            or "think_sfens" in lower
            or "write book path" in lower
        ):
            return "peta"
        if (
            "reached max_book_ply" in lower
            or "過去10分" in line
            or "all tasks completed" in lower
            or re.search(r"\[\d+\]\s+.+\s,\s*[0-9.]+", line)
        ):
            return "search"
        return "other"

    def send_command(self, command: str) -> bool:
        if not self.is_running() or self.process is None or self.process.stdin is None:
            messagebox.showinfo("未起動", "BookMiner.py を起動してください。")
            return False
        self._append_log("other", f"\n[GUI] > {command}\n")
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            messagebox.showerror("送信失敗", str(exc))
            return False
        return True

    def send_think(self) -> None:
        value = self.eval_limit.get().strip()
        if not value:
            messagebox.showerror("入力エラー", "eval_limit を指定してください。")
            return
        if self.send_command(f"e {value}"):
            self.send_command("t")

    def send_peta_next(self) -> None:
        eval_diff = self.eval_diff.get().strip()
        if not eval_diff:
            messagebox.showerror("入力エラー", "eval diff を指定してください。")
            return
        max_step = self.max_step.get().strip()
        self.send_command(f"n {eval_diff}" if not max_step else f"n {eval_diff} {max_step}")

    def _append_log(self, key: str, text: str) -> None:
        log = self.log_widgets.get(key) or self.log_widgets["other"]
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
        self.start_button.configure(state="disabled" if running else "normal")
        self.quit_button.configure(state="normal" if running else "disabled")


def main() -> int:
    root = tk.Tk()
    root.title("BookMiner GUI")
    root.geometry("980x720")
    root.minsize(760, 520)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    gui = BookMinerGui(root)

    def on_close() -> None:
        if gui.is_running():
            gui.process.terminate()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
