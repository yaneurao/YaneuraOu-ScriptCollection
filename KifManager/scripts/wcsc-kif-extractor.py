#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Sequence

from kif_extractor_common import add_common_arguments, add_year_arguments, print_stats, run_extractor


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract USI position commands from WCSC game records."
    )
    add_common_arguments(parser)
    add_year_arguments(parser)
    parser.add_argument(
        "--finalists-only",
        action="store_true",
        help="extract only games where either player reached the final of the same WCSC tournament",
    )
    args = parser.parse_args(argv)

    stats = run_extractor(
        args.input_dir,
        args.output,
        args.both_player_list,
        args.either_player_list,
        None,
        source_kind="wcsc",
        start_year=args.start_year,
        end_year=args.end_year,
        wcsc_finalists_only=args.finalists_only,
        require_rating=False,
        verbose=args.verbose,
    )
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
