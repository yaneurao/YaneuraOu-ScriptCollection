#!/usr/bin/env python3
import argparse
from pathlib import Path

import numpy as np


PsvRecord = np.dtype([
    ("sfen", np.uint8, 32),
    ("score", "<i2"),
    ("move", "<u2"),
    ("gamePly", "<u2"),
    ("game_result", "i1"),
    ("padding", "u1"),
])

PSV_RECORD_SIZE = 40

if PsvRecord.itemsize != PSV_RECORD_SIZE:
    raise RuntimeError(f"PsvRecord size must be {PSV_RECORD_SIZE} bytes")


def load_psv(path: Path) -> np.ndarray:
    file_size = path.stat().st_size
    if file_size % PSV_RECORD_SIZE != 0:
        raise RuntimeError(
            f"{path}: file size {file_size} is not a multiple of {PSV_RECORD_SIZE}"
        )
    return np.fromfile(path, dtype=PsvRecord)


def make_output_path(outpath: Path, index) -> Path:
    if index is None:
        return outpath
    return outpath.with_name(f"{outpath.stem}-{index:03}{outpath.suffix}")


def write_part(records: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records.tofile(path)
    print(path, len(records))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split and optionally shuffle PSV(PsvRecord/PackedSfenValue) files."
    )
    parser.add_argument("psv", type=Path, nargs="+", help="input .psv file(s)")
    parser.add_argument("--outpath", type=Path, help="output .psv path or split filename base")
    parser.add_argument("--split", type=int, help="number of output files")
    parser.add_argument("--positions", type=int, help="records per output file")
    parser.add_argument("--shuffle", action="store_true", help="shuffle records before writing")
    parser.add_argument("--seed", type=int, help="random seed for --shuffle")
    parser.add_argument("--uniq", action="store_true", help="deduplicate records before writing")
    parser.add_argument(
        "--uniq_each_split",
        action="store_true",
        help="deduplicate each output part after splitting",
    )
    args = parser.parse_args()

    if args.split is not None and args.positions is not None:
        parser.error("--split and --positions cannot be specified together")
    if args.split is not None and args.split <= 0:
        parser.error("--split must be positive")
    if args.positions is not None and args.positions <= 0:
        parser.error("--positions must be positive")

    arrays = [load_psv(path) for path in args.psv]
    records = arrays[0] if len(arrays) == 1 else np.concatenate(arrays)
    original_len = len(records)

    if args.uniq:
        records = np.unique(records)
        print(args.psv, original_len, len(records))
    else:
        print(args.psv, original_len)

    if args.shuffle:
        if args.seed is None:
            np.random.shuffle(records)
        else:
            rng = np.random.default_rng(args.seed)
            rng.shuffle(records)

    outpath = args.outpath if args.outpath is not None else args.psv[0]
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

        output_index = i + 1 if split_requested or args.outpath is None else None
        write_part(part, make_output_path(outpath, output_index))
        pos = pos_next


if __name__ == "__main__":
    main()
