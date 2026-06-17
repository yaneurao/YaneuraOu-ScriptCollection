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
        help="directory to save wdoorYYYY.7z. Default: downloaded-kif/floodgate",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout in seconds. Default: 60")
    parser.add_argument(
        "--download-yesterday",
        action="store_true",
        help="also download yesterday's CSA files into output-dir/YYYYMMDD",
    )
    parser.add_argument(
        "--download-today",
        action="store_true",
        help="also download today's CSA files into output-dir/YYYYMMDD",
    )
    args = parser.parse_args(argv)

    try:
        stats = download_floodgate_kif(
            FloodgateDownloadJob(
                args.year,
                args.output_dir,
                download_yesterday=args.download_yesterday,
                download_today=args.download_today,
                timeout=args.timeout,
            ),
            log=lambda text: print(text, end=""),
        )
    except (FloodgateDownloadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    text = f"skipped={stats.skipped} downloaded={stats.bytes_written} destination={stats.destination}"
    if stats.yesterday is not None:
        text += (
            f" yesterday_found={stats.yesterday.found} yesterday_downloaded={stats.yesterday.downloaded}"
            f" yesterday_skipped={stats.yesterday.skipped} yesterday_failed={stats.yesterday.failed}"
            f" yesterday_dir={stats.yesterday.destination_dir}"
        )
    if stats.today is not None:
        text += (
            f" today_found={stats.today.found} today_downloaded={stats.today.downloaded}"
            f" today_skipped={stats.today.skipped} today_failed={stats.today.failed}"
            f" today_dir={stats.today.destination_dir}"
        )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
