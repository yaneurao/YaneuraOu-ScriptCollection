#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from kif_extractor_common import parse_date_value
from floodgate_kif_downloader_core import (
    FloodgateDailyDownloadJob,
    FloodgateDownloadError,
    download_floodgate_daily_kif,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download floodgate daily CSA files.")
    parser.add_argument("start_date", help="first target date. Example: 2026-06-19 or 2026/6/19")
    parser.add_argument("end_date", nargs="?", help="last target date. Defaults to start_date.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("downloaded-kif/floodgate-daily"),
        help="directory to save YYYYMMDD/*.csa. Default: downloaded-kif/floodgate-daily",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout in seconds. Default: 60")
    args = parser.parse_args(argv)

    try:
        start_date = parse_date_value(args.start_date, "開始日", year_only_month_day=(1, 1))
        end_date = parse_date_value(args.end_date or args.start_date, "終了日", year_only_month_day=(12, 31))
        if start_date is None or end_date is None:
            raise ValueError("開始日と終了日を指定してください。")
        stats = download_floodgate_daily_kif(
            FloodgateDailyDownloadJob(
                start_date=start_date,
                end_date=end_date,
                output_dir=args.output_dir,
                timeout=args.timeout,
            ),
            log=lambda text: print(text, end=""),
        )
    except (ValueError, FloodgateDownloadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"days={len(stats.days)} found={stats.found} downloaded={stats.downloaded} "
        f"skipped={stats.skipped} failed={stats.failed} bytes={stats.bytes_written} "
        f"output={stats.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
