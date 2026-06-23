#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# CLI entry point. The downloader implementation is in floodgate_kif_downloader_core.py.
from floodgate_kif_downloader_core import (
    FloodgateDownloadError,
    FloodgateDownloadJob,
    download_floodgate_kif,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download floodgate yearly kifu archives.")
    parser.add_argument("start_year", type=int, help="first target year. Specify 2008 or later.")
    parser.add_argument("end_year", type=int, nargs="?", help="last target year. Defaults to start_year.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("downloaded-kif/floodgate"),
        help="directory to save wdoorYYYY.7z. Default: downloaded-kif/floodgate",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout in seconds. Default: 60")
    args = parser.parse_args(argv)

    end_year = args.end_year if args.end_year is not None else args.start_year
    if args.start_year > end_year:
        print("error: start_year must be less than or equal to end_year", file=sys.stderr)
        return 1

    failed = False
    stats_list = []
    for year in range(args.start_year, end_year + 1):
        try:
            stats = download_floodgate_kif(
                FloodgateDownloadJob(
                    year,
                    args.output_dir,
                    timeout=args.timeout,
                ),
                log=lambda text: print(text, end=""),
            )
        except (FloodgateDownloadError, OSError) as exc:
            print(f"error: {year}: {exc}", file=sys.stderr)
            failed = True
            continue
        stats_list.append(stats)

        print(f"year={stats.year} skipped={stats.skipped} downloaded={stats.bytes_written} destination={stats.destination}")

    print(f"completed={len(stats_list)} failed={int(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
