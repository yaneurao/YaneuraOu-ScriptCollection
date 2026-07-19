#!/usr/bin/env python3
"""
Copy teacher files from multiple folders in round-robin filename order.

This is intended for trainer.py input folders where .hcpe and .hcpe3 files can
coexist and are consumed in sorted filename order.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil


@dataclass(frozen=True)
class SourceSpec:
    source_dir: Path
    patterns: list[str]
    files: list[Path]


@dataclass(frozen=True)
class OutputItem:
    index: int
    source_index: int
    input_file: Path
    output_file: Path


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def normalize_pattern(value: str) -> str:
    if value.startswith("."):
        return f"*{value}"
    return value


def parse_source_args(source_args: list[list[str]]) -> list[tuple[Path, list[str]]]:
    sources: list[tuple[Path, list[str]]] = []
    for values in source_args:
        if len(values) > 2:
            raise ValueError("--source must be DIR or DIR PATTERN")
        source_dir = Path(values[0])
        patterns = [normalize_pattern(values[1])] if len(values) == 2 else ["*.hcpe", "*.hcpe3"]
        sources.append((source_dir, patterns))
    return sources


def collect_files(source_dir: Path, output_dir: Path, patterns: list[str], recursive: bool) -> list[Path]:
    resolved_output_dir = output_dir.resolve()
    files: dict[Path, None] = {}
    for pattern in patterns:
        candidates = source_dir.rglob(pattern) if recursive else source_dir.glob(pattern)
        for path in candidates:
            if not path.is_file():
                continue
            if is_relative_to(path.resolve(), resolved_output_dir):
                continue
            files[path] = None
    return sorted(files)


def build_sources(args: argparse.Namespace) -> list[SourceSpec]:
    sources: list[SourceSpec] = []
    for source_dir, patterns in parse_source_args(args.source):
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source folder not found: {source_dir}")
        if source_dir.resolve() == args.output.resolve():
            raise ValueError("source folders and --output must be different folders")

        files = collect_files(source_dir, args.output, patterns, args.recursive)
        if not files:
            raise FileNotFoundError(
                f"no input files found in {source_dir}: {', '.join(patterns)}"
            )
        sources.append(SourceSpec(source_dir, patterns, files))
    return sources


def iter_round_robin(sources: list[SourceSpec], output_dir: Path, digits: int) -> list[OutputItem]:
    positions = [0 for _ in sources]
    items: list[OutputItem] = []
    index = 1

    while True:
        made_progress = False
        for source_index, source in enumerate(sources, start=1):
            position = positions[source_index - 1]
            if position >= len(source.files):
                continue
            input_file = source.files[position]
            output_file = output_dir / f"{index:0{digits}d}-{input_file.name}"
            items.append(OutputItem(index, source_index, input_file, output_file))
            positions[source_index - 1] += 1
            index += 1
            made_progress = True
        if not made_progress:
            break

    return items


def copy_item(item: OutputItem, method: str) -> None:
    item.output_file.parent.mkdir(parents=True, exist_ok=True)
    if method == "copy":
        shutil.copy2(item.input_file, item.output_file)
    elif method == "hardlink":
        os.link(item.input_file, item.output_file)
    else:
        raise ValueError(f"unknown copy method: {method}")


def write_manifest_header(manifest) -> None:
    manifest.write("index\toutput\tsource_index\tinput\tbytes\n")


def write_manifest_row(manifest, item: OutputItem) -> None:
    manifest.write(
        "\t".join(
            [
                str(item.index),
                str(item.output_file),
                str(item.source_index),
                str(item.input_file),
                str(item.input_file.stat().st_size),
            ]
        )
        + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy .hcpe/.hcpe3 teacher files from source folders in round-robin order."
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    parser.add_argument(
        "-s",
        "--source",
        nargs="+",
        action="append",
        required=True,
        metavar="VALUE",
        help="source folder, optionally followed by a glob pattern or extension such as .hcpe3",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect input files from each source folder",
    )
    parser.add_argument(
        "--digits",
        type=int,
        default=4,
        help="zero-padding width for output file numbers",
    )
    parser.add_argument(
        "--method",
        choices=("copy", "hardlink"),
        default="copy",
        help="how to create output files",
    )
    parser.add_argument(
        "--manifest",
        default="interleaved-manifest.tsv",
        help="manifest filename under the output folder",
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

    sources = build_sources(args)
    items = iter_round_robin(sources, args.output, args.digits)
    if len(str(len(items))) > args.digits:
        raise ValueError(
            f"--digits={args.digits} is too small for {len(items)} output files"
        )

    manifest_path = args.output / args.manifest
    existing = [item.output_file for item in items if item.output_file.exists()]
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
        write_manifest_header(manifest)

    try:
        total_bytes = 0
        for item in items:
            if item.output_file.exists():
                item.output_file.unlink()
            copy_item(item, args.method)
            size = item.output_file.stat().st_size
            total_bytes += size
            if manifest is not None:
                write_manifest_row(manifest, item)
            print(f"{item.output_file} <- {item.input_file} ({size} bytes)")
    finally:
        if manifest is not None:
            manifest.close()

    print("sources", len(sources))
    print("output_files", len(items))
    print("bytes", total_bytes)


if __name__ == "__main__":
    main()
