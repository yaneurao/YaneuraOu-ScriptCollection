#!/usr/bin/env python3
"""
Shuffle fixed-size teacher files in a folder and split them into output files.

This script is intentionally out-of-core. It first shards records into temporary
bucket files using a deterministic key derived from the 32-byte packed position,
then loads one bucket at a time, shuffles that bucket in memory, and writes
split outputs.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
sys.path.insert(0, str(COMMON_LIB_DIR))

from TeacherFormatLib import (  # noqa: E402
    HCPE,
    HCPE_SIZE,
    PSV,
    PSV_SIZE,
    validate_fixed_record_file,
)


DEFAULT_POSITIONS = 10_000_000
DEFAULT_BUCKET_COUNT = 1024
DEFAULT_CHUNK_RECORDS = 1_000_000
DEFAULT_DIGITS = 5
UINT64_MASK = (1 << 64) - 1

FORMATS = {
    "hcpe": {
        "dtype": HCPE,
        "record_size": HCPE_SIZE,
        "position_field": "hcp",
    },
    "psv": {
        "dtype": PSV,
        "record_size": PSV_SIZE,
        "position_field": "sfen",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Out-of-core shuffle/split for a folder of fixed-size teacher files "
            "(.hcpe or .psv). Output files are written as "
            "<prefix>-00001.<format>, <prefix>-00002.<format>, ..."
        )
    )
    parser.add_argument("src_teacher_folder", type=Path, help="folder containing input .hcpe or .psv files")
    parser.add_argument("dst_teacher_folder", type=Path, help="folder for shuffled split teacher files")
    parser.add_argument(
        "--format",
        choices=sorted(FORMATS),
        help="input/output format. If omitted, infer from files in the source folder.",
    )
    parser.add_argument(
        "--positions",
        type=int,
        default=DEFAULT_POSITIONS,
        help=f"positions per output file (default: {DEFAULT_POSITIONS})",
    )
    parser.add_argument("--prefix", default="shuffled", help="output filename prefix (default: shuffled)")
    parser.add_argument(
        "--digits",
        type=int,
        default=DEFAULT_DIGITS,
        help=f"zero-padding width for output file numbers (default: {DEFAULT_DIGITS})",
    )
    parser.add_argument(
        "--bucket-count",
        type=int,
        default=DEFAULT_BUCKET_COUNT,
        help=f"temporary bucket count (default: {DEFAULT_BUCKET_COUNT})",
    )
    parser.add_argument(
        "--chunk-records",
        type=int,
        default=DEFAULT_CHUNK_RECORDS,
        help=f"input records to process per chunk (default: {DEFAULT_CHUNK_RECORDS})",
    )
    parser.add_argument("--seed", type=int, default=0, help="deterministic shuffle seed (default: 0)")
    parser.add_argument("--recursive", action="store_true", help="collect teacher files recursively")
    parser.add_argument("--tmp-dir", type=Path, help="temporary directory root")
    parser.add_argument("--keep-temp", action="store_true", help="keep temporary bucket files")
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow dst_teacher_folder to contain files and overwrite matching output files",
    )
    return parser.parse_args()


def infer_format(src_dir: Path, recursive: bool, requested_format: str | None) -> str:
    if requested_format is not None:
        return requested_format

    formats = []
    for fmt in sorted(FORMATS):
        pattern = f"**/*.{fmt}" if recursive else f"*.{fmt}"
        if any(p.is_file() for p in src_dir.glob(pattern)):
            formats.append(fmt)

    if not formats:
        raise FileNotFoundError(f"no .hcpe/.psv files found in: {src_dir}")
    if len(formats) > 1:
        raise ValueError(
            "source folder contains multiple teacher formats: "
            + ", ".join(f".{fmt}" for fmt in formats)
            + " (specify --format)"
        )
    return formats[0]


def collect_teacher_files(src_dir: Path, recursive: bool, fmt: str) -> list[Path]:
    if not src_dir.is_dir():
        raise FileNotFoundError(f"source folder not found: {src_dir}")
    pattern = f"**/*.{fmt}" if recursive else f"*.{fmt}"
    files = sorted(p for p in src_dir.glob(pattern) if p.is_file())
    if not files:
        raise FileNotFoundError(f"no .{fmt} files found in: {src_dir}")
    return files


def ensure_output_dir(path: Path, *, force: bool, prefix: str, fmt: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    existing_outputs = sorted(path.glob(f"{prefix}-*.{fmt}"))
    other_entries = [p for p in path.iterdir() if p.name != ".gitkeep" and p not in existing_outputs]
    if other_entries and not force:
        raise FileExistsError(f"destination folder is not empty: {path} (use --force to allow this)")
    if existing_outputs:
        if not force:
            raise FileExistsError(f"output files already exist in: {path} (use --force to overwrite)")
        for output in existing_outputs:
            output.unlink()


def packed_position_xor_keys(records: np.ndarray, position_field: str, seed: int) -> np.ndarray:
    packed_position = np.ascontiguousarray(records[position_field])
    words = packed_position.view("<u8").reshape(len(records), 4)
    keys = np.bitwise_xor.reduce(words, axis=1)
    if seed:
        keys = keys ^ np.uint64(seed & UINT64_MASK)
    return keys


def bucket_path(work_dir: Path, bucket: int, fmt: str) -> Path:
    return work_dir / f"bucket-{bucket:06}.{fmt}"


def shard_inputs(
    input_files: list[Path],
    work_dir: Path,
    *,
    fmt: str,
    dtype: np.dtype,
    record_size: int,
    position_field: str,
    bucket_count: int,
    chunk_records: int,
    seed: int,
) -> int:
    total_records = 0
    for file_index, path in enumerate(input_files, start=1):
        file_records = validate_fixed_record_file(path, record_size, fmt.upper())
        print(f"[shard] {file_index}/{len(input_files)} {path} ({file_records} positions)")
        total_records += file_records

        with path.open("rb") as f:
            while True:
                data = f.read(record_size * chunk_records)
                if not data:
                    break
                if len(data) % record_size != 0:
                    raise ValueError(f"truncated {fmt.upper()} chunk: {path}")

                records = np.frombuffer(data, dtype=dtype)
                keys = packed_position_xor_keys(records, position_field, seed)
                buckets = keys % np.uint64(bucket_count)

                order = np.argsort(buckets, kind="stable")
                sorted_buckets = buckets[order]
                sorted_records = records[order]
                split_points = np.flatnonzero(sorted_buckets[1:] != sorted_buckets[:-1]) + 1

                start = 0
                for end in list(split_points) + [len(sorted_records)]:
                    bucket = int(sorted_buckets[start])
                    with bucket_path(work_dir, bucket, fmt).open("ab") as out:
                        sorted_records[start:end].tofile(out)
                    start = end

    return total_records


class SplitWriter:
    def __init__(self, dst_dir: Path, prefix: str, positions: int, fmt: str, digits: int):
        self.dst_dir = dst_dir
        self.prefix = prefix
        self.positions = positions
        self.fmt = fmt
        self.digits = digits
        self.index = 0
        self.in_current = 0
        self.current = None
        self.paths: list[Path] = []

    def close(self) -> None:
        if self.current is not None:
            self.current.close()
            self.current = None

    def _open_next(self) -> None:
        self.close()
        self.index += 1
        self.in_current = 0
        path = self.dst_dir / f"{self.prefix}-{self.index:0{self.digits}d}.{self.fmt}"
        self.current = path.open("wb")
        self.paths.append(path)

    def write(self, records: np.ndarray) -> None:
        pos = 0
        while pos < len(records):
            if self.current is None or self.in_current >= self.positions:
                self._open_next()

            writable = min(len(records) - pos, self.positions - self.in_current)
            records[pos : pos + writable].tofile(self.current)
            self.in_current += writable
            pos += writable


def write_shuffled_outputs(
    work_dir: Path,
    dst_dir: Path,
    *,
    fmt: str,
    dtype: np.dtype,
    prefix: str,
    digits: int,
    bucket_count: int,
    positions: int,
    seed: int,
) -> list[Path]:
    rng = np.random.default_rng(seed)
    bucket_order = np.arange(bucket_count)
    rng.shuffle(bucket_order)

    writer = SplitWriter(dst_dir, prefix, positions, fmt, digits)
    try:
        for ordinal, bucket in enumerate(bucket_order, start=1):
            path = bucket_path(work_dir, int(bucket), fmt)
            if not path.is_file():
                continue

            records = np.fromfile(path, dtype=dtype)
            if len(records) == 0:
                continue
            rng.shuffle(records)
            print(f"[write] bucket {ordinal}/{bucket_count} id={int(bucket)} ({len(records)} positions)")
            writer.write(records)
    finally:
        writer.close()

    return writer.paths


def validate_args(args: argparse.Namespace) -> None:
    if args.positions <= 0:
        raise ValueError("--positions must be positive")
    if args.bucket_count <= 0:
        raise ValueError("--bucket-count must be positive")
    if args.chunk_records <= 0:
        raise ValueError("--chunk-records must be positive")
    if args.digits <= 0:
        raise ValueError("--digits must be positive")
    if not args.prefix:
        raise ValueError("--prefix must not be empty")


def main() -> None:
    args = parse_args()
    validate_args(args)

    fmt = infer_format(args.src_teacher_folder, args.recursive, args.format)
    format_info = FORMATS[fmt]
    dtype = format_info["dtype"]
    record_size = format_info["record_size"]
    position_field = format_info["position_field"]

    input_files = collect_teacher_files(args.src_teacher_folder, args.recursive, fmt)
    ensure_output_dir(args.dst_teacher_folder, force=args.force, prefix=args.prefix, fmt=fmt)

    tmp_root = args.tmp_dir if args.tmp_dir is not None else args.dst_teacher_folder
    tmp_root.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=".shuffle_split_teacher-", dir=tmp_root))

    print(f"format      : {fmt}")
    print(f"input files : {len(input_files)}")
    print(f"output dir  : {args.dst_teacher_folder}")
    print(f"positions   : {args.positions}")
    print(f"digits      : {args.digits}")
    print(f"buckets     : {args.bucket_count}")
    print(f"chunk       : {args.chunk_records}")
    print(f"seed        : {args.seed}")
    print(f"temp dir    : {work_dir}")

    try:
        total_records = shard_inputs(
            input_files,
            work_dir,
            fmt=fmt,
            dtype=dtype,
            record_size=record_size,
            position_field=position_field,
            bucket_count=args.bucket_count,
            chunk_records=args.chunk_records,
            seed=args.seed,
        )
        outputs = write_shuffled_outputs(
            work_dir,
            args.dst_teacher_folder,
            fmt=fmt,
            dtype=dtype,
            prefix=args.prefix,
            digits=args.digits,
            bucket_count=args.bucket_count,
            positions=args.positions,
            seed=args.seed,
        )
        print(f"done: {total_records} positions -> {len(outputs)} files")
        for path in outputs:
            print(path)
    finally:
        if args.keep_temp:
            print(f"kept temp dir: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
