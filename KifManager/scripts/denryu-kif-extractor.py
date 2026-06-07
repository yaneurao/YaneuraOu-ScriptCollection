#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Sequence

from kif_extractor_common import add_common_arguments, print_stats, run_extractor


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract USI position commands from Denryu-sen game records."
    )
    add_common_arguments(parser)
    parser.add_argument(
        "--finalists-only",
        action="store_true",
        help="extract only games by players who appeared in the final/A-class league of each Denryu-sen production event.",
    )
    args = parser.parse_args(argv)

    stats = run_extractor(
        args.input_dir,
        args.output,
        args.both_player_list,
        args.either_player_list,
        None,
        source_kind="denryu",
        wcsc_finalists_only=args.finalists_only,
        reversal_threshold=args.reversal_threshold,
        require_rating=False,
        verbose=args.verbose,
    )
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
