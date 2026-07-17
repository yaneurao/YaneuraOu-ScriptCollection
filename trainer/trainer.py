"""dlshogi training wrapper.

使い方とWindows環境構築手順は readme.md を参照。
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DLSHOGI_DIR = Path(r"C:\shogi\DeepLearningShogi")
DEFAULT_TRAIN_DIR = Path(r"C:\shogi\teacher\yane-distill")
DEFAULT_TEST_DATA = Path(
    r"C:\shogi\teacher\test\test20231010_fg2021_dls5_ryfc20_ev8250k825.hcpe"
)
DEFAULT_MODEL_ROOT = Path(r"C:\shogi\model")
CHECKPOINT_RE = re.compile(r"checkpoint-(\d+)\.(?:pth|ckpt)$")
ROUND_SUFFIX_RE = re.compile(r"_round(\d+)$")
INFO_PREFIX_RE = re.compile(r"^[^\t]*\tINFO\t(.*)$")
TRAIN_LOG_INDEX_RE = re.compile(r"train-(\d+)\.log$")
TRAIN_LOG_ROUND_DIR_RE = re.compile(r"^(?P<base>.+)_round(?P<round>\d+)$")

FLOAT_RE = r"[-+]?[\d.]+(?:[eE][-+]?\d+)?|nan|inf|-inf"
FOUR_FLOATS_RE = rf"({FLOAT_RE}), ({FLOAT_RE}), ({FLOAT_RE}), ({FLOAT_RE})"
TWO_FLOATS_RE = rf"({FLOAT_RE}), ({FLOAT_RE})"

TRAIN_SUMMARY_RE = re.compile(
    rf"epoch = (\d+), steps = (\d+), "
    rf"train loss avr = {FOUR_FLOATS_RE}, "
    rf"test loss = {FOUR_FLOATS_RE}, "
    rf"test accuracy = {TWO_FLOATS_RE}, "
    rf"test entropy = {TWO_FLOATS_RE}"
)

SWA_SUMMARY_RE = re.compile(
    rf"epoch = (\d+), steps = (\d+), "
    rf"swa test loss = {FOUR_FLOATS_RE}, "
    rf"swa test accuracy = {TWO_FLOATS_RE}, "
    rf"swa test entropy = {TWO_FLOATS_RE}"
)


@dataclass
class TrainLogRow:
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


def collect_teacher_files(train_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("*.hcpe", "*.hcpe3"):
        files.extend(train_dir.glob(pattern))
    return sorted(files)


def checkpoint_path(out_dir: Path, epoch: int, suffix: str) -> Path:
    return out_dir / f"checkpoint-{epoch:04}{suffix}"


def checkpoint_number(path: Path) -> int:
    match = CHECKPOINT_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"Cannot infer checkpoint number from: {path}")
    return int(match.group(1))


def make_out_dir(model_root: Path, network: str) -> Path:
    return model_root / network


def next_round_out_dir(resume_checkpoint: Path) -> Path:
    resume_dir = resume_checkpoint.resolve().parent
    match = ROUND_SUFFIX_RE.search(resume_dir.name)
    if match:
        base_name = resume_dir.name[: match.start()]
        next_round = int(match.group(1)) + 1
    else:
        base_name = resume_dir.name
        next_round = 2
    return resume_dir.with_name(f"{base_name}_round{next_round}")


def round_directories(base_dir: Path) -> list[tuple[int, Path]]:
    directories: list[tuple[int, Path]] = []
    if base_dir.is_dir():
        directories.append((1, base_dir))

    try:
        round_children = list(base_dir.parent.glob(f"{base_dir.name}_round*"))
    except OSError:
        round_children = []

    for child in round_children:
        if not child.is_dir():
            continue
        match = ROUND_SUFFIX_RE.search(child.name)
        if not match:
            continue
        if child.name[: match.start()] != base_dir.name:
            continue
        directories.append((int(match.group(1)), child))

    return sorted(directories, key=lambda item: item[0])


def latest_checkpoint(directory: Path, suffix: str) -> Path | None:
    checkpoints: list[tuple[int, Path]] = []
    for path in directory.glob(f"checkpoint-*{suffix}"):
        try:
            checkpoints.append((checkpoint_number(path), path))
        except ValueError:
            continue
    if not checkpoints:
        return None
    return max(checkpoints, key=lambda item: item[0])[1]


def latest_round_checkpoint(base_dir: Path, suffix: str) -> tuple[int, int, Path, Path] | None:
    states: list[tuple[int, int, Path, Path]] = []
    for round_number, directory in round_directories(base_dir):
        checkpoint = latest_checkpoint(directory, suffix)
        if checkpoint is None:
            continue
        states.append((checkpoint_number(checkpoint), round_number, directory, checkpoint))
    if not states:
        return None
    return max(states, key=lambda item: item[0])


def latest_round_checkpoint_before(
    base_dir: Path, before_round: int, suffix: str
) -> tuple[int, int, Path, Path] | None:
    states: list[tuple[int, int, Path, Path]] = []
    for round_number, directory in round_directories(base_dir):
        if round_number >= before_round:
            continue
        checkpoint = latest_checkpoint(directory, suffix)
        if checkpoint is None:
            continue
        states.append((checkpoint_number(checkpoint), round_number, directory, checkpoint))
    if not states:
        return None
    return max(states, key=lambda item: item[0])


def checkpoint_numbers_in_directory(directory: Path, suffix: str) -> list[int]:
    numbers: list[int] = []
    for path in directory.glob(f"checkpoint-*{suffix}"):
        try:
            numbers.append(checkpoint_number(path))
        except ValueError:
            continue
    return sorted(numbers)


def checkpoint_offset_from_round_dir(directory: Path, suffix: str) -> int:
    numbers = checkpoint_numbers_in_directory(directory, suffix)
    return numbers[0] - 1 if numbers else 0


def round_has_exported_model(directory: Path) -> bool:
    return any(directory.glob("model-*"))


def auto_round_state(
    model_root: Path, network: str, total_epochs: int, checkpoint_suffix: str
) -> tuple[Path, Path | None, int, bool, bool]:
    base_dir = make_out_dir(model_root, network)
    round_dirs = round_directories(base_dir)
    if not round_dirs:
        return base_dir, None, 0, False, False

    latest_round_number, latest_dir = round_dirs[-1]
    latest_path = latest_checkpoint(latest_dir, checkpoint_suffix)

    if latest_path is None:
        previous_state = latest_round_checkpoint_before(
            base_dir, latest_round_number, checkpoint_suffix
        )
        if previous_state is None:
            return latest_dir, None, 0, False, False
        previous_number, _, _, previous_path = previous_state
        return latest_dir, previous_path, previous_number, True, True

    latest_number = checkpoint_number(latest_path)
    round_checkpoint_count = len(
        checkpoint_numbers_in_directory(latest_dir, checkpoint_suffix)
    )
    if round_has_exported_model(latest_dir) or round_checkpoint_count >= total_epochs:
        return next_round_out_dir(latest_path), latest_path, latest_number, True, True

    checkpoint_offset = checkpoint_offset_from_round_dir(latest_dir, checkpoint_suffix)
    return latest_dir, None, checkpoint_offset, False, False


def train_log_info_message(line: str) -> str:
    match = INFO_PREFIX_RE.match(line.rstrip("\n"))
    return match.group(1) if match else line.strip()


def format_log_float(value: str) -> str:
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


def parse_train_log(path: Path, teacher_root: Path | None) -> list[TrainLogRow]:
    rows: list[TrainLogRow] = []
    rows_by_epoch: dict[int, TrainLogRow] = {}
    state = TrainLogRow(source=str(path))
    current_lr = "nan"

    fallback_epoch = TRAIN_LOG_INDEX_RE.fullmatch(path.name)
    if fallback_epoch:
        state.epoch = int(fallback_epoch.group(1))

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        message = train_log_info_message(raw_line)

        if message.startswith("batchsize="):
            state.batchsize = message.split("=", 1)[1]
            continue

        if message.startswith("val_lambda="):
            state.val_lambda = message.split("=", 1)[1]
            continue

        if message.startswith("lr_scheduler lr="):
            current_lr = message.split("=", 1)[1]
            continue

        if message.startswith("lr="):
            current_lr = message.split("=", 1)[1]
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
            row = TrainLogRow(
                source=str(path),
                epoch=epoch,
                steps=int(match.group(2)),
                teacher=state.teacher,
                position_num=state.position_num,
                lr=current_lr,
                val_lambda=state.val_lambda,
                batchsize=state.batchsize,
            )
            values = [format_log_float(value) for value in match.groups()[2:]]
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
                row = TrainLogRow(
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
            values = [format_log_float(value) for value in match.groups()[2:]]
            row.swa_test_accuracy = values[4:6]
            continue

    return rows


def train_log_index(path: Path) -> int:
    match = TRAIN_LOG_INDEX_RE.fullmatch(path.name)
    return int(match.group(1)) if match else 0


def split_train_log_round_dir_name(name: str) -> tuple[str, int]:
    match = TRAIN_LOG_ROUND_DIR_RE.fullmatch(name)
    if match:
        return match.group("base"), int(match.group("round"))
    return name, 1


def train_log_round_directories(path: Path) -> list[Path]:
    base_name, _ = split_train_log_round_dir_name(path.name)
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
        child_base_name, round_num = split_train_log_round_dir_name(child.name)
        if child_base_name == base_name and round_num >= 2:
            candidates.append((round_num, child))

    return [directory for _, directory in sorted(candidates, key=lambda item: item[0])]


def iter_train_log_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            for directory in train_log_round_directories(path):
                for log_file in sorted(directory.glob("train-*.log"), key=train_log_index):
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


def default_train_log_output_path(paths: list[Path]) -> Path:
    first_path = paths[0]
    if first_path.is_dir():
        model_name = first_path.name
    elif TRAIN_LOG_INDEX_RE.fullmatch(first_path.name):
        parent = first_path.resolve().parent
        if parent == Path.cwd().resolve():
            model_name = first_path.stem
        else:
            model_name = first_path.parent.name or first_path.stem
    else:
        model_name = first_path.stem

    model_name, _ = split_train_log_round_dir_name(model_name)
    return Path.cwd() / f"{model_name}.csv"


def latest_train_log_file(log_files: list[Path]) -> Path:
    return max(log_files, key=lambda path: (path.stat().st_mtime, train_log_index(path)))


def tail_lines(path: Path, count: int) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def train_log_row_to_dict(row: TrainLogRow) -> dict[str, str | int | None]:
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
        "lr": format_log_float(row.lr),
        "val_lambda": row.val_lambda,
        "batchsize": row.batchsize,
        "teacher": row.teacher,
    }


def show_train_log(
    paths: list[Path],
    *,
    output_path: Path | None = None,
    teacher_root: Path | None = Path(r"C:\shogi\teacher"),
) -> Path:
    log_files = iter_train_log_files(paths)
    if not log_files:
        raise FileNotFoundError("No log files found.")

    parsed_rows: list[TrainLogRow] = []
    resolved_teacher_root = teacher_root.resolve() if teacher_root else None
    for path in log_files:
        parsed_rows.extend(parse_train_log(path, resolved_teacher_root))
    parsed_rows.sort(key=lambda row: (row.epoch is None, row.epoch or 0, row.source))

    rows = [train_log_row_to_dict(row) for row in parsed_rows]
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = list(train_log_row_to_dict(TrainLogRow(source="")).keys())
        print("No epoch summary rows found yet.", file=sys.stderr)

    output_path = output_path.resolve() if output_path else default_train_log_output_path(paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        file_writer = csv.DictWriter(f, fieldnames=fieldnames)
        file_writer.writeheader()
        file_writer.writerows(rows)

    stdout_writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    stdout_writer.writeheader()
    stdout_writer.writerows(rows)

    latest_log = latest_train_log_file(log_files)
    print()
    print(f"# latest log: {latest_log}")
    for line in tail_lines(latest_log, 3):
        print(f"# {line}")
    print(f"wrote: {output_path}", file=sys.stderr)
    return output_path


def lightning_precision(no_amp: bool, amp_dtype: str) -> str:
    if no_amp:
        return "32-true"
    if amp_dtype == "bfloat16":
        return "bf16-mixed"
    return "16-mixed"


def cosine_scheduler_period(total_epochs: int) -> int:
    # scheduler.step() runs after each teacher file, so file N uses t=N-1.
    return max(1, total_epochs - 1)


def cosine_scheduler_train_arg(total_epochs: int, lr_min: float) -> str:
    period = cosine_scheduler_period(total_epochs)
    return (
        "dlshogi.lr_scheduler.CosineLRScheduler("
        f"t_initial={period},lr_min={lr_min},cycle_limit=1)"
    )


def cosine_scheduler_config(total_epochs: int, lr_min: float) -> dict:
    return {
        "class_path": "dlshogi.lr_scheduler.CosineLRScheduler",
        "init_args": {
            "t_initial": cosine_scheduler_period(total_epochs),
            "lr_min": lr_min,
            "cycle_limit": 1,
        },
    }


def inductor_subprocess_env(
    args: argparse.Namespace, out_dir: Path, checkpoint_number_for_file: int
) -> dict[str, str] | None:
    if not args.use_compile or (args.compile_backend or "").lower() != "inductor":
        return None

    env = os.environ.copy()
    cache_root = env.get("DLSHOGI_INDUCTOR_CACHE_ROOT")
    if cache_root:
        cache_base = Path(cache_root)
    else:
        cache_base = out_dir / "_ti"
    cache_dir = cache_base / f"{checkpoint_number_for_file:x}{os.getpid():x}"
    inductor_cache_dir = cache_dir / "i"
    triton_cache_dir = cache_dir / "t"
    inductor_cache_dir.mkdir(parents=True, exist_ok=True)
    triton_cache_dir.mkdir(parents=True, exist_ok=True)
    env["TORCHINDUCTOR_CACHE_DIR"] = str(inductor_cache_dir)
    env["TRITON_CACHE_DIR"] = str(triton_cache_dir)
    env["TORCHINDUCTOR_COMPILE_THREADS"] = "1"
    env["DLSHOGI_PATCH_TRITON_CACHE"] = "1"
    # PyTorch 2.5 on Windows can hit FileExistsError in pad_mm's LocalCache
    # while benchmarking shape padding. Disabling this keeps inductor usable.
    env["TORCHINDUCTOR_SHAPE_PADDING"] = "0"
    print(f"torchinductor cache: {inductor_cache_dir}")
    print(f"triton cache: {triton_cache_dir}")
    print("torchinductor compile threads: 1")
    print("torchinductor shape padding: disabled")
    print("triton cache parent-dir patch: enabled")
    return env


TRITON_CACHE_PATCH_RUNNER = r"""
import os
import runpy
import sys
import uuid


def patch_triton_cache_parent_dirs():
    if os.environ.get("DLSHOGI_PATCH_TRITON_CACHE") != "1":
        return
    try:
        import triton.runtime.cache as triton_cache
    except Exception:
        return

    file_cache_manager = getattr(triton_cache, "FileCacheManager", None)
    if file_cache_manager is None:
        return
    if getattr(file_cache_manager, "_dlshogi_parent_dir_patch", False):
        return

    def put_with_parent_dirs(self, data, filename, binary=True):
        if not self.cache_dir:
            raise RuntimeError("Could not create or locate cache dir")

        binary = isinstance(data, bytes)
        if not binary:
            data = str(data)

        assert self.lock_path is not None
        filepath = self._make_path(filename)
        parent = os.path.dirname(filepath)
        if parent:
            os.makedirs(parent, exist_ok=True)

        if os.name == "nt":
            mode = "wb" if binary else "w"
            with open(filepath, mode) as f:
                f.write(data)
            return filepath

        temp_path = f"{filepath}.tmp.pid_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        temp_parent = os.path.dirname(temp_path)
        if temp_parent:
            os.makedirs(temp_parent, exist_ok=True)

        mode = "wb" if binary else "w"
        with open(temp_path, mode) as f:
            f.write(data)

        try:
            os.replace(temp_path, filepath)
        except PermissionError:
            if os.name == "nt":
                os.remove(temp_path)
            else:
                raise
        return filepath

    file_cache_manager.put = put_with_parent_dirs
    file_cache_manager._dlshogi_parent_dir_patch = True


patch_triton_cache_parent_dirs()
module_name = sys.argv[1]
sys.argv = [module_name, *sys.argv[2:]]
runpy.run_module(module_name, run_name="__main__")
"""


def python_module_command(
    module_name: str, module_args: list[str], env: dict[str, str] | None
) -> list[str]:
    if env and env.get("DLSHOGI_PATCH_TRITON_CACHE") == "1":
        return [sys.executable, "-c", TRITON_CACHE_PATCH_RUNNER, module_name, *module_args]
    return [sys.executable, "-m", module_name, *module_args]


def run_command_with_log(
    command: list[str],
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            log_file.write(line)
        return_code = process.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, command)


def ptl_model_checkpoint_from_lightning(source: Path, target: Path) -> Path:
    import torch

    checkpoint = torch.load(source, map_location="cpu")
    state_dict = checkpoint.get("state_dict")
    if not state_dict:
        raise ValueError(f"Lightning checkpoint has no state_dict: {source}")

    model_state = {
        key.removeprefix("model."): value
        for key, value in state_dict.items()
        if key.startswith("model.")
    }
    if not model_state:
        raise ValueError(f"Lightning checkpoint has no model.* entries: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model_state}, target)
    return target


def write_ptl_config(
    path: Path,
    *,
    out_dir: Path,
    tmp_checkpoint_name: str,
    teacher_file: Path,
    test_data: Path,
    max_epochs: int,
    total_epochs: int,
    checkpoint_number_for_file: int,
    args: argparse.Namespace,
    model_resume_checkpoint: Path | None,
    export_model: bool,
) -> None:
    import yaml

    ema_decay = args.swa_n_avr / (args.swa_n_avr + 1)
    config = {
        "seed_everything": 0,
        "trainer": {
            "accelerator": "gpu" if args.gpu >= 0 else "cpu",
            "devices": [args.gpu] if args.gpu >= 0 else 1,
            "max_epochs": max_epochs,
            "precision": lightning_precision(args.no_amp, args.amp_dtype),
            "gradient_clip_val": 10.0,
            "val_check_interval": 1.0,
            "num_sanity_val_steps": 0,
            "log_every_n_steps": 50,
            "default_root_dir": str(out_dir),
            "logger": {
                "class_path": "lightning.pytorch.loggers.CSVLogger",
                "init_args": {
                    "save_dir": str(out_dir),
                    "name": "metrics",
                    "version": f"train-{checkpoint_number_for_file:04}",
                },
            },
            "callbacks": [
                {
                    "class_path": "lightning.pytorch.callbacks.ModelCheckpoint",
                    "init_args": {
                        "dirpath": str(out_dir),
                        "filename": tmp_checkpoint_name,
                        "save_top_k": -1,
                        "save_on_train_epoch_end": True,
                        "auto_insert_metric_name": False,
                        "save_last": False,
                    },
                },
                {
                    "class_path": "lightning.pytorch.callbacks.LearningRateMonitor",
                    "init_args": {
                        "logging_interval": "epoch",
                    },
                },
            ],
        },
        "model": {
            "network": args.network,
            "val_lambda": args.val_lambda,
            "use_ema": args.use_swa,
            "update_bn": args.use_swa and export_model,
            "ema_start_epoch": args.swa_start_epoch,
            "ema_freq": args.swa_freq,
            "ema_decay": ema_decay,
            "lr_scheduler_interval": "epoch",
            "model_filename": str(out_dir / "model-{epoch:04d}") if export_model else None,
            "resume_model": str(model_resume_checkpoint) if model_resume_checkpoint else None,
            "use_compile": args.use_compile,
            "compile_backend": args.compile_backend,
            "compile_mode": args.compile_mode,
            "compile_fullgraph": args.compile_fullgraph,
            "compile_dynamic": args.compile_dynamic,
        },
        "data": {
            "train_files": [str(teacher_file)],
            "val_files": [str(test_data)],
            "batch_size": args.batchsize,
            "val_batch_size": args.batchsize,
            "use_average": not args.no_average,
            "use_evalfix": False,
            "temperature": 1.0,
            "cache": None,
        },
        "optimizer": {
            "class_path": "torch.optim.SGD",
            "init_args": {
                "lr": args.lr,
                "momentum": 0.9,
                "nesterov": True,
                "weight_decay": 0.0001,
            },
        },
        "lr_scheduler": cosine_scheduler_config(total_epochs, args.lr_min),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)


def finalize_ptl_checkpoint(tmp_checkpoint: Path, target_checkpoint: Path) -> None:
    if tmp_checkpoint.exists():
        tmp_checkpoint.replace(target_checkpoint)
        return

    candidates = [
        path
        for path in target_checkpoint.parent.glob("*.ckpt")
        if path.resolve() != target_checkpoint.resolve()
    ]
    if not candidates:
        raise FileNotFoundError(f"No Lightning checkpoint was written for {target_checkpoint}")
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    latest.replace(target_checkpoint)


def run_one_round(
    args: argparse.Namespace,
    *,
    dlshogi_dir: Path,
    test_data: Path,
    model_root: Path,
    teacher_files: list[Path],
    total_epochs: int,
    checkpoint_suffix: str,
    use_explicit_state: bool,
) -> None:
    auto_reset_optimizer = False
    auto_reset_scheduler = False
    resume_checkpoint: Path | None = (
        args.resume_checkpoint.resolve()
        if use_explicit_state and args.resume_checkpoint
        else None
    )
    explicit_out_dir = (
        args.out_dir.resolve() if use_explicit_state and args.out_dir else None
    )

    if explicit_out_dir:
        out_dir = explicit_out_dir
        checkpoint_offset = checkpoint_number(resume_checkpoint) if resume_checkpoint else 0
    elif resume_checkpoint:
        out_dir = next_round_out_dir(resume_checkpoint)
        checkpoint_offset = checkpoint_number(resume_checkpoint)
    else:
        (
            out_dir,
            resume_checkpoint,
            checkpoint_offset,
            auto_reset_optimizer,
            auto_reset_scheduler,
        ) = auto_round_state(model_root, args.network, total_epochs, checkpoint_suffix)
        if args.backend == "ptl" and resume_checkpoint is None and checkpoint_offset == 0:
            base_dir = make_out_dir(model_root, args.network)
            legacy_state = latest_round_checkpoint(base_dir, ".pth")
            if legacy_state is not None:
                legacy_number, _, legacy_dir, legacy_checkpoint = legacy_state
                legacy_checkpoint_count = len(
                    checkpoint_numbers_in_directory(legacy_dir, ".pth")
                )
                if not (
                    round_has_exported_model(legacy_dir)
                    or legacy_checkpoint_count >= total_epochs
                ):
                    raise RuntimeError(
                        "PTL backend found legacy train.py checkpoints, but the "
                        f"latest one is incomplete: {legacy_checkpoint}. "
                        "Finish the train.py round first, or pass --resume_checkpoint explicitly."
                    )
                out_dir = next_round_out_dir(legacy_checkpoint)
                resume_checkpoint = legacy_checkpoint
                checkpoint_offset = legacy_number
                auto_reset_optimizer = True
                auto_reset_scheduler = True

    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

    lr_scheduler = cosine_scheduler_train_arg(total_epochs, args.lr_min)

    print(f"DeepLearningShogi: {dlshogi_dir}")
    print(f"teacher files: {len(teacher_files)}")
    print(f"test data: {test_data}")
    print(f"out dir: {out_dir}")
    print(f"network: {args.network}")
    print(f"lr scheduler: {lr_scheduler}")
    if args.use_compile:
        compile_options = []
        if args.compile_backend:
            compile_options.append(f"backend={args.compile_backend}")
        if args.compile_mode:
            compile_options.append(f"mode={args.compile_mode}")
        if args.compile_fullgraph:
            compile_options.append("fullgraph=True")
        if args.compile_dynamic:
            compile_options.append("dynamic=True")
        suffix = f" ({', '.join(compile_options)})" if compile_options else ""
        print(f"torch.compile: enabled{suffix}")
    if resume_checkpoint:
        print(f"initial checkpoint: {resume_checkpoint}")
        print(f"checkpoint offset: {checkpoint_offset}")

    for file_index, teacher_file in enumerate(teacher_files, start=1):
        if file_index < args.start_index:
            continue

        checkpoint_number_for_file = checkpoint_offset + file_index
        current_checkpoint = checkpoint_path(
            out_dir, checkpoint_number_for_file, checkpoint_suffix
        )
        if current_checkpoint.exists():
            print(f"[{file_index:04}/{total_epochs:04}] already done: {teacher_file}")
            continue

        previous_checkpoint = checkpoint_path(
            out_dir, checkpoint_offset + file_index - 1, checkpoint_suffix
        )

        print(f"[{file_index:04}/{total_epochs:04}] train: {teacher_file}")
        if previous_checkpoint.exists():
            print(f"resume: {previous_checkpoint}")

        if args.backend == "train":
            train_args = [
                str(teacher_file),
                str(test_data),
                "--network",
                args.network,
                "--epoch",
                "1",
                "--batchsize",
                str(args.batchsize),
                "--testbatchsize",
                str(args.batchsize),
                "--gpu",
                str(args.gpu),
                "--lr",
                str(args.lr),
                "--lr_scheduler",
                lr_scheduler,
                "--scheduler_step_mode",
                "epoch",
                "--optimizer",
                "SGD(momentum=0.9,nesterov=True)",
                "--weight_decay",
                "0.0001",
                "--val_lambda",
                str(args.val_lambda),
                "--checkpoint",
                str(out_dir / "checkpoint-{epoch:04}.pth"),
                "--log",
                str(out_dir / f"train-{checkpoint_number_for_file:04}.log"),
            ]

            if previous_checkpoint.exists():
                train_args.extend(["--resume", str(previous_checkpoint)])
            elif file_index == args.start_index and resume_checkpoint:
                train_args.extend(["--resume", str(resume_checkpoint)])
                if args.reset_optimizer or auto_reset_optimizer:
                    train_args.append("--reset_optimizer")
                if args.reset_scheduler or auto_reset_scheduler:
                    train_args.append("--reset_scheduler")

            if file_index == total_epochs:
                train_args.extend(["--model", str(out_dir / "model-{epoch:04}")])
            if args.use_swa:
                train_args.extend(
                    [
                        "--use_swa",
                        "--swa_freq",
                        str(args.swa_freq),
                        "--swa_n_avr",
                        str(args.swa_n_avr),
                        "--swa_start_epoch",
                        str(args.swa_start_epoch),
                    ]
                )
            if not args.no_amp:
                train_args.append("--use_amp")
                train_args.extend(["--amp_dtype", args.amp_dtype])
            if not args.no_average:
                train_args.append("--use_average")
            if args.use_compile:
                train_args.append("--use_compile")
                if args.compile_backend:
                    train_args.extend(["--compile_backend", args.compile_backend])
                if args.compile_mode:
                    train_args.extend(["--compile_mode", args.compile_mode])
                if args.compile_fullgraph:
                    train_args.append("--compile_fullgraph")
                if args.compile_dynamic:
                    train_args.append("--compile_dynamic")

            run_env = inductor_subprocess_env(args, out_dir, checkpoint_number_for_file)
            command = python_module_command("dlshogi.train", train_args, run_env)
            subprocess.run(
                command,
                cwd=dlshogi_dir,
                env=run_env,
                check=True,
            )
            continue

        ptl_ckpt_path: Path | None = None
        model_resume_checkpoint: Path | None = None
        if previous_checkpoint.exists():
            ptl_ckpt_path = previous_checkpoint
        elif file_index == args.start_index and resume_checkpoint:
            if resume_checkpoint.suffix == ".ckpt" and not (
                args.reset_optimizer
                or args.reset_scheduler
                or auto_reset_optimizer
                or auto_reset_scheduler
            ):
                ptl_ckpt_path = resume_checkpoint
            elif resume_checkpoint.suffix == ".ckpt":
                model_resume_checkpoint = ptl_model_checkpoint_from_lightning(
                    resume_checkpoint,
                    out_dir / f"resume-model-{checkpoint_offset:04}.pth",
                )
            else:
                model_resume_checkpoint = resume_checkpoint

        tmp_checkpoint_name = f"_ptl-{checkpoint_number_for_file:04}"
        tmp_checkpoint = out_dir / f"{tmp_checkpoint_name}.ckpt"
        if tmp_checkpoint.exists():
            tmp_checkpoint.unlink()
        config_path = out_dir / f"ptl-config-{checkpoint_number_for_file:04}.yaml"
        log_path = out_dir / f"train-{checkpoint_number_for_file:04}.log"
        write_ptl_config(
            config_path,
            out_dir=out_dir,
            tmp_checkpoint_name=tmp_checkpoint_name,
            teacher_file=teacher_file,
            test_data=test_data,
            max_epochs=file_index if ptl_ckpt_path else 1,
            total_epochs=total_epochs,
            checkpoint_number_for_file=checkpoint_number_for_file,
            args=args,
            model_resume_checkpoint=model_resume_checkpoint,
            export_model=file_index == total_epochs,
        )

        ptl_args = ["fit", "--config", str(config_path)]
        if ptl_ckpt_path:
            ptl_args.extend(["--ckpt_path", str(ptl_ckpt_path)])
        run_env = inductor_subprocess_env(args, out_dir, checkpoint_number_for_file)
        command = python_module_command("dlshogi.ptl", ptl_args, run_env)
        run_command_with_log(
            command,
            dlshogi_dir,
            log_path,
            env=run_env,
        )
        finalize_ptl_checkpoint(tmp_checkpoint, current_checkpoint)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train exp_i 20b256 with yane-distill teacher data."
    )
    parser.add_argument(
        "--backend",
        choices=("train", "ptl"),
        default="train",
        help="Use legacy dlshogi.train or PyTorch Lightning dlshogi.ptl.",
    )
    parser.add_argument("--dlshogi_dir", type=Path, default=DEFAULT_DLSHOGI_DIR)
    parser.add_argument("--train_dir", type=Path, default=DEFAULT_TRAIN_DIR)
    parser.add_argument("--test_data", type=Path, default=DEFAULT_TEST_DATA)
    parser.add_argument(
        "--model_root",
        type=Path,
        default=DEFAULT_MODEL_ROOT,
        help="Root directory for model outputs. Used when --out_dir is omitted.",
    )
    parser.add_argument(
        "--out_dir",
        type=Path,
        help="Explicit output directory. Overrides automatic output naming.",
    )
    parser.add_argument(
        "--show_log",
        action="store_true",
        help="Extract and print training log CSV for --out_dir or --model_root/--network, then exit.",
    )
    parser.add_argument("--batchsize", type=int, default=1024)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--lr", type=float, default=0.03)
    parser.add_argument(
        "--lr_min",
        type=float,
        default=1e-5,
        help="Minimum learning rate for the cosine LR scheduler.",
    )
    parser.add_argument("--network", default="exp___i20x256")
    parser.add_argument("--val_lambda", type=float, default=1.0)
    parser.add_argument(
        "--amp_dtype",
        choices=("bfloat16", "float16"),
        default="bfloat16",
        help="Use bfloat16 by default because exp_i has Transformer layers.",
    )
    parser.add_argument("--no_amp", action="store_true")
    parser.add_argument("--no_average", action="store_true")
    parser.add_argument("--use_swa", dest="use_swa", action="store_true", default=True)
    parser.add_argument("--no_swa", dest="use_swa", action="store_false")
    parser.add_argument("--swa_freq", type=int, default=250)
    parser.add_argument("--swa_n_avr", type=int, default=10)
    parser.add_argument("--swa_start_epoch", type=int, default=1)
    parser.add_argument(
        "--use_compile",
        action="store_true",
        help="Use torch.compile. On Windows, dlshogi defaults to aot_eager unless --compile_backend is set.",
    )
    parser.add_argument(
        "--compile_backend",
        help="Backend for torch.compile, e.g. inductor or aot_eager.",
    )
    parser.add_argument(
        "--compile_mode",
        help="Mode for torch.compile, e.g. default, reduce-overhead, or max-autotune.",
    )
    parser.add_argument(
        "--compile_fullgraph",
        action="store_true",
        help="Pass fullgraph=True to torch.compile.",
    )
    parser.add_argument(
        "--compile_dynamic",
        action="store_true",
        help="Pass dynamic=True to torch.compile.",
    )
    parser.add_argument("--start_index", type=int, default=1)
    parser.add_argument(
        "--resume_checkpoint",
        type=Path,
        help="Checkpoint to initialize from before the first teacher file.",
    )
    parser.add_argument(
        "--reset_optimizer",
        action="store_true",
        help="Reset optimizer state when --resume_checkpoint is used.",
    )
    parser.add_argument(
        "--reset_scheduler",
        action="store_true",
        help="Reset lr scheduler state when --resume_checkpoint is used.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help=(
            "Number of rounds (full passes over all teacher files) to run consecutively. "
            "Equivalent to invoking trainer.py this many times. "
            "--out_dir / --resume_checkpoint apply to the first round only; "
            "subsequent rounds use auto round detection from --model_root and --network."
        ),
    )
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error(f"--rounds must be >= 1 (got {args.rounds})")

    dlshogi_dir = args.dlshogi_dir.resolve()
    train_dir = args.train_dir.resolve()
    test_data = args.test_data.resolve()
    model_root = args.model_root.resolve()

    if args.show_log:
        log_target = args.out_dir.resolve() if args.out_dir else make_out_dir(model_root, args.network)
        show_train_log([log_target], teacher_root=train_dir.parent)
        return

    teacher_files = collect_teacher_files(train_dir)
    if not teacher_files:
        raise FileNotFoundError(f"No .hcpe/.hcpe3 files found in {train_dir}")
    if not test_data.exists():
        raise FileNotFoundError(f"Test data not found: {test_data}")
    total_epochs = len(teacher_files)
    checkpoint_suffix = ".ckpt" if args.backend == "ptl" else ".pth"

    for round_iter in range(args.rounds):
        if args.rounds > 1:
            print()
            print(f"=== round {round_iter + 1}/{args.rounds} ===")
        run_one_round(
            args,
            dlshogi_dir=dlshogi_dir,
            test_data=test_data,
            model_root=model_root,
            teacher_files=teacher_files,
            total_epochs=total_epochs,
            checkpoint_suffix=checkpoint_suffix,
            use_explicit_state=(round_iter == 0),
        )


if __name__ == "__main__":
    main()
