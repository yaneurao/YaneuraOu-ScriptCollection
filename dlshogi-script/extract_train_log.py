"""dlshogi の train-*.log から学習結果を表形式で抽出する。

使い方:

    cd C:\shogi\learner
    python extract_train_log.py C:\shogi\model\exp___i20x256

標準では、指定したフォルダ内の train-*.log をすべて読む。
さらに、同じ親フォルダに exp___i20x256_round2,
exp___i20x256_round3, ... のようなフォルダがあれば自動で読む。

カンマ区切りのCSVを標準出力に書き、同じ内容をファイルにも保存する。
保存先を指定しなければ、カレントディレクトリの exp___i20x256.csv に保存する。
保存先を変えたい場合:

    python extract_train_log.py C:\shogi\model\exp___i20x256 ^
      --output C:\shogi\model\exp___i20x256\summary.csv

注意:

    この学習スクリプトは教師1ファイルごとに train.py を呼ぶ。
    train.py は --model が指定された呼び出しでだけ SWA test accuracy を
    ログに出すため、SWA の行が無い epoch は nan になる。
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


INFO_PREFIX_RE = re.compile(r"^[^\t]*\tINFO\t(.*)$")
LOG_INDEX_RE = re.compile(r"train-(\d+)\.log$")
ROUND_DIR_RE = re.compile(r"^(?P<base>.+)_round(?P<round>\d+)$")

FLOAT = r"[-+]?[\d.]+(?:[eE][-+]?\d+)?|nan|inf|-inf"
FOUR_FLOATS = rf"({FLOAT}), ({FLOAT}), ({FLOAT}), ({FLOAT})"
TWO_FLOATS = rf"({FLOAT}), ({FLOAT})"

TRAIN_SUMMARY_RE = re.compile(
    rf"epoch = (\d+), steps = (\d+), "
    rf"train loss avr = {FOUR_FLOATS}, "
    rf"test loss = {FOUR_FLOATS}, "
    rf"test accuracy = {TWO_FLOATS}, "
    rf"test entropy = {TWO_FLOATS}"
)

SWA_SUMMARY_RE = re.compile(
    rf"epoch = (\d+), steps = (\d+), "
    rf"swa test loss = {FOUR_FLOATS}, "
    rf"swa test accuracy = {TWO_FLOATS}, "
    rf"swa test entropy = {TWO_FLOATS}"
)


@dataclass
class LogRow:
    source: str
    epoch: int | None = None
    steps: int | None = None
    teacher: str = ""
    position_num: int | None = None
    lr: str = "nan"
    val_lambda: str = "nan"
    batchsize: str = "nan"
    train_loss: list[str] = field(default_factory=lambda: ["nan"] * 4)
    test_loss: list[str] = field(default_factory=lambda: ["nan"] * 4)
    test_accuracy: list[str] = field(default_factory=lambda: ["nan"] * 2)
    test_entropy: list[str] = field(default_factory=lambda: ["nan"] * 2)
    swa_test_accuracy: list[str] = field(default_factory=lambda: ["nan"] * 2)


def info_message(line: str) -> str:
    match = INFO_PREFIX_RE.match(line.rstrip("\n"))
    return match.group(1) if match else line.strip()


def format_float(value: str) -> str:
    lower = value.lower()
    if lower in {"nan", "inf", "-inf"}:
        return lower
    number = float(value)
    if not math.isfinite(number):
        return str(number).lower()
    return f"{number:.6f}"


def relative_teacher(path_text: str, teacher_root: Path | None) -> str:
    path = Path(path_text)
    if teacher_root is None:
        return path_text
    try:
        return str(path.relative_to(teacher_root))
    except ValueError:
        return path_text


def parse_log(path: Path, teacher_root: Path | None) -> list[LogRow]:
    rows: list[LogRow] = []
    rows_by_epoch: dict[int, LogRow] = {}
    state = LogRow(source=str(path))
    current_lr = "nan"

    fallback_epoch = LOG_INDEX_RE.fullmatch(path.name)
    if fallback_epoch:
        state.epoch = int(fallback_epoch.group(1))

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        message = info_message(raw_line)

        if message.startswith("batchsize="):
            state.batchsize = message.split("=", 1)[1]
            continue

        if message.startswith("val_lambda="):
            state.val_lambda = message.split("=", 1)[1]
            continue

        if message.startswith("lr_scheduler lr="):
            current_lr = message.split("=", 1)[1]
            if state.lr == "nan":
                state.lr = current_lr
            continue

        if message.startswith("lr=") and current_lr == "nan":
            current_lr = message.split("=", 1)[1]
            if state.lr == "nan":
                state.lr = current_lr
            continue

        if message.startswith("train position num ="):
            state.position_num = int(message.rsplit("=", 1)[1])
            continue

        if (
            (message.endswith(".hcpe") or message.endswith(".hcpe3"))
            and "teacher" in message.lower()
        ):
            state.teacher = relative_teacher(message, teacher_root)
            state.position_num = None
            continue

        match = TRAIN_SUMMARY_RE.fullmatch(message)
        if match:
            epoch = int(match.group(1))
            row = LogRow(
                source=str(path),
                epoch=epoch,
                steps=int(match.group(2)),
                teacher=state.teacher,
                position_num=state.position_num,
                lr=current_lr,
                val_lambda=state.val_lambda,
                batchsize=state.batchsize,
            )
            values = [format_float(value) for value in match.groups()[2:]]
            row.train_loss = values[0:4]
            row.test_loss = values[4:8]
            row.test_accuracy = values[8:10]
            row.test_entropy = values[10:12]
            rows.append(row)
            rows_by_epoch[epoch] = row
            continue

        match = SWA_SUMMARY_RE.fullmatch(message)
        if match:
            epoch = int(match.group(1))
            row = rows_by_epoch.get(epoch)
            if row is None:
                row = LogRow(
                    source=str(path),
                    epoch=epoch,
                    steps=int(match.group(2)),
                    teacher=state.teacher,
                    position_num=state.position_num,
                    lr=current_lr,
                    val_lambda=state.val_lambda,
                    batchsize=state.batchsize,
                )
                rows.append(row)
                rows_by_epoch[epoch] = row
            values = [format_float(value) for value in match.groups()[2:]]
            row.swa_test_accuracy = values[4:6]
            continue

    return rows


def log_index(path: Path) -> int:
    match = LOG_INDEX_RE.fullmatch(path.name)
    return int(match.group(1)) if match else 0


def split_round_dir_name(name: str) -> tuple[str, int]:
    match = ROUND_DIR_RE.fullmatch(name)
    if match:
        return match.group("base"), int(match.group("round"))
    return name, 1


def round_directories(path: Path) -> list[Path]:
    base_name, _ = split_round_dir_name(path.name)
    candidates: list[tuple[int, Path]] = []

    base_dir = path.with_name(base_name)
    if base_dir.is_dir():
        candidates.append((1, base_dir))

    try:
        round_children = list(path.parent.glob(f"{base_name}_round*"))
    except OSError:
        round_children = []

    for child in round_children:
        if not child.is_dir():
            continue
        child_base_name, round_num = split_round_dir_name(child.name)
        if child_base_name == base_name and round_num >= 2:
            candidates.append((round_num, child))

    return [directory for _, directory in sorted(candidates, key=lambda item: item[0])]


def iter_log_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            for directory in round_directories(path):
                for log_file in sorted(directory.glob("train-*.log"), key=log_index):
                    resolved = log_file.resolve()
                    if resolved not in seen:
                        files.append(log_file)
                        seen.add(resolved)
        else:
            resolved = path.resolve()
            if resolved not in seen:
                files.append(path)
                seen.add(resolved)
    return files


def default_output_path(paths: list[Path]) -> Path:
    first_path = paths[0]
    if first_path.is_dir():
        model_name = first_path.name
    elif LOG_INDEX_RE.fullmatch(first_path.name):
        model_name = first_path.parent.name
    else:
        model_name = first_path.stem

    model_name, _ = split_round_dir_name(model_name)
    return Path.cwd() / f"{model_name}.csv"


def row_to_dict(row: LogRow) -> dict[str, str | int | None]:
    return {
        "epoch": row.epoch,
        "swa_test_accuracy": row.swa_test_accuracy[0],
        "swa_test_value_accuracy": row.swa_test_accuracy[1],
        "test_accuracy": row.test_accuracy[0],
        "test_value_accuracy": row.test_accuracy[1],
        "train_loss_policy": row.train_loss[0],
        "train_loss_result": row.train_loss[1],
        "train_loss_value": row.train_loss[2],
        "train_loss_total": row.train_loss[3],
        "test_loss_policy": row.test_loss[0],
        "test_loss_result": row.test_loss[1],
        "test_loss_value": row.test_loss[2],
        "test_loss_total": row.test_loss[3],
        "test_entropy_policy": row.test_entropy[0],
        "test_entropy_value": row.test_entropy[1],
        "position_num": row.position_num,
        "lr": format_float(row.lr),
        "val_lambda": row.val_lambda,
        "batchsize": row.batchsize,
        "teacher": row.teacher,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract dlshogi train log metrics.")
    parser.add_argument("paths", type=Path, nargs="+", help="Log file(s) or directory.")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="CSV output path. Defaults to .\\<model-name>.csv.",
    )
    parser.add_argument(
        "--teacher_root",
        type=Path,
        default=Path(r"C:\shogi\teacher"),
        help="Teacher root stripped from teacher paths.",
    )
    args = parser.parse_args()

    log_files = iter_log_files(args.paths)
    if not log_files:
        raise FileNotFoundError("No log files found.")

    parsed_rows: list[LogRow] = []
    for path in log_files:
        parsed_rows.extend(parse_log(path, args.teacher_root.resolve()))
    parsed_rows.sort(key=lambda row: (row.epoch is None, row.epoch or 0, row.source))

    rows = [row_to_dict(row) for row in parsed_rows]
    if not rows:
        raise ValueError("No epoch summary rows found.")
    fieldnames = list(rows[0].keys())
    delimiter = ","

    output_path = args.output.resolve() if args.output else default_output_path(args.paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        file_writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delimiter)
        file_writer.writeheader()
        file_writer.writerows(rows)

    stdout_writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, delimiter=delimiter)
    stdout_writer.writeheader()
    stdout_writer.writerows(rows)
    print(f"wrote: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
