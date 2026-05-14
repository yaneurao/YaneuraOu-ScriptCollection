#!/usr/bin/env python3
"""
Split and optionally shuffle fixed-size teacher-data records.

Supported formats are PSV and HCPE. Both are fixed-size position records, so
they can be split, concatenated, shuffled, and deduplicated by record.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherFormatLib import (  # noqa: E402
    HCPE,
    HCPE_SIZE,
    PSV,
    PSV_SIZE,
    extension_of,
    validate_fixed_record_file,
)


FORMATS = {
    "hcpe": (HCPE, HCPE_SIZE),
    "psv": (PSV, PSV_SIZE),
}


def infer_input_format(paths: list[Path]) -> str:
    formats = {extension_of(path) for path in paths}
    unsupported = sorted(fmt for fmt in formats if fmt not in FORMATS)
    if unsupported:
        raise ValueError(f"unsupported input extension: .{unsupported[0]}")
    if len(formats) != 1:
        raise ValueError(
            "all input files must have the same teacher format extension: "
            + ", ".join(f".{fmt}" for fmt in sorted(formats))
        )
    return next(iter(formats))


def load_records(path: Path, fmt: str) -> np.ndarray:
    dtype, record_size = FORMATS[fmt]
    validate_fixed_record_file(path, record_size, fmt.upper())
    return np.fromfile(path, dtype=dtype)


def resolve_output_path(output: Path | None, first_input: Path, fmt: str) -> Path:
    if output is None:
        return first_input
    if output.suffix == "":
        return output.with_suffix(f".{fmt}")
    if extension_of(output) != fmt:
        raise ValueError(f"--output extension must be .{fmt}: {output}")
    return output


def make_output_path(output: Path, index: int | None) -> Path:
    if index is None:
        return output
    return output.with_name(f"{output.stem}-{index:03}{output.suffix}")


def write_part(records: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records.tofile(path)
    print(path, len(records))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split and optionally shuffle fixed-size teacher-data files "
            "(.psv or .hcpe)."
        )
    )
    parser.add_argument("input", type=Path, nargs="+", help="input .psv or .hcpe file(s)")
    parser.add_argument(
        "--output",
        "--outpath",
        dest="output",
        type=Path,
        help="output path or split filename base",
    )
    parser.add_argument("--split", type=int, help="number of output files")
    parser.add_argument("--positions", type=int, help="records per output file")
    parser.add_argument("--shuffle", action="store_true", help="shuffle records before writing")
    parser.add_argument("--seed", type=int, help="random seed for --shuffle")
    parser.add_argument("--uniq", action="store_true", help="deduplicate records before writing")
    parser.add_argument(
        "--uniq-each-split",
        "--uniq_each_split",
        action="store_true",
        help="deduplicate each output part after splitting",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.split is not None and args.positions is not None:
        raise ValueError("--split and --positions cannot be specified together")
    if args.split is not None and args.split <= 0:
        raise ValueError("--split must be positive")
    if args.positions is not None and args.positions <= 0:
        raise ValueError("--positions must be positive")

    fmt = infer_input_format(args.input)
    output = resolve_output_path(args.output, args.input[0], fmt)

    arrays = [load_records(path, fmt) for path in args.input]
    records = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
    original_len = len(records)

    if args.uniq:
        records = np.unique(records)
        print(args.input, original_len, len(records))
    else:
        print(args.input, original_len)

    if args.shuffle:
        if args.seed is None:
            np.random.shuffle(records)
        else:
            rng = np.random.default_rng(args.seed)
            rng.shuffle(records)

    split_requested = args.split is not None or args.positions is not None

    if args.split is not None:
        chunk_size = (len(records) + args.split - 1) // args.split
        num_parts = args.split if len(records) > 0 else 1
    elif args.positions is not None:
        chunk_size = args.positions
        num_parts = (len(records) + chunk_size - 1) // chunk_size if len(records) > 0 else 1
    else:
        chunk_size = len(records)
        num_parts = 1

    pos = 0
    for i in range(num_parts):
        pos_next = min(pos + chunk_size, len(records))
        if i > 0 and pos >= len(records):
            break

        part = records[pos:pos_next]
        if args.uniq_each_split:
            before = len(part)
            part = np.unique(part)
            print("uniq_each_split", before, len(part))

        output_index = i + 1 if split_requested or args.output is None else None
        write_part(part, make_output_path(output, output_index))
        pos = pos_next


if __name__ == "__main__":
    main()
