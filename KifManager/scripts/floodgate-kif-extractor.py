#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Sequence

from kif_extractor_common import add_common_arguments, add_date_arguments, print_stats, run_extractor


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract USI position commands from floodgate CSA game records."
    )
    add_common_arguments(parser)
    add_date_arguments(parser)
    parser.add_argument(
        "--min-rating",
        type=float,
        default=None,
        help="minimum floodgate rating reached by both players during the selected period",
    )
    parser.add_argument(
        "--losing-player-min-rating",
        type=float,
        default=None,
        help="also extract games lost by a player who reached this floodgate rating during the selected period",
    )
    args = parser.parse_args(argv)

    stats = run_extractor(
        args.input_dir,
        args.output,
        args.both_player_list,
        args.either_player_list,
        args.min_rating,
        source_kind="floodgate",
        start_date=args.start_date,
        end_date=args.end_date,
        reversal_threshold=args.reversal_threshold,
        require_rating=args.min_rating is not None,
        losing_player_min_rating=args.losing_player_min_rating,
        verbose=args.verbose,
    )
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
