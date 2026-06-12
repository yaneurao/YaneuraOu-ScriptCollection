#!/usr/bin/env python3
"""やねうら王定跡DB .db を やねうら王 バイナリ定跡DB .ybb へ変換します。

The converter is written for large books.  It creates sorted temporary runs and
then k-way merges them, so the whole book is not kept in memory at once.
"""

from __future__ import annotations

import argparse
import heapq
import os
import shutil
import struct
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import cshogi

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))
from YaneuraOuBookLib import (
    YBB_FLAG_MOVE_DEPTH as FLAG_MOVE_DEPTH,
    YBB_HEADER_STRUCT as HEADER_STRUCT,
    YBB_INDEX_STRUCT as INDEX_STRUCT,
    YBB_MAGIC as MAGIC,
    YBB_MOVE_DEPTH_STRUCT as MOVE_DEPTH_STRUCT,
    YBB_MOVE_STRUCT as MOVE_STRUCT,
    pack_sfen,
    trim_sfen_ply,
    usi_to_move16,
    ybb_path_from_output,
)

RUN_MAGIC = b"YBBRUN1\0"
RUN_HEADER_STRUCT = struct.Struct("<8sQ")
RUN_RECORD_STRUCT = struct.Struct("<32sHH")

DEFAULT_CHUNK_POSITIONS = 500_000
DEFAULT_CHUNK_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_OPEN_RUNS = 64

YbbRunRecord = tuple[bytes, int, bytes]


def parse_move_line(line: str) -> tuple[str, int, int] | None:
    if "," in line:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[1]
        depth_text = parts[2] if len(parts) > 2 else "0"
    else:
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[2]
        depth_text = parts[3] if len(parts) > 3 else "0"

    if eval_text.lower() == "none":
        return None

    value = int(eval_text)
    if value < -32768 or value > 32767:
        raise ValueError(f"eval is out of int16 range: {value}")
    depth = int(depth_text)
    if depth < 0 or depth > 65535:
        raise ValueError(f"depth is out of uint16 range: {depth}")
    return move_text, value, depth


class YbbRunWriter:
    def __init__(self, path: Path, record_count: int, move_record_size: int) -> None:
        self.path = path
        self.record_count = record_count
        self.move_record_size = move_record_size
        self.written = 0
        self.file: BinaryIO | None = None

    def __enter__(self) -> "YbbRunWriter":
        self.file = self.path.open("wb")
        self.file.write(RUN_HEADER_STRUCT.pack(RUN_MAGIC, self.record_count))
        return self

    def write(self, record: YbbRunRecord) -> None:
        if self.file is None:
            raise RuntimeError("run writer is not open")
        packed_sfen, ply, moves_blob = record
        move_count, remainder = divmod(len(moves_blob), self.move_record_size)
        if remainder != 0:
            raise ValueError("moves blob size is broken")
        if move_count > 65535:
            raise ValueError("too many moves in one position")
        if ply < 0 or ply > 65535:
            raise ValueError(f"ply is out of uint16 range: {ply}")
        self.file.write(RUN_RECORD_STRUCT.pack(packed_sfen, ply, move_count))
        self.file.write(moves_blob)
        self.written += 1

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()
        if exc_type is None and self.written != self.record_count:
            raise RuntimeError(
                f"run record count mismatch: expected {self.record_count}, wrote {self.written}"
            )


class YbbRunReader:
    def __init__(self, path: Path, move_record_size: int) -> None:
        self.path = path
        self.move_record_size = move_record_size
        self.file: BinaryIO | None = None
        self.record_count = 0
        self.remaining = 0

    def __enter__(self) -> "YbbRunReader":
        self.file = self.path.open("rb")
        header = self.file.read(RUN_HEADER_STRUCT.size)
        if len(header) != RUN_HEADER_STRUCT.size:
            raise ValueError(f"broken run header: {self.path}")
        magic, count = RUN_HEADER_STRUCT.unpack(header)
        if magic != RUN_MAGIC:
            raise ValueError(f"invalid run magic: {self.path}")
        self.record_count = count
        self.remaining = count
        return self

    def read_next(self) -> YbbRunRecord | None:
        if self.file is None:
            raise RuntimeError("run reader is not open")
        if self.remaining == 0:
            return None

        header = self.file.read(RUN_RECORD_STRUCT.size)
        if len(header) != RUN_RECORD_STRUCT.size:
            raise ValueError(f"broken run record: {self.path}")
        packed_sfen, ply, move_count = RUN_RECORD_STRUCT.unpack(header)
        moves_blob = self.file.read(move_count * self.move_record_size)
        if len(moves_blob) != move_count * self.move_record_size:
            raise ValueError(f"broken run move records: {self.path}")
        self.remaining -= 1
        return packed_sfen, ply, moves_blob

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()


def read_ybb_run_count(path: Path) -> int:
    with path.open("rb") as f:
        header = f.read(RUN_HEADER_STRUCT.size)
    if len(header) != RUN_HEADER_STRUCT.size:
        raise ValueError(f"broken run header: {path}")
    magic, count = RUN_HEADER_STRUCT.unpack(header)
    if magic != RUN_MAGIC:
        raise ValueError(f"invalid run magic: {path}")
    return int(count)


def iter_merged_ybb_records(readers: list[YbbRunReader]):
    heap: list[tuple[bytes, int, YbbRunRecord]] = []

    for index, reader in enumerate(readers):
        record = reader.read_next()
        if record is not None:
            heapq.heappush(heap, (record[0], index, record))

    previous_key: bytes | None = None
    while heap:
        packed_sfen, reader_index, record = heapq.heappop(heap)
        if previous_key == packed_sfen:
            raise ValueError(f"duplicated packed sfen: {packed_sfen.hex()}")
        previous_key = packed_sfen
        yield record

        next_record = readers[reader_index].read_next()
        if next_record is not None:
            heapq.heappush(heap, (next_record[0], reader_index, next_record))


def write_ybb_run(records: list[YbbRunRecord], path: Path, move_record_size: int) -> None:
    records.sort(key=lambda item: item[0])
    previous_key: bytes | None = None
    for packed_sfen, _, _ in records:
        if previous_key == packed_sfen:
            raise ValueError(f"duplicated packed sfen in one chunk: {packed_sfen.hex()}")
        previous_key = packed_sfen

    with YbbRunWriter(path, len(records), move_record_size) as writer:
        for record in records:
            writer.write(record)


def merge_ybb_runs_to_run(run_paths: list[Path], output_path: Path, move_record_size: int) -> None:
    total = sum(read_ybb_run_count(path) for path in run_paths)
    with ExitStack() as stack:
        readers = [stack.enter_context(YbbRunReader(path, move_record_size)) for path in run_paths]
        writer = stack.enter_context(YbbRunWriter(output_path, total, move_record_size))
        for record in iter_merged_ybb_records(readers):
            writer.write(record)


def reduce_ybb_runs(run_paths: list[Path], work_dir: Path, max_open_runs: int, move_record_size: int) -> list[Path]:
    if max_open_runs < 2:
        raise ValueError("--max-open-runs must be at least 2")

    stage = 0
    current = run_paths
    while len(current) > max_open_runs:
        next_runs: list[Path] = []
        for group_index, start in enumerate(range(0, len(current), max_open_runs)):
            group = current[start : start + max_open_runs]
            if len(group) == 1:
                next_runs.append(group[0])
                continue

            output_path = work_dir / f"merge-{stage:02d}-{group_index:06d}.run"
            print(f"merge runs: {len(group)} -> {output_path}")
            merge_ybb_runs_to_run(group, output_path, move_record_size)
            next_runs.append(output_path)
            for path in group:
                path.unlink(missing_ok=True)

        current = next_runs
        stage += 1

    return current


def write_final_ybb(run_paths: list[Path], output_base: Path, flags: int, move_record_size: int) -> None:
    output_path = ybb_path_from_output(output_base)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_tmp = output_path.with_name(output_path.name + ".tmp")
    total = sum(read_ybb_run_count(path) for path in run_paths)
    index_size = HEADER_STRUCT.size + total * INDEX_STRUCT.size

    try:
        with output_tmp.open("w+b") as output_file:
            output_file.write(HEADER_STRUCT.pack(MAGIC, total, flags))
            index_offset = HEADER_STRUCT.size
            move_offset = 0

            if run_paths:
                with ExitStack() as stack:
                    readers = [stack.enter_context(YbbRunReader(path, move_record_size)) for path in run_paths]
                    for packed_sfen, ply, moves_blob in iter_merged_ybb_records(readers):
                        move_count = len(moves_blob) // move_record_size
                        output_file.seek(index_size + move_offset)
                        output_file.write(moves_blob)
                        output_file.seek(index_offset)
                        output_file.write(INDEX_STRUCT.pack(packed_sfen, move_offset, ply, move_count))
                        index_offset += INDEX_STRUCT.size
                        move_offset += len(moves_blob)

        os.replace(output_tmp, output_path)
    except Exception:
        output_tmp.unlink(missing_ok=True)
        raise


def flush_chunk(
    records: list[YbbRunRecord],
    run_paths: list[Path],
    work_dir: Path,
    run_index: int,
    move_record_size: int,
) -> int:
    if not records:
        return run_index
    run_path = work_dir / f"db-to-ybb-{run_index:06d}.run"
    print(f"write run: {run_path} ({len(records)} positions)")
    write_ybb_run(records, run_path, move_record_size)
    run_paths.append(run_path)
    records.clear()
    return run_index + 1


def convert_db_to_ybb(
    input_db: Path,
    output_base: Path,
    work_dir: Path,
    chunk_positions: int,
    chunk_bytes: int,
    max_open_runs: int,
    include_depth: bool,
) -> None:
    move_struct = MOVE_DEPTH_STRUCT if include_depth else MOVE_STRUCT
    move_record_size = move_struct.size
    flags = FLAG_MOVE_DEPTH if include_depth else 0
    run_paths: list[Path] = []
    chunk_records: list[YbbRunRecord] = []
    chunk_estimated_bytes = 0
    run_index = 0
    total_positions = 0

    current_packed_sfen: bytes | None = None
    current_ply = 1
    current_moves: list[bytes] = []
    current_sfen = ""
    board = cshogi.Board()

    def finish_current() -> None:
        nonlocal chunk_estimated_bytes, run_index, total_positions
        nonlocal current_packed_sfen, current_ply, current_moves, current_sfen

        if current_packed_sfen is None:
            return
        if current_moves:
            moves_blob = b"".join(current_moves)
            chunk_records.append((current_packed_sfen, current_ply, moves_blob))
            chunk_estimated_bytes += 32 + 2 + 2 + len(moves_blob)
            total_positions += 1

            if len(chunk_records) >= chunk_positions or chunk_estimated_bytes >= chunk_bytes:
                run_index = flush_chunk(chunk_records, run_paths, work_dir, run_index, move_record_size)
                chunk_estimated_bytes = 0

        current_packed_sfen = None
        current_ply = 1
        current_moves = []
        current_sfen = ""

    with input_db.open("r", encoding="utf-8-sig", errors="replace") as f:
        for line_number, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue

            if line.startswith("sfen "):
                finish_current()
                current_sfen, current_ply = trim_sfen_ply(line[5:])
                if current_ply < 0 or current_ply > 65535:
                    raise ValueError(f"line {line_number}: ply is out of uint16 range: {current_ply}")
                board.set_sfen(f"{current_sfen} {current_ply}")
                current_packed_sfen = pack_sfen(board)
                continue

            if current_packed_sfen is None:
                raise ValueError(f"line {line_number}: move line appears before sfen line: {line}")

            parsed = parse_move_line(line)
            if parsed is None:
                continue
            move_text, value, depth = parsed
            move16 = usi_to_move16(board, move_text)
            if include_depth:
                current_moves.append(move_struct.pack(move16, value, depth))
            else:
                current_moves.append(move_struct.pack(move16, value))
            if len(current_moves) > 65535:
                raise ValueError(f"line {line_number}: too many moves: {current_sfen}")

    finish_current()
    flush_chunk(chunk_records, run_paths, work_dir, run_index, move_record_size)

    print(f"read positions: {total_positions}")
    run_paths = reduce_ybb_runs(run_paths, work_dir, max_open_runs, move_record_size)
    write_final_ybb(run_paths, output_base, flags, move_record_size)


def make_work_dir(tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="convert-db-to-ybb-", dir=tmp_dir))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="やねうら王定跡DB .db を やねうら王 バイナリ定跡DB .ybb へ変換します。"
    )
    parser.add_argument("input_db", type=Path)
    parser.add_argument(
        "output_base",
        type=Path,
        help="output ybb path. .ybb suffix is optional.",
    )
    parser.add_argument("--tmp-dir", type=Path, default=Path("tmp"))
    parser.add_argument("--chunk-positions", type=int, default=DEFAULT_CHUNK_POSITIONS)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--max-open-runs", type=int, default=DEFAULT_MAX_OPEN_RUNS)
    parser.add_argument(
        "--no-depth",
        action="store_true",
        help="do not write move depth records. default keeps depth.",
    )
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    if args.chunk_positions <= 0:
        raise ValueError("--chunk-positions must be positive")
    if args.chunk_bytes <= 0:
        raise ValueError("--chunk-bytes must be positive")

    work_dir = make_work_dir(args.tmp_dir)
    try:
        convert_db_to_ybb(
            args.input_db,
            args.output_base,
            work_dir,
            args.chunk_positions,
            args.chunk_bytes,
            args.max_open_runs,
            not args.no_depth,
        )
    finally:
        if args.keep_temp:
            print(f"keep temp: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(f"wrote: {ybb_path_from_output(args.output_base)}")


if __name__ == "__main__":
    main()
