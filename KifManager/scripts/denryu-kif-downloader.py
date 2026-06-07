#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# CLI entry point. The downloader implementation is in denryu_kif_downloader_core.py.
from denryu_kif_downloader_core import (
    DENRYU_DEFAULT_OUTPUT_DIR,
    DenryuDownloadError,
    DenryuDownloadJob,
    download_denryu_kif,
    fallback_denryu_tournament_options,
    fetch_denryu_tournament_options,
)


def parse_interval_argument(value: str) -> float:
    raw = value.strip()
    bypass_limit = raw.startswith("!")
    if bypass_limit:
        raw = raw[1:].strip()

    try:
        interval = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number.") from exc

    if interval < 0:
        raise argparse.ArgumentTypeError("interval must be 0 or greater.")
    if interval < 2 and not bypass_limit:
        raise argparse.ArgumentTypeError("interval must be 2 or greater.")
    return interval


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download Denryu-sen kifu files.")
    parser.add_argument(
        "source",
        nargs="?",
        help="Denryu-sen live page URL, archive URL, or tournament key such as dr6_production.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path(DENRYU_DEFAULT_OUTPUT_DIR),
        help=f"parent directory to save kifu files. Default: {DENRYU_DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="download CSA files from live kifulist.txt instead of the official archive ZIP.",
    )
    parser.add_argument(
        "--interval",
        type=parse_interval_argument,
        default=10.0,
        help="seconds to wait between accesses. Must be 2 or greater. Default: 10",
    )
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing files.")
    parser.add_argument("--timeout", type=float, default=60.0, help="network timeout in seconds. Default: 60")
    parser.add_argument(
        "--list-tournaments",
        action="store_true",
        help="print known tournaments from the official link page and exit.",
    )
    args = parser.parse_args(argv)

    if args.list_tournaments:
        try:
            options = fetch_denryu_tournament_options(timeout=args.timeout)
        except Exception:
            options = fallback_denryu_tournament_options()
        for option in options:
            print(f"{option.key}\t{option.title}\t{option.preferred_url}")
        return 0

    if not args.source:
        parser.error("source is required unless --list-tournaments is specified.")

    try:
        stats = download_denryu_kif(
            DenryuDownloadJob(
                args.source,
                args.output_dir,
                args.interval,
                overwrite=args.overwrite,
                timeout=args.timeout,
                use_live_page=args.live,
            ),
            log=lambda text: print(text, end=""),
        )
    except (DenryuDownloadError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"mode={stats.mode} tournament={stats.tournament} found={stats.found} "
        f"downloaded={stats.downloaded} skipped={stats.skipped} output={stats.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
