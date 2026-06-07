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
    parser = argparse.ArgumentParser(description="Download floodgate yearly kifu archive.")
    parser.add_argument("year", type=int, help="target year. Specify 2008 or later.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("downloaded-kif/floodgate"),
        help="directory to save wdoorYYYY.7z or current-year wdoorYYYY-YYYYMMDD.7z. Default: downloaded-kif/floodgate",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout in seconds. Default: 60")
    args = parser.parse_args(argv)

    try:
        stats = download_floodgate_kif(
            FloodgateDownloadJob(args.year, args.output_dir, timeout=args.timeout),
            log=lambda text: print(text, end=""),
        )
    except (FloodgateDownloadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"downloaded={stats.bytes_written} destination={stats.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
