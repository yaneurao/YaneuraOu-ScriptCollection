#!/usr/bin/env python3
"""Record GUI analysis positions into BookMiner's think_sfens.txt.

This program is registered in a shogi GUI as if it were a USI engine.
It starts the real engine, passes every command and response through, and
records incoming "position ..." commands in a BookMiner-compatible format.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
from pathlib import Path


def default_output_path() -> Path:
    return Path.cwd() / "think_sfens.txt"


def normalize_position_command(line: str) -> str | None:
    command = line.strip()
    if not command.startswith("position "):
        return None

    command = command[len("position ") :].strip()
    if not command:
        return None

    return " ".join(command.split())


def dedupe_key(line: str) -> str:
    command, _sep, _metadata = line.partition(",")
    return " ".join(command.strip().split())


class ThinkSfensRecoder:
    def __init__(self, output_path: Path, dedupe: bool) -> None:
        self.output_path = output_path
        self.dedupe = dedupe
        self.seen: set[str] = set()
        self.lock = threading.Lock()
        self.out = None

    def open(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.dedupe and self.output_path.exists():
            with self.output_path.open("r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key = dedupe_key(line)
                    if key:
                        self.seen.add(key)

        self.out = self.output_path.open("a", encoding="utf-8", newline="\n", buffering=1)

    def close(self) -> None:
        if self.out is not None:
            self.out.close()
            self.out = None

    def record_if_position(self, line: str) -> None:
        position_command = normalize_position_command(line)
        if position_command is None:
            return

        key = dedupe_key(position_command)
        with self.lock:
            if self.dedupe and key in self.seen:
                return
            if self.dedupe:
                self.seen.add(key)
            assert self.out is not None
            self.out.write(position_command + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record shogi GUI analysis positions to BookMiner think_sfens.txt while proxying a real USI engine.",
    )
    parser.add_argument(
        "--engine-path",
        required=True,
        help="path to the real USI engine executable",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=default_output_path(),
        help="output think_sfens.txt path; default is ./think_sfens.txt in the current working directory",
    )
    parser.add_argument(
        "--engine-cwd",
        type=Path,
        default=None,
        help="working directory for the real engine; default is the engine executable directory",
    )
    parser.add_argument(
        "--engine-name",
        default=None,
        help="engine name reported to the GUI; replaces the real engine's 'id name ...' line",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="record duplicate positions too",
    )
    parser.add_argument(
        "engine_args",
        nargs=argparse.REMAINDER,
        help="arguments passed to the real engine; put them after --",
    )
    return parser


def engine_command(engine_path: str, engine_args: list[str]) -> list[str]:
    args = list(engine_args)
    if args and args[0] == "--":
        args = args[1:]
    return [engine_path, *args]


def rewrite_engine_name(line: str, engine_name: str | None) -> str:
    if engine_name is None:
        return line

    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    if body.startswith("id name "):
        return f"id name {engine_name}{newline}"
    return line


def forward_stream(src, dst, engine_name: str | None = None) -> None:
    try:
        for line in src:
            line = rewrite_engine_name(line, engine_name)
            dst.write(line)
            dst.flush()
    except Exception as exc:
        print(f"[ThinkSfensRecoder] stream forwarding stopped: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    engine_path = Path(args.engine_path).expanduser()
    if not engine_path.is_file():
        print(f"[ThinkSfensRecoder] engine not found: {engine_path}", file=sys.stderr)
        return 1

    cwd = args.engine_cwd
    if cwd is None:
        cwd = engine_path.resolve().parent

    recoder = ThinkSfensRecoder(args.output.expanduser(), dedupe=not args.no_dedupe)
    recoder.open()

    process = subprocess.Popen(
        engine_command(str(engine_path), args.engine_args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    stdout_thread = threading.Thread(target=forward_stream, args=(process.stdout, sys.stdout, args.engine_name), daemon=True)
    stderr_thread = threading.Thread(target=forward_stream, args=(process.stderr, sys.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        for raw_line in sys.stdin:
            recoder.record_if_position(raw_line)
            process.stdin.write(raw_line)
            process.stdin.flush()
            if raw_line.strip() == "quit":
                break
    except BrokenPipeError:
        pass
    finally:
        recoder.close()
        try:
            process.stdin.close()
        except Exception:
            pass

    try:
        return process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
