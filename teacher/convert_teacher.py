#!/usr/bin/env python3
"""
Teacher-data format converter.

Input format is inferred from the input path. If the output path is a file,
the output format is inferred from its extension. If the output path is a
folder, --to is required.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherConvertLib import (  # noqa: E402
    convert_hcpe3_to_hcpe_file,
    convert_hcpe3_to_psv_file,
    convert_hcpe_to_psv_file,
    convert_pack_to_hcpe_file,
    convert_psv_to_hcpe_file,
)
from TeacherFormatLib import (  # noqa: E402
    ConvertStats,
    extension_of,
    has_extension,
    output_for_file,
)


CONVERTERS = {
    ("pack", "hcpe"): convert_pack_to_hcpe_file,
    ("hcpe", "psv"): convert_hcpe_to_psv_file,
    ("psv", "hcpe"): convert_psv_to_hcpe_file,
    ("hcpe3", "hcpe"): convert_hcpe3_to_hcpe_file,
    ("hcpe3", "psv"): convert_hcpe3_to_psv_file,
}

INPUT_FORMATS = sorted({src for src, _ in CONVERTERS})
OUTPUT_FORMATS = sorted({dst for _, dst in CONVERTERS})


def normalize_format(value: str | None) -> str | None:
    if value is None:
        return None
    return value.lower().lstrip(".")


def collect_input_files(input_path: Path, recursive: bool) -> tuple[str, str, list[Path]]:
    if has_extension(input_path):
        input_format = extension_of(input_path)
        if input_format not in INPUT_FORMATS:
            raise ValueError(f"unsupported input extension: .{input_format}")
        if not input_path.is_file():
            raise FileNotFoundError(f"input file not found: {input_path}")
        return "file", input_format, [input_path]

    if not input_path.is_dir():
        raise FileNotFoundError(f"input folder not found: {input_path}")

    pattern = "**/*" if recursive else "*"
    by_format: dict[str, list[Path]] = {fmt: [] for fmt in INPUT_FORMATS}
    for path in input_path.glob(pattern):
        if path.is_file():
            ext = extension_of(path)
            if ext in by_format:
                by_format[ext].append(path)

    found = {fmt: sorted(paths) for fmt, paths in by_format.items() if paths}
    if not found:
        raise FileNotFoundError(
            f"no teacher files found in {input_path}; expected one of: "
            + ", ".join(f"*.{fmt}" for fmt in INPUT_FORMATS)
        )
    if len(found) != 1:
        formats = ", ".join(f".{fmt}" for fmt in found)
        raise ValueError(f"multiple input formats found in {input_path}: {formats}")

    input_format, files = next(iter(found.items()))
    return "folder", input_format, files


def resolve_output_format(output_path: Path, to_format: str | None) -> tuple[str, str]:
    to_format = normalize_format(to_format)
    if to_format is not None and to_format not in OUTPUT_FORMATS:
        raise ValueError(f"unsupported --to format: {to_format}")

    if has_extension(output_path):
        output_format = extension_of(output_path)
        if output_format not in OUTPUT_FORMATS:
            raise ValueError(f"unsupported output extension: .{output_format}")
        if to_format is not None and to_format != output_format:
            raise ValueError(
                f"--to {to_format} does not match output extension .{output_format}"
            )
        return "file", output_format

    if to_format is None:
        raise ValueError("--to is required when --output is a folder")
    return "folder", to_format


def print_stats(prefix: str, stats: ConvertStats) -> None:
    if stats.games:
        print(
            f"{prefix}: files={stats.files}, games={stats.games}, "
            f"positions={stats.positions}"
        )
        return
    print(f"{prefix}: files={stats.files}, positions={stats.positions}")


def convert_to_single_file(
    converter,
    input_files: list[Path],
    output_path: Path,
    *,
    batch_size: int,
    no_progress: bool,
) -> ConvertStats:
    total = ConvertStats()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as output:
        for input_file in input_files:
            stats = converter(
                input_file,
                output,
                batch_size=batch_size,
                no_progress=no_progress,
            )
            total.add(stats)
    print_stats(str(output_path), total)
    return total


def convert_to_output_folder(
    converter,
    input_files: list[Path],
    input_root: Path,
    output_dir: Path,
    output_format: str,
    *,
    recursive: bool,
    batch_size: int,
    no_progress: bool,
) -> ConvertStats:
    total = ConvertStats()
    for input_file in input_files:
        output_path = output_for_file(
            input_file,
            input_root,
            output_dir,
            output_format,
            recursive,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as output:
            stats = converter(
                input_file,
                output,
                batch_size=batch_size,
                no_progress=no_progress,
            )
        total.add(stats)
        print_stats(f"{input_file} -> {output_path}", stats)

    print_stats("total", total)
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert teacher-data formats. Input format is inferred from "
            "--input. Output format is inferred from the output file extension, "
            "or from --to when --output is a folder."
        )
    )
    parser.add_argument("--input", "-i", type=Path, required=True, help="input file or folder")
    parser.add_argument("--output", "-o", type=Path, required=True, help="output file or folder")
    parser.add_argument(
        "--to",
        choices=OUTPUT_FORMATS,
        help="output format; required when --output is a folder",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="recursively collect input files when --input is a folder",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=65536,
        help="number of fixed-size records to process per chunk",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="disable tqdm progress bars",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be positive")

    input_mode, input_format, input_files = collect_input_files(args.input, args.recursive)
    output_mode, output_format = resolve_output_format(args.output, args.to)
    converter = CONVERTERS.get((input_format, output_format))
    if converter is None:
        raise ValueError(f"unsupported conversion: {input_format} -> {output_format}")

    print(f"conversion: {input_format} -> {output_format}")
    if output_mode == "file":
        convert_to_single_file(
            converter,
            input_files,
            args.output,
            batch_size=args.batch_size,
            no_progress=args.no_progress,
        )
        return

    convert_to_output_folder(
        converter,
        input_files,
        args.input if input_mode == "folder" else args.input.parent,
        args.output,
        output_format,
        recursive=args.recursive,
        batch_size=args.batch_size,
        no_progress=args.no_progress,
    )


if __name__ == "__main__":
    main()
