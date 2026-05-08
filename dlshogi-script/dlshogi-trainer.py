"""yane-distill 教師で exp_i 20b256 を学習するためのスクリプト。

使い方:

1. 1周目を最初から実行する

    cd C:\shogi\learner
    python dlshogi-trainer.py

   既定では C:\shogi\teacher\yane-distill にある *.hcpe / *.hcpe3 を
   1ファイルずつ train.py に渡して、全教師ファイルを1回ずつ学習する。
   出力先は --network から自動で決まる。
   既定値では C:\shogi\model\exp___i20x256。
   フォルダ名にも --network の文字列をそのまま使う。

2. 途中中断から再開する / 次の周を始める

    python dlshogi-trainer.py

   同じコマンドをもう一度実行すればよい。
   最新の周が途中なら、その周の次の未完了教師ファイルから再開する。
   最新の周が完了済みなら、自動で次の round フォルダを作り、lr=0.03 から次の周を始める。
   例えば1周目が完了していれば、出力先は自動的に
   C:\shogi\model\exp___i20x256_round2 になる。

3. 手動で特定 checkpoint から次の周を始める

    python dlshogi-trainer.py ^
      --resume_checkpoint C:\shogi\model\exp___i20x256\checkpoint-0021.pth ^
      --reset_optimizer ^
      --reset_scheduler

   既存の出力先を手動で指定したい場合だけ --out_dir を使う。

   新しい周を開始するときは optimizer と CosineAnnealingLR を自動で初期化する。
   そのため2周目/3周目の lr は 0.03 から再スタートする。

4. --start_index について

   --start_index は、その周の教師ファイルを何番目から実行するかを指定する。
   番号は、ソート済み教師ファイル一覧の1始まり。
   例えば --start_index 5 なら、1から4番目の教師ファイルはこの実行では飛ばし、
   5番目の教師ファイルから train.py を呼ぶ。

   checkpoint や log の番号は詰めず、教師ファイル番号に対応したままになる。
   例えば --start_index 5 なら train-0005.log と checkpoint-0005.pth から使う。

   通常の途中中断からの再開では指定しなくてよい。
   既存の checkpoint がある教師ファイルは自動で already done として飛ばすため、
   同じコマンドを再実行すれば次の未完了教師ファイルから続きになる。

5. SWA について

   SWA は既定で ON。最後に書き出される model は SWA 済みになる。
   checkpoint にも swa_model が保存されるので、途中中断からの再開もできる。

   SWA を使いたくない場合だけ --no_swa を付ける。

    python dlshogi-trainer.py --no_swa

   注意: このスクリプトは教師1ファイルごとに train.py を呼ぶため、
   train.py が最後に行う SWA の BatchNorm 更新は、最後の教師ファイルだけを使う。
   SWA の平均重み自体は学習全体で更新されるが、BN 更新を全教師で厳密に行いたい場合は
   追加の最終エクスポート処理が必要。

主な既定値:

   network     = exp___i20x256
   model folder= C:\shogi\model\exp___i20x256
   batchsize   = 1024
   lr          = 0.03
   eta_min     = 1e-5
   amp_dtype   = bfloat16
   val_lambda  = 1.0
   start_index = 1
   use_swa     = True

補足:

   exp_i は Transformer 層を含むため、既定で --use_amp --amp_dtype bfloat16
   を train.py に渡す。float16 では途中で NaN になりやすい。
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path


DEFAULT_DLSHOGI_DIR = Path(r"C:\shogi\DeepLearningShogi")
DEFAULT_TRAIN_DIR = Path(r"C:\shogi\teacher\yane-distill")
DEFAULT_TEST_DATA = Path(
    r"C:\shogi\teacher\test\test20231010_fg2021_dls5_ryfc20_ev8250k825.hcpe"
)
DEFAULT_MODEL_ROOT = Path(r"C:\shogi\model")
CHECKPOINT_RE = re.compile(r"checkpoint-(\d+)\.pth$")
ROUND_SUFFIX_RE = re.compile(r"_round(\d+)$")


def collect_teacher_files(train_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("*.hcpe", "*.hcpe3"):
        files.extend(train_dir.glob(pattern))
    return sorted(files)


def checkpoint_path(out_dir: Path, epoch: int) -> Path:
    return out_dir / f"checkpoint-{epoch:04}.pth"


def checkpoint_number(path: Path) -> int:
    match = CHECKPOINT_RE.fullmatch(path.name)
    if not match:
        raise ValueError(f"Cannot infer checkpoint number from: {path}")
    return int(match.group(1))


def make_out_dir(model_root: Path, network: str) -> Path:
    return model_root / network


def reset_logging() -> None:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        handler.flush()
        handler.close()
        root_logger.removeHandler(handler)


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


def latest_checkpoint(directory: Path) -> Path | None:
    checkpoints: list[tuple[int, Path]] = []
    for path in directory.glob("checkpoint-*.pth"):
        try:
            checkpoints.append((checkpoint_number(path), path))
        except ValueError:
            continue
    if not checkpoints:
        return None
    return max(checkpoints, key=lambda item: item[0])[1]


def find_checkpoint_by_number(base_dir: Path, number: int) -> Path | None:
    checkpoint_name = f"checkpoint-{number:04}.pth"
    for _, directory in round_directories(base_dir):
        path = directory / checkpoint_name
        if path.exists():
            return path
    return None


def auto_round_state(
    model_root: Path, network: str, total_epochs: int
) -> tuple[Path, Path | None, int, bool, bool]:
    base_dir = make_out_dir(model_root, network)
    states: list[tuple[int, int, Path, Path]] = []
    for round_number, directory in round_directories(base_dir):
        checkpoint = latest_checkpoint(directory)
        if checkpoint is None:
            continue
        states.append((checkpoint_number(checkpoint), round_number, directory, checkpoint))

    if not states:
        return base_dir, None, 0, False, False

    latest_number, _, latest_dir, latest_path = max(states, key=lambda item: item[0])
    if latest_number % total_epochs == 0:
        return next_round_out_dir(latest_path), latest_path, latest_number, True, True

    checkpoint_offset = (latest_number // total_epochs) * total_epochs
    resume_checkpoint = (
        find_checkpoint_by_number(base_dir, checkpoint_offset)
        if checkpoint_offset > 0
        else None
    )
    return latest_dir, resume_checkpoint, checkpoint_offset, False, False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train exp_i 20b256 with yane-distill teacher data."
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
    parser.add_argument("--batchsize", type=int, default=1024)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--lr", type=float, default=0.03)
    parser.add_argument("--eta_min", type=float, default=1e-5)
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
    args = parser.parse_args()

    dlshogi_dir = args.dlshogi_dir.resolve()
    train_dir = args.train_dir.resolve()
    test_data = args.test_data.resolve()
    model_root = args.model_root.resolve()

    teacher_files = collect_teacher_files(train_dir)
    if not teacher_files:
        raise FileNotFoundError(f"No .hcpe/.hcpe3 files found in {train_dir}")
    if not test_data.exists():
        raise FileNotFoundError(f"Test data not found: {test_data}")
    total_epochs = len(teacher_files)

    auto_reset_optimizer = False
    auto_reset_scheduler = False
    resume_checkpoint = args.resume_checkpoint.resolve() if args.resume_checkpoint else None
    if args.out_dir:
        out_dir = args.out_dir.resolve()
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
        ) = auto_round_state(model_root, args.network, total_epochs)

    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"

    sys.path.insert(0, str(dlshogi_dir))
    os.chdir(dlshogi_dir)

    from dlshogi.train import main as train_main

    total_epochs = len(teacher_files)
    lr_scheduler = f"CosineAnnealingLR(T_max={total_epochs},eta_min={args.eta_min})"

    print(f"DeepLearningShogi: {dlshogi_dir}")
    print(f"teacher files: {len(teacher_files)}")
    print(f"test data: {test_data}")
    print(f"out dir: {out_dir}")
    print(f"network: {args.network}")
    print(f"lr scheduler: {lr_scheduler}")
    if resume_checkpoint:
        print(f"initial checkpoint: {resume_checkpoint}")
        print(f"checkpoint offset: {checkpoint_offset}")

    for file_index, teacher_file in enumerate(teacher_files, start=1):
        if file_index < args.start_index:
            continue

        current_checkpoint = checkpoint_path(out_dir, checkpoint_offset + file_index)
        if current_checkpoint.exists():
            print(f"[{file_index:04}/{total_epochs:04}] already done: {teacher_file}")
            continue

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
            str(out_dir / f"train-{file_index:04}.log"),
        ]

        previous_checkpoint = checkpoint_path(out_dir, checkpoint_offset + file_index - 1)
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

        print(f"[{file_index:04}/{total_epochs:04}] train: {teacher_file}")
        if previous_checkpoint.exists():
            print(f"resume: {previous_checkpoint}")

        reset_logging()
        train_main(*train_args)


if __name__ == "__main__":
    main()
