#!/usr/bin/env python3
"""Entry point for the YO-Build MVP GUI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lib.app import run_gui
from lib.planner import create_plan, validate_plan
from lib.presets import PRESET_NAMES, create_preset
from lib.script_writer import write_build_run


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YO-Build GUI and script generator")
    parser.add_argument(
        "--generate-preset",
        choices=PRESET_NAMES,
        help="Generate scripts from a built-in preset without opening the GUI.",
    )
    parser.add_argument(
        "--run-root",
        help="Output directory for generated run folders. Defaults to YO-Build/runs.",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="Print the generated build plan as JSON when used with --generate-preset.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    yobuild_root = Path(__file__).resolve().parents[1]

    if args.generate_preset:
        recipe = create_preset(args.generate_preset, yobuild_root)
        if args.run_root:
            recipe["run_root"] = args.run_root
        plan = create_plan(recipe)
        warnings = validate_plan(recipe, plan)
        run_dir = write_build_run(recipe, plan, yobuild_root)
        if args.print_plan:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        print(run_dir)
        return 0

    run_gui(yobuild_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
