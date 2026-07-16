#!/usr/bin/env python3
"""
Concatenate HCPE3 files from one folder by fixed-size file groups.

HCPE3 files are sequences of game records and do not have a whole-file header,
so concatenating complete HCPE3 files is valid.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


COPY_BUFFER_SIZE = 16 * 1024 * 1024


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def collect_files(source_dir: Path, output_dir: Path, pattern: str, recursive: bool) -> list[Path]:
    files = source_dir.rglob(pattern) if recursive else source_dir.glob(pattern)
    resolved_output_dir = output_dir.resolve()
    return sorted(
        path
        for path in files
        if path.is_file() and not is_relative_to(path.resolve(), resolved_output_dir)
    )


def chunks(items: list[Path], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def concat_files(input_files: list[Path], output_file: Path) -> int:
    bytes_written = 0
    with output_file.open("wb") as out:
        for input_file in input_files:
            bytes_written += input_file.stat().st_size
            with input_file.open("rb") as src:
                shutil.copyfileobj(src, out, length=COPY_BUFFER_SIZE)
    return bytes_written


def make_output_path(output_dir: Path, prefix: str, index: int, digits: int) -> Path:
    return output_dir / f"{prefix}-{index:0{digits}d}.hcpe3"


def parse_source_and_group_size(args: argparse.Namespace) -> tuple[Path, int]:
    if len(args.source) == 1:
        if args.group_size is None:
            raise ValueError("--group-size is required when --source has no count")
        source_dir = Path(args.source[0])
        group_size = args.group_size
    elif len(args.source) == 2:
        if args.group_size is not None:
            raise ValueError("group size is specified twice")
        source_dir = Path(args.source[0])
        try:
            group_size = int(args.source[1])
        except ValueError as exc:
            raise ValueError(f"group size must be an integer: {args.source[1]}") from exc
    else:
        raise ValueError("--source must be DIR or DIR COUNT")

    if group_size <= 0:
        raise ValueError("group size must be positive")
    return source_dir, group_size


def write_manifest_header(manifest, group_size: int) -> None:
    columns = ["output", "bytes", "files"]
    columns.extend(f"input{i}" for i in range(1, group_size + 1))
    manifest.write("\t".join(columns) + "\n")


def write_manifest_row(manifest, output_file: Path, bytes_written: int, input_files: list[Path], group_size: int) -> None:
    columns = [str(output_file), str(bytes_written), str(len(input_files))]
    columns.extend(str(path) for path in input_files)
    columns.extend("" for _ in range(group_size - len(input_files)))
    manifest.write("\t".join(columns) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Concatenate HCPE3 files from one folder by fixed-size file groups."
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    parser.add_argument(
        "-s",
        "--source",
        nargs="+",
        required=True,
        metavar="VALUE",
        help="source folder, optionally followed by group size",
    )
    parser.add_argument(
        "-n",
        "--group-size",
        type=int,
        help="number of input files per output file",
    )
    parser.add_argument("--pattern", default="*.hcpe3", help="input filename pattern")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect input files from the source folder",
    )
    parser.add_argument("--prefix", default="merged", help="output filename prefix")
    parser.add_argument(
        "--digits",
        type=int,
        default=5,
        help="zero-padding width for output file numbers",
    )
    parser.add_argument(
        "--drop-remainder",
        action="store_true",
        help="do not write the last group when it has fewer than group size files",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="do not write the manifest TSV file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow overwriting existing output and manifest files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.digits <= 0:
        raise ValueError("--digits must be positive")

    source_dir, group_size = parse_source_and_group_size(args)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source folder not found: {source_dir}")
    if source_dir.resolve() == args.output.resolve():
        raise ValueError("source and --output must be different folders")

    input_files = collect_files(source_dir, args.output, args.pattern, args.recursive)
    if not input_files:
        raise FileNotFoundError(f"no input files found in {source_dir}: {args.pattern}")

    output_groups = [
        group
        for group in chunks(input_files, group_size)
        if not args.drop_remainder or len(group) == group_size
    ]
    if not output_groups:
        raise ValueError("no output files can be written from the specified source")

    output_files = [
        make_output_path(args.output, args.prefix, i, args.digits)
        for i in range(1, len(output_groups) + 1)
    ]
    manifest_path = args.output / f"{args.prefix}-manifest.tsv"

    existing = [path for path in output_files if path.exists()]
    if not args.no_manifest and manifest_path.exists():
        existing.append(manifest_path)
    if existing and not args.force:
        raise FileExistsError(
            "output already exists; use --force to overwrite: " + str(existing[0])
        )

    args.output.mkdir(parents=True, exist_ok=True)

    manifest = None
    if not args.no_manifest:
        manifest = manifest_path.open("w", encoding="utf-8", newline="")
        write_manifest_header(manifest, group_size)

    try:
        total_bytes = 0
        for output_file, group in zip(output_files, output_groups):
            bytes_written = concat_files(group, output_file)
            total_bytes += bytes_written
            if manifest is not None:
                write_manifest_row(manifest, output_file, bytes_written, group, group_size)
            print(output_file, "files", len(group), "bytes", bytes_written)
    finally:
        if manifest is not None:
            manifest.close()

    print("input_files", len(input_files))
    print("output_files", len(output_files))
    print("bytes", total_bytes)


if __name__ == "__main__":
    main()
