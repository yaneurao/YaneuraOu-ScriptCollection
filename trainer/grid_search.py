"""Run trainer.py grid search and summarize results.

Example:
  python trainer/grid_search.py --checkpoint C:\\model\\checkpoint-0839.pth \
    --train-dir C:\\teacher\\train --network exp___i15x192 \
    --lrs 0.001 0.0007 --val-lambdas 0.33 0.5 --temperatures 1.0 0.8 \
    --use_compile --compile_backend inductor
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path, PureWindowsPath

import trainer as trainer_module


@dataclass(frozen=True)
class Trial:
    lr: float
    lr_min: float | None
    val_lambda: float
    temperature: float
    policy_mix: float
    batchsize: int | None
    batches_per_update: int | None
    out_dir: Path
    value_loss_min_weight: float = 1.0


FLOAT_TAG_RE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
TRIAL_DIR_RE = re.compile(
    rf"^(?P<network>.+)_lr(?P<lr>{FLOAT_TAG_RE})"
    r"(?:_lrmin(?P<lr_min>[^_]+))?"
    r"_val(?P<val_lambda>[^_]+)"
    r"(?:_temp(?P<temperature>[^_]+))?"
    r"(?:_pmix(?P<policy_mix>[^_]+))?"
    r"(?:_bs(?P<batchsize>\d+))?"
    r"(?:_bpu(?P<batches_per_update>\d+))?"
    r"(?:_vlmw(?P<value_loss_min_weight>[^_]+))?$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run YOSC trainer.py for every lr/val_lambda pair and summarize logs."
        )
    )
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--train-dir", type=Path)
    parser.add_argument("--network")
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--lrs", type=float, nargs="+")
    parser.add_argument("--lr-mins", type=float, nargs="+")
    parser.add_argument("--val-lambdas", type=float, nargs="+")
    parser.add_argument("--temperatures", type=float, nargs="+")
    parser.add_argument("--policy-mixes", type=float, nargs="+")
    parser.add_argument("--value-loss-min-weights", type=float, nargs="+")
    parser.add_argument("--batchsizes", type=int, nargs="+")
    parser.add_argument("--batches-per-updates", type=int, nargs="+")
    parser.add_argument("--rounds", type=int, nargs="+",
                        help="Rounds to summarize; train once up to the largest (default: 1).")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--trainer", type=Path, default=Path(__file__).with_name("trainer.py"))
    parser.add_argument("--summary-csv", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Do not run training. Rebuild summary CSV from existing trial folders.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run remaining trials even if one trainer.py invocation fails.",
    )
    parser.add_argument("--batchsize", type=int)
    parser.add_argument("--batches-per-update", type=int)
    parser.add_argument("--lr-min", type=float)
    parser.add_argument("--lr-scheduler", choices=("cosine", "exponential"))
    parser.add_argument("--hcpe_val_lambda", type=float)
    parser.add_argument("--hcpe3_val_lambda", type=float)
    parser.add_argument("--gpu", type=int)
    parser.add_argument("--amp_dtype", choices=("bfloat16", "float16"))
    parser.add_argument("--no_amp", action="store_true")
    parser.add_argument("--no_average", action="store_true")
    parser.add_argument("--no_evalfix", action="store_true")
    parser.add_argument("--use_swa", action="store_true")
    parser.add_argument("--no_swa", action="store_true")
    parser.add_argument("--swa_freq", type=int)
    parser.add_argument("--swa_n_avr", type=int)
    parser.add_argument("--swa_start_epoch", type=int)
    parser.add_argument("--use_compile", action="store_true")
    parser.add_argument("--compile_backend")
    parser.add_argument("--compile_mode")
    parser.add_argument("--compile_fullgraph", action="store_true")
    parser.add_argument("--compile_dynamic", action="store_true")
    args = parser.parse_args()
    args.include_value_loss_min_weight = args.value_loss_min_weights is not None
    if args.value_loss_min_weights is None:
        args.value_loss_min_weights = [1.0]
    if any(not 0.0 <= value <= 1.0 for value in args.value_loss_min_weights):
        parser.error("--value-loss-min-weights values must be between 0 and 1")
    args.include_temperature = args.temperatures is not None
    args.include_policy_mix = args.policy_mixes is not None
    if args.temperatures is None:
        args.temperatures = [1.0]
    if args.policy_mixes is None:
        args.policy_mixes = [1.0]
    if any(not 0.0 <= value <= 1.0 for value in args.policy_mixes):
        parser.error("--policy-mixes values must be between 0 and 1")
    if not args.summary_only:
        missing = [
            name
            for name in ("checkpoint", "train_dir", "network", "lrs", "val_lambdas")
            if getattr(args, name) is None
        ]
        if missing:
            parser.error(
                "the following arguments are required unless --summary-only is used: "
                + ", ".join("--" + name.replace("_", "-") for name in missing)
            )
    if args.rounds is not None and any(value < 1 for value in args.rounds):
        parser.error("--rounds must be >= 1")
    args.summary_rounds = sorted(set(args.rounds)) if args.rounds is not None else (
        None if args.summary_only else [1]
    )
    args.rounds = max(args.summary_rounds) if args.summary_rounds else 1
    if args.batchsizes and args.batchsize is not None:
        parser.error("--batchsizes and --batchsize cannot be used together")
    if args.batches_per_updates and args.batches_per_update is not None:
        parser.error("--batches-per-updates and --batches-per-update cannot be used together")
    if args.lr_mins and args.lr_min is not None:
        parser.error("--lr-mins and --lr-min cannot be used together")
    return args


def float_tag(value: float) -> str:
    return f"{value:.12g}"


def make_trials(args: argparse.Namespace) -> list[Trial]:
    trials: list[Trial] = []
    lr_mins = args.lr_mins or [args.lr_min]
    batchsizes = args.batchsizes or [args.batchsize]
    batches_per_updates = args.batches_per_updates or [args.batches_per_update]
    for lr in args.lrs:
        for lr_min in lr_mins:
            for val_lambda in args.val_lambdas:
                for temperature, policy_mix, value_loss_min_weight in product(
                        args.temperatures, args.policy_mixes, args.value_loss_min_weights):
                    for batchsize in batchsizes:
                        for batches_per_update in batches_per_updates:
                            name = f"{args.network}_lr{float_tag(lr)}"
                            if lr_min is not None:
                                name += f"_lrmin{float_tag(lr_min)}"
                            name += f"_val{float_tag(val_lambda)}"
                            if args.include_temperature:
                                name += f"_temp{float_tag(temperature)}"
                            if args.include_policy_mix:
                                name += f"_pmix{float_tag(policy_mix)}"
                            if batchsize is not None:
                                name += f"_bs{batchsize}"
                            if batches_per_update is not None:
                                name += f"_bpu{batches_per_update}"
                            if args.include_value_loss_min_weight:
                                name += f"_vlmw{float_tag(value_loss_min_weight)}"
                            trials.append(
                                Trial(
                                    lr=lr,
                                    lr_min=lr_min,
                                    val_lambda=val_lambda,
                                    temperature=temperature,
                                    policy_mix=policy_mix,
                                    batchsize=batchsize,
                                    batches_per_update=batches_per_update,
                                    out_dir=args.model_root / name,
                                    value_loss_min_weight=value_loss_min_weight,
                                )
                            )
    return trials


def trial_from_directory(path: Path) -> Trial | None:
    match = TRIAL_DIR_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        lr = float(match.group("lr"))
        lr_min = float(match.group("lr_min")) if match.group("lr_min") else None
        val_lambda = float(match.group("val_lambda"))
        temperature = float(match.group("temperature") or "1.0")
        policy_mix = float(match.group("policy_mix") or "1.0")
        value_loss_min_weight = float(match.group("value_loss_min_weight") or "1.0")
        batchsize = int(match.group("batchsize")) if match.group("batchsize") else None
        batches_per_update = (
            int(match.group("batches_per_update"))
            if match.group("batches_per_update")
            else None
        )
    except ValueError:
        return None
    return Trial(
        lr=lr,
        lr_min=lr_min,
        val_lambda=val_lambda,
        temperature=temperature,
        policy_mix=policy_mix,
        batchsize=batchsize,
        batches_per_update=batches_per_update,
        out_dir=path,
        value_loss_min_weight=value_loss_min_weight,
    )


def discover_trials(model_root: Path) -> list[Trial]:
    if not model_root.is_dir():
        raise FileNotFoundError(f"model root not found: {model_root}")

    trials: list[Trial] = []
    for child in model_root.iterdir():
        if not child.is_dir():
            continue
        trial = trial_from_directory(child)
        if trial is not None:
            trials.append(trial)
    return sorted(
        trials,
        key=lambda trial: (
            trial.lr,
            trial.lr_min if trial.lr_min is not None else -1,
            trial.val_lambda,
            trial.temperature,
            trial.policy_mix,
            trial.value_loss_min_weight,
            trial.batchsize or -1,
            trial.batches_per_update or -1,
            str(trial.out_dir),
        ),
    )


def trainer_command(args: argparse.Namespace, trial: Trial) -> list[str]:
    command = [
        args.python,
        str(args.trainer),
        "--network",
        args.network,
        "--train_dir",
        str(args.train_dir),
        "--out_dir",
        str(trial.out_dir),
        "--init_checkpoint",
        str(args.checkpoint),
        "--rounds",
        str(args.rounds),
        "--lr",
        str(trial.lr),
        "--val_lambda",
        str(trial.val_lambda),
        "--temperature",
        str(trial.temperature),
        "--policy-mix",
        str(trial.policy_mix),
        "--value-loss-min-weight",
        str(trial.value_loss_min_weight),
    ]
    if trial.batchsize is not None:
        command.extend(["--batchsize", str(trial.batchsize)])
    if trial.batches_per_update is not None:
        command.extend(["--batches-per-update", str(trial.batches_per_update)])
    if trial.lr_min is not None:
        command.extend(["--lr-min", str(trial.lr_min)])
    append_optional_trainer_args(args, command)
    return command


def append_optional_trainer_args(args: argparse.Namespace, command: list[str]) -> None:
    value_options = [
        ("lr_scheduler", "--lr-scheduler"),
        ("hcpe_val_lambda", "--hcpe_val_lambda"),
        ("hcpe3_val_lambda", "--hcpe3_val_lambda"),
        ("gpu", "--gpu"),
        ("amp_dtype", "--amp_dtype"),
        ("swa_freq", "--swa_freq"),
        ("swa_n_avr", "--swa_n_avr"),
        ("swa_start_epoch", "--swa_start_epoch"),
        ("compile_backend", "--compile_backend"),
        ("compile_mode", "--compile_mode"),
    ]
    for attr, option in value_options:
        value = getattr(args, attr)
        if value is not None:
            command.extend([option, str(value)])

    flag_options = [
        ("no_amp", "--no_amp"),
        ("no_average", "--no_average"),
        ("no_evalfix", "--no_evalfix"),
        ("use_swa", "--use_swa"),
        ("no_swa", "--no_swa"),
        ("use_compile", "--use_compile"),
        ("compile_fullgraph", "--compile_fullgraph"),
        ("compile_dynamic", "--compile_dynamic"),
    ]
    for attr, option in flag_options:
        if getattr(args, attr):
            command.append(option)


def summarize_trial(args: argparse.Namespace, trial: Trial,
                    round_number: int | None = None) -> dict[str, str | int]:
    out_dir = trial.out_dir if round_number in (None, 1) else trial.out_dir.with_name(
        f"{trial.out_dir.name}_round{round_number}"
    )
    if round_number is None:
        log_files = trainer_module.iter_train_log_files([out_dir]) if out_dir.exists() else []
    else:
        # Only this round: iter_train_log_files also includes sibling rounds.
        log_files = sorted(out_dir.glob('train-*.log'), key=trainer_module.train_log_index)
    rows: list[trainer_module.TrainLogRow] = []
    for log_file in log_files:
        rows.extend(trainer_module.parse_train_log(log_file, None))

    train_dir = str(args.train_dir) if args.train_dir is not None else ""
    teachers = [row.teacher for row in rows if row.teacher]
    if teachers:
        teacher = teachers[-1]
        teacher_path = PureWindowsPath(teacher) if "\\" in teacher else Path(teacher)
        train_dir = str(teacher_path.parent)

    summary: dict[str, str | int] = {
        "lr": str(trial.lr),
        "lr_min": str(trial.lr_min) if trial.lr_min is not None else "",
        "val_lambda": str(trial.val_lambda),
        "temperature": str(trial.temperature),
        "policy_mix": str(trial.policy_mix),
        "value_loss_min_weight": str(trial.value_loss_min_weight),
        "batchsize": str(trial.batchsize) if trial.batchsize is not None else "",
        "batches_per_update": (
            str(trial.batches_per_update)
            if trial.batches_per_update is not None
            else ""
        ),
        "test_policy_accuracy": "",
        "test_value_accuracy": "",
        "swa_test_policy_accuracy": "",
        "swa_test_value_accuracy": "",
        "test_total_loss": "",
        "round": round_number or (
            trainer_module.split_train_log_round_dir_name(log_files[-1].parent.name)[1]
            if log_files else 1
        ),
        "status": "done" if rows else "no_log",
        "final_epoch": "",
        "train-dir": train_dir,
        "out_dir": str(out_dir),
    }
    if not rows:
        return summary

    final = rows[-1]
    summary.update(
        {
            "final_epoch": final.epoch or "",
            "test_policy_accuracy": final.test_accuracy[0],
            "test_value_accuracy": final.test_accuracy[1],
            "swa_test_policy_accuracy": final.swa_test_accuracy[0],
            "swa_test_value_accuracy": final.swa_test_accuracy[1],
            "test_total_loss": final.test_loss[3],
        }
    )

    return summary


def summarize_trials(args: argparse.Namespace, trials: list[Trial]) -> list[dict[str, str | int]]:
    return [summarize_trial(args, trial, round_number)
            for trial in trials for round_number in (args.summary_rounds or [None])]


def write_summary(path: Path, rows: list[dict[str, str | int]], *,
                  include_temperature: bool = True, include_policy_mix: bool = True,
                  include_value_loss_min_weight: bool = False) -> None:
    fieldnames = [
        "lr",
        "lr_min",
        "val_lambda",
        "temperature",
        "policy_mix",
        "value_loss_min_weight",
        "batchsize",
        "batches_per_update",
        "round",
        "test_policy_accuracy",
        "test_value_accuracy",
        "swa_test_policy_accuracy",
        "swa_test_value_accuracy",
        "test_total_loss",
        "status",
        "final_epoch",
        "train-dir",
        "out_dir",
    ]
    if not include_temperature:
        fieldnames.remove("temperature")
    if not include_policy_mix:
        fieldnames.remove("policy_mix")
    if not include_value_loss_min_weight:
        fieldnames.remove("value_loss_min_weight")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fieldnames if key in row} for row in rows)


def completed_round_count(args: argparse.Namespace, trial: Trial) -> int:
    if not trainer_module.checkpoint_files_in_directory(trial.out_dir):
        return 0
    teacher_count = len(trainer_module.collect_teacher_files(args.train_dir))
    if not teacher_count:
        return 0
    directories = dict(trainer_module.round_directories(trial.out_dir))
    for round_number in range(1, args.rounds + 1):
        directory = directories.get(round_number)
        if directory is None:
            return round_number - 1
        epoch = teacher_count * round_number
        checkpoint = directory / f"checkpoint-{epoch:04}.pth"
        # The final checkpoint precedes SWA evaluation and model export.
        model = directory / f"model-{epoch:04}"
        if not checkpoint.is_file() or not any(
            path.is_file() and path.stat().st_size > 0
            for path in (model, model.with_suffix(".npz"))
        ):
            return round_number - 1
        log = directory / f"train-{epoch:04}.log"
        if not log.is_file():
            return round_number - 1
        rows = trainer_module.parse_train_log(log, None)
        if not rows or rows[-1].epoch != epoch or not rows[-1].test_loss[3]:
            return round_number - 1
    return args.rounds


def trial_is_complete(args: argparse.Namespace, trial: Trial) -> bool:
    return completed_round_count(args, trial) == args.rounds


def remaining_trainer_commands(
    args: argparse.Namespace, trial: Trial, completed: int,
) -> list[list[str]]:
    if completed == 0:
        return [trainer_command(args, trial)]
    teacher_count = len(trainer_module.collect_teacher_files(args.train_dir))
    commands = []
    for round_number in range(completed + 1, args.rounds + 1):
        out_dir = trial.out_dir.with_name(f"{trial.out_dir.name}_round{round_number}")
        if trainer_module.checkpoint_files_in_directory(out_dir):
            raise FileExistsError(
                f"Round {round_number} has incomplete checkpoints: {out_dir}. "
                "Automatic continuation is supported only from completed rounds."
            )
        previous_dir = trial.out_dir if round_number == 2 else trial.out_dir.with_name(
            f"{trial.out_dir.name}_round{round_number - 1}"
        )
        checkpoint = previous_dir / f"checkpoint-{teacher_count * (round_number - 1):04}.pth"
        command = trainer_command(args, trial)
        command[command.index('--out_dir') + 1] = str(out_dir)
        command[command.index('--rounds') + 1] = '1'
        init_index = command.index('--init_checkpoint')
        command[init_index:init_index + 2] = ['--resume_checkpoint', str(checkpoint)]
        command.extend(['--reset_optimizer', '--reset_scheduler'])
        commands.append(command)
    return commands


def main() -> None:
    args = parse_args()
    trials = discover_trials(args.model_root) if args.summary_only else make_trials(args)
    if args.summary_only and not trials:
        raise ValueError(f"No trial folders found in {args.model_root}; summary CSV was not changed.")
    summary_csv = args.summary_csv or args.model_root / "grid_summary.csv"
    summary_options = dict(include_temperature=args.include_temperature,
                           include_policy_mix=args.include_policy_mix,
                           include_value_loss_min_weight=args.include_value_loss_min_weight)

    if not args.summary_only:
        summaries = summarize_trials(args, trials)
        write_summary(summary_csv, summaries, **summary_options)
        print(f"summary initialized: {summary_csv}")

        for index, trial in enumerate(trials, start=1):
            print(
                f"[{index}/{len(trials)}] "
                f"lr={trial.lr} "
                f"lr_min={trial.lr_min if trial.lr_min is not None else '-'} "
                f"val_lambda={trial.val_lambda} "
                f"temperature={trial.temperature} "
                f"policy_mix={trial.policy_mix} "
                f"value_loss_min_weight={trial.value_loss_min_weight} "
                f"batchsize={trial.batchsize if trial.batchsize is not None else '-'} "
                "batches_per_update="
                f"{trial.batches_per_update if trial.batches_per_update is not None else '-'}"
            )
            completed = completed_round_count(args, trial)
            if completed == args.rounds:
                print(f"skip completed: {trial.out_dir}")
                continue
            commands = remaining_trainer_commands(args, trial, completed)
            if completed:
                print(f"continue: {completed}/{args.rounds} rounds completed; "
                      f"starting round {completed + 1}: {trial.out_dir}")
            if args.dry_run:
                for command in commands:
                    print(" ".join(command))
                continue
            failed = False
            try:
                for command in commands:
                    print(" ".join(command))
                    subprocess.run(command, check=True)
            except subprocess.CalledProcessError:
                failed = True
                if not args.continue_on_error:
                    summaries = summarize_trials(args, trials)
                    write_summary(summary_csv, summaries, **summary_options)
                    print(f"summary: {summary_csv}")
                    raise
                print(f"trial failed: {trial.out_dir}", file=sys.stderr)

            summaries = summarize_trials(args, trials)
            write_summary(summary_csv, summaries, **summary_options)
            status = "failed" if failed else "done"
            print(f"summary updated ({status}): {summary_csv}")

    summaries = summarize_trials(args, trials)
    write_summary(summary_csv, summaries, **summary_options)
    print(f"summary: {summary_csv}")


if __name__ == "__main__":
    main()
