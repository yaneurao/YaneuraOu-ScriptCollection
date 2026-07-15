#!/usr/bin/env python3
"""
Concatenate HCPE3 files from multiple folders in a round-robin pattern.

HCPE3 files are sequences of game records and do not have a whole-file header,
so concatenating complete HCPE3 files is valid.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil


@dataclass(frozen=True)
class SourceSpec:
    source_dir: Path
    take: int
    files: list[Path]


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def collect_files(src_dir: Path, output_dir: Path, pattern: str, recursive: bool) -> list[Path]:
    files = src_dir.rglob(pattern) if recursive else src_dir.glob(pattern)
    resolved_output_dir = output_dir.resolve()
    return sorted(
        path
        for path in files
        if path.is_file() and not is_relative_to(path.resolve(), resolved_output_dir)
    )


def concat_files(src_files: list[Path], output_file: Path) -> None:
    with output_file.open("wb") as out:
        for src_file in src_files:
            with src_file.open("rb") as src:
                shutil.copyfileobj(src, out, length=16 * 1024 * 1024)


def parse_sources(source_args: list[list[str]], output_dir: Path, pattern: str, recursive: bool) -> list[SourceSpec]:
    sources = []
    for source_dir_text, take_text in source_args:
        source_dir = Path(source_dir_text)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"source folder not found: {source_dir}")
        if source_dir.resolve() == output_dir.resolve():
            raise ValueError("source folders and --output must be different folders")

        try:
            take = int(take_text)
        except ValueError as exc:
            raise ValueError(f"source take count must be an integer: {take_text}") from exc
        if take <= 0:
            raise ValueError(f"source take count must be positive: {source_dir} {take}")

        files = collect_files(source_dir, output_dir, pattern, recursive)
        if not files:
            raise FileNotFoundError(f"no input files found in {source_dir}: {pattern}")
        sources.append(SourceSpec(source_dir=source_dir, take=take, files=files))

    return sources


def make_output_path(output_dir: Path, prefix: str, index: int, digits: int) -> Path:
    return output_dir / f"{prefix}-{index:0{digits}d}.hcpe3"


def selected_slots_for_output(sources: list[SourceSpec], output_index: int) -> list[list[Path | None]]:
    selected = []
    for source in sources:
        start = output_index * source.take
        source_slots = []
        for offset in range(source.take):
            input_index = start + offset
            if input_index < len(source.files):
                source_slots.append(source.files[input_index])
            else:
                source_slots.append(None)
        selected.append(source_slots)
    return selected


def flatten_selected_slots(selected_slots: list[list[Path | None]]) -> list[Path]:
    return [
        path
        for source_slots in selected_slots
        for path in source_slots
        if path is not None
    ]


def write_manifest_header(manifest, sources: list[SourceSpec]) -> None:
    columns = ["output"]
    for source_index, source in enumerate(sources, start=1):
        columns.extend(
            f"source{source_index}_{slot_index}"
            for slot_index in range(1, source.take + 1)
        )
    manifest.write("\t".join(columns) + "\n")


def write_manifest_row(
    manifest,
    output_file: Path,
    selected_slots: list[list[Path | None]],
) -> None:
    columns = [str(output_file)]
    columns.extend(
        "" if path is None else str(path)
        for source_slots in selected_slots
        for path in source_slots
    )
    manifest.write("\t".join(columns) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Concatenate HCPE3 files from multiple folders. For each output, "
            "the script takes the requested number of files from each --source "
            "in the order the sources are specified."
        )
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    parser.add_argument(
        "--source",
        nargs=2,
        action="append",
        metavar=("DIR", "COUNT"),
        required=True,
        help="source folder and number of files to take per output",
    )
    parser.add_argument("--pattern", default="*.hcpe3", help="input filename pattern")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect input files from each source folder",
    )
    parser.add_argument("--prefix", default="mixed", help="output filename prefix")
    parser.add_argument(
        "--digits",
        type=int,
        default=5,
        help="zero-padding width for output file numbers",
    )
    parser.add_argument(
        "--max-outputs",
        type=int,
        help="maximum number of output files to write",
    )
    parser.add_argument(
        "--allow-partial-last",
        action="store_true",
        help="write one final partial output when complete groups leave remaining files",
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
    if args.max_outputs is not None and args.max_outputs <= 0:
        raise ValueError("--max-outputs must be positive")

    sources = parse_sources(args.source, args.output, args.pattern, args.recursive)

    natural_complete_outputs = min(len(source.files) // source.take for source in sources)
    complete_outputs = natural_complete_outputs
    if args.max_outputs is not None:
        complete_outputs = min(complete_outputs, args.max_outputs)

    write_partial_last = False
    if args.allow_partial_last:
        can_write_more = args.max_outputs is None or complete_outputs < args.max_outputs
        has_remainder = any(
            len(source.files) > complete_outputs * source.take
            for source in sources
        )
        write_partial_last = can_write_more and complete_outputs == natural_complete_outputs and has_remainder

    total_outputs = complete_outputs + (1 if write_partial_last else 0)
    if total_outputs == 0:
        raise ValueError("no output files can be written from the specified sources")

    output_files = [
        make_output_path(args.output, args.prefix, i, args.digits)
        for i in range(1, total_outputs + 1)
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
        write_manifest_header(manifest, sources)

    try:
        for output_index, output_file in enumerate(output_files):
            selected_slots = selected_slots_for_output(sources, output_index)
            selected_files = flatten_selected_slots(selected_slots)
            concat_files(selected_files, output_file)
            if manifest is not None:
                write_manifest_row(manifest, output_file, selected_slots)
            print(output_file, len(selected_files))
    finally:
        if manifest is not None:
            manifest.close()

    for i, source in enumerate(sources, start=1):
        used = min(len(source.files), total_outputs * source.take)
        print(f"source{i}", source.source_dir, "files", len(source.files), "used", used)
    print("outputs", total_outputs)


if __name__ == "__main__":
    main()
