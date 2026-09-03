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
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import trainer as trainer_module


@dataclass(frozen=True)
class Trial:
    lr: float
    val_lambda: float
    temperature: float
    batchsize: int | None
    batches_per_update: int | None
    out_dir: Path


TRIAL_DIR_RE = re.compile(
    r"^(?P<network>.+)_lr(?P<lr>[^_]+)_val(?P<val_lambda>[^_]+)"
    r"(?:_temp(?P<temperature>[^_]+))?"
    r"(?:_bs(?P<batchsize>\d+))?"
    r"(?:_bpu(?P<batches_per_update>\d+))?$"
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
    parser.add_argument("--val-lambdas", type=float, nargs="+")
    parser.add_argument("--temperatures", type=float, nargs="+", default=[1.0])
    parser.add_argument("--batchsizes", type=int, nargs="+")
    parser.add_argument("--batches-per-updates", type=int, nargs="+")
    parser.add_argument("--rounds", type=int, default=1)
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
    parser.add_argument(
        "--score",
        choices=("swa_test_accuracy", "test_accuracy", "test_value_accuracy", "test_loss_total"),
        default="swa_test_accuracy",
        help="Metric used for best_* columns. Falls back to test_accuracy if SWA is absent.",
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
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    if args.batchsizes and args.batchsize is not None:
        parser.error("--batchsizes and --batchsize cannot be used together")
    if args.batches_per_updates and args.batches_per_update is not None:
        parser.error("--batches-per-updates and --batches-per-update cannot be used together")
    return args


def float_tag(value: float) -> str:
    return f"{value:.12g}"


def make_trials(args: argparse.Namespace) -> list[Trial]:
    trials: list[Trial] = []
    batchsizes = args.batchsizes or [args.batchsize]
    batches_per_updates = args.batches_per_updates or [args.batches_per_update]
    for lr in args.lrs:
        for val_lambda in args.val_lambdas:
            for temperature in args.temperatures:
                for batchsize in batchsizes:
                    for batches_per_update in batches_per_updates:
                        name = (
                            f"{args.network}_lr{float_tag(lr)}"
                            f"_val{float_tag(val_lambda)}"
                            f"_temp{float_tag(temperature)}"
                        )
                        if batchsize is not None:
                            name += f"_bs{batchsize}"
                        if batches_per_update is not None:
                            name += f"_bpu{batches_per_update}"
                        trials.append(
                            Trial(
                                lr=lr,
                                val_lambda=val_lambda,
                                temperature=temperature,
                                batchsize=batchsize,
                                batches_per_update=batches_per_update,
                                out_dir=args.model_root / name,
                            )
                        )
    return trials


def trial_from_directory(path: Path) -> Trial | None:
    match = TRIAL_DIR_RE.fullmatch(path.name)
    if not match:
        return None
    try:
        lr = float(match.group("lr"))
        val_lambda = float(match.group("val_lambda"))
        temperature = float(match.group("temperature") or "1.0")
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
        val_lambda=val_lambda,
        temperature=temperature,
        batchsize=batchsize,
        batches_per_update=batches_per_update,
        out_dir=path,
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
            trial.val_lambda,
            trial.temperature,
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
    ]
    if trial.batchsize is not None:
        command.extend(["--batchsize", str(trial.batchsize)])
    if trial.batches_per_update is not None:
        command.extend(["--batches-per-update", str(trial.batches_per_update)])
    append_optional_trainer_args(args, command)
    return command


def append_optional_trainer_args(args: argparse.Namespace, command: list[str]) -> None:
    value_options = [
        ("lr_min", "--lr-min"),
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


def parse_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return math.nan


def row_metric(row: trainer_module.TrainLogRow, metric: str) -> float:
    if metric == "swa_test_accuracy":
        value = parse_float(row.swa_test_accuracy[0])
        if not math.isnan(value):
            return value
        return parse_float(row.test_accuracy[0])
    if metric == "test_accuracy":
        return parse_float(row.test_accuracy[0])
    if metric == "test_value_accuracy":
        return parse_float(row.test_accuracy[1])
    if metric == "test_loss_total":
        return parse_float(row.test_loss[3])
    raise ValueError(metric)


def better_score(candidate: float, current: float, metric: str) -> bool:
    if math.isnan(current):
        return True
    if math.isnan(candidate):
        return False
    if metric == "test_loss_total":
        return candidate < current
    return candidate > current


def summarize_trial(args: argparse.Namespace, trial: Trial) -> dict[str, str | int]:
    log_files = trainer_module.iter_train_log_files([trial.out_dir]) if trial.out_dir.exists() else []
    rows: list[trainer_module.TrainLogRow] = []
    teacher_root = args.train_dir if args.train_dir is not None else None
    for log_file in log_files:
        rows.extend(trainer_module.parse_train_log(log_file, teacher_root))

    summary: dict[str, str | int] = {
        "lr": str(trial.lr),
        "val_lambda": str(trial.val_lambda),
        "temperature": str(trial.temperature),
        "batchsize": str(trial.batchsize or ""),
        "batches_per_update": str(trial.batches_per_update or ""),
        "policy_test_accuracy": "",
        "test_value_accuracy": "",
        "swa_policy_test_accuracy": "",
        "swa_test_value_accuracy": "",
        "test_loss": "",
        "best_metric": args.score,
        "best_score": "",
        "best_epoch": "",
        "best_source": "",
        "status": "done" if rows else "no_log",
        "log_files": str(len(log_files)),
        "rows": str(len(rows)),
        "final_epoch": "",
        "out_dir": str(trial.out_dir),
    }
    if not rows:
        return summary

    final = rows[-1]
    summary.update(
        {
            "final_epoch": final.epoch or "",
            "policy_test_accuracy": final.test_accuracy[0],
            "test_value_accuracy": final.test_accuracy[1],
            "swa_policy_test_accuracy": final.swa_test_accuracy[0],
            "swa_test_value_accuracy": final.swa_test_accuracy[1],
            "test_loss": final.test_loss[3],
        }
    )

    best_row = rows[0]
    best_value = row_metric(best_row, args.score)
    for row in rows[1:]:
        value = row_metric(row, args.score)
        if better_score(value, best_value, args.score):
            best_row = row
            best_value = value

    summary.update(
        {
            "best_score": "" if math.isnan(best_value) else f"{best_value:.7f}",
            "best_epoch": best_row.epoch or "",
            "best_source": best_row.source,
        }
    )
    return summary


def write_summary(path: Path, rows: list[dict[str, str | int]]) -> None:
    fieldnames = [
        "lr",
        "val_lambda",
        "temperature",
        "batchsize",
        "batches_per_update",
        "policy_test_accuracy",
        "test_value_accuracy",
        "swa_policy_test_accuracy",
        "swa_test_value_accuracy",
        "test_loss",
        "best_metric",
        "best_score",
        "best_epoch",
        "best_source",
        "status",
        "log_files",
        "rows",
        "final_epoch",
        "out_dir",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    trials = discover_trials(args.model_root) if args.summary_only else make_trials(args)
    summary_csv = args.summary_csv or args.model_root / "grid_summary.csv"

    if not args.summary_only:
        for index, trial in enumerate(trials, start=1):
            command = trainer_command(args, trial)
            print(
                f"[{index}/{len(trials)}] "
                f"lr={trial.lr} val_lambda={trial.val_lambda} "
                f"temperature={trial.temperature} "
                f"batchsize={trial.batchsize or '-'} "
                f"batches_per_update={trial.batches_per_update or '-'}"
            )
            print(" ".join(command))
            if args.dry_run:
                summaries = [summarize_trial(args, item) for item in trials]
                write_summary(summary_csv, summaries)
                print(f"summary updated (dry-run): {summary_csv}")
                continue
            failed = False
            try:
                subprocess.run(command, check=True)
            except subprocess.CalledProcessError:
                failed = True
                if not args.continue_on_error:
                    summaries = [summarize_trial(args, item) for item in trials]
                    write_summary(summary_csv, summaries)
                    print(f"summary: {summary_csv}")
                    raise
                print(f"trial failed: {trial.out_dir}", file=sys.stderr)

            summaries = [summarize_trial(args, item) for item in trials]
            write_summary(summary_csv, summaries)
            status = "failed" if failed else "done"
            print(f"summary updated ({status}): {summary_csv}")

    summaries = [summarize_trial(args, trial) for trial in trials]
    write_summary(summary_csv, summaries)
    print(f"summary: {summary_csv}")


if __name__ == "__main__":
    main()
