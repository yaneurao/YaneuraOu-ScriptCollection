"""Run trainer.py grid search and summarize results.

Example:
  python trainer/grid_search.py --checkpoint C:\\model\\checkpoint-0839.pth \
    --train-dir C:\\teacher\\train --network exp___i15x192 \
    --lrs 0.001 0.0007 --val-lambdas 0.33 0.5 \
    -- --use_compile --compile_backend inductor
"""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import trainer as trainer_module


@dataclass(frozen=True)
class Trial:
    lr: float
    val_lambda: float
    out_dir: Path


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description=(
            "Run YOSC trainer.py for every lr/val_lambda pair and summarize logs."
        )
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--train-dir", type=Path, required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--lrs", type=float, nargs="+", required=True)
    parser.add_argument("--val-lambdas", type=float, nargs="+", required=True)
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
    args, extra_args = parser.parse_known_args()
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    return args, extra_args


def float_tag(value: float) -> str:
    return f"{value:.12g}"


def make_trials(args: argparse.Namespace) -> list[Trial]:
    trials: list[Trial] = []
    for lr in args.lrs:
        for val_lambda in args.val_lambdas:
            name = f"{args.network}_lr{float_tag(lr)}_val{float_tag(val_lambda)}"
            trials.append(Trial(lr=lr, val_lambda=val_lambda, out_dir=args.model_root / name))
    return trials


def trainer_command(args: argparse.Namespace, trial: Trial, extra_args: list[str]) -> list[str]:
    return [
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
        *extra_args,
    ]


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
    for log_file in log_files:
        rows.extend(trainer_module.parse_train_log(log_file, args.train_dir))

    summary: dict[str, str | int] = {
        "lr": str(trial.lr),
        "val_lambda": str(trial.val_lambda),
        "out_dir": str(trial.out_dir),
        "log_files": str(len(log_files)),
        "rows": str(len(rows)),
        "status": "done" if rows else "no_log",
        "final_epoch": "",
        "final_test_accuracy": "",
        "final_test_value_accuracy": "",
        "final_swa_test_accuracy": "",
        "final_swa_test_value_accuracy": "",
        "final_test_loss_total": "",
        "best_metric": args.score,
        "best_score": "",
        "best_epoch": "",
        "best_source": "",
    }
    if not rows:
        return summary

    final = rows[-1]
    summary.update(
        {
            "final_epoch": final.epoch or "",
            "final_test_accuracy": final.test_accuracy[0],
            "final_test_value_accuracy": final.test_accuracy[1],
            "final_swa_test_accuracy": final.swa_test_accuracy[0],
            "final_swa_test_value_accuracy": final.swa_test_accuracy[1],
            "final_test_loss_total": final.test_loss[3],
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
        "out_dir",
        "status",
        "log_files",
        "rows",
        "final_epoch",
        "final_test_accuracy",
        "final_test_value_accuracy",
        "final_swa_test_accuracy",
        "final_swa_test_value_accuracy",
        "final_test_loss_total",
        "best_metric",
        "best_score",
        "best_epoch",
        "best_source",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args, extra_args = parse_args()
    trials = make_trials(args)
    summary_csv = args.summary_csv or args.model_root / "grid_summary.csv"

    if not args.summary_only:
        for index, trial in enumerate(trials, start=1):
            command = trainer_command(args, trial, extra_args)
            print(f"[{index}/{len(trials)}] lr={trial.lr} val_lambda={trial.val_lambda}")
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
