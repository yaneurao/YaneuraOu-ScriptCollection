#!/usr/bin/env python3
"""やねうら王 バイナリ定跡DB .ybb を やねうら王定跡DB .db へ変換します。

The converter external-sorts by SFEN, so the output is deterministic without
loading the whole book into memory.
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

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))
from YaneuraOuBookLib import (
    YANEURAOU_BOOK_HEADER_V1,
    YBB_FLAG_MOVE_DEPTH as FLAG_MOVE_DEPTH,
    YBB_HEADER_STRUCT as HEADER_STRUCT,
    YBB_INDEX_STRUCT as INDEX_STRUCT,
    YBB_MOVE_DEPTH_STRUCT as MOVE_DEPTH_STRUCT,
    YBB_MOVE_STRUCT as MOVE_STRUCT,
    board_from_packed_sfen,
    move16_to_usi,
    read_ybb_header,
    resolve_ybb_input,
    trim_number,
)

RUN_MAGIC = b"DBRUN1\0\0"
RUN_HEADER_STRUCT = struct.Struct("<8sQ")
RUN_RECORD_STRUCT = struct.Struct("<II")

DEFAULT_CHUNK_POSITIONS = 500_000
DEFAULT_CHUNK_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_OPEN_RUNS = 64

DbRunRecord = tuple[bytes, bytes]


class DbRunWriter:
    def __init__(self, path: Path, record_count: int) -> None:
        self.path = path
        self.record_count = record_count
        self.written = 0
        self.file: BinaryIO | None = None

    def __enter__(self) -> "DbRunWriter":
        self.file = self.path.open("wb")
        self.file.write(RUN_HEADER_STRUCT.pack(RUN_MAGIC, self.record_count))
        return self

    def write(self, record: DbRunRecord) -> None:
        if self.file is None:
            raise RuntimeError("run writer is not open")
        key, block = record
        self.file.write(RUN_RECORD_STRUCT.pack(len(key), len(block)))
        self.file.write(key)
        self.file.write(block)
        self.written += 1

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()
        if exc_type is None and self.written != self.record_count:
            raise RuntimeError(
                f"run record count mismatch: expected {self.record_count}, wrote {self.written}"
            )


class DbRunReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: BinaryIO | None = None
        self.record_count = 0
        self.remaining = 0

    def __enter__(self) -> "DbRunReader":
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

    def read_next(self) -> DbRunRecord | None:
        if self.file is None:
            raise RuntimeError("run reader is not open")
        if self.remaining == 0:
            return None

        header = self.file.read(RUN_RECORD_STRUCT.size)
        if len(header) != RUN_RECORD_STRUCT.size:
            raise ValueError(f"broken run record: {self.path}")
        key_size, block_size = RUN_RECORD_STRUCT.unpack(header)
        key = self.file.read(key_size)
        block = self.file.read(block_size)
        if len(key) != key_size or len(block) != block_size:
            raise ValueError(f"broken run payload: {self.path}")
        self.remaining -= 1
        return key, block

    def __exit__(self, exc_type, exc, tb) -> None:  # type:ignore[no-untyped-def]
        if self.file is not None:
            self.file.close()


def read_db_run_count(path: Path) -> int:
    with path.open("rb") as f:
        header = f.read(RUN_HEADER_STRUCT.size)
    if len(header) != RUN_HEADER_STRUCT.size:
        raise ValueError(f"broken run header: {path}")
    magic, count = RUN_HEADER_STRUCT.unpack(header)
    if magic != RUN_MAGIC:
        raise ValueError(f"invalid run magic: {path}")
    return int(count)


def iter_merged_db_records(readers: list[DbRunReader]):
    heap: list[tuple[bytes, int, DbRunRecord]] = []

    for index, reader in enumerate(readers):
        record = reader.read_next()
        if record is not None:
            heapq.heappush(heap, (record[0], index, record))

    previous_key: bytes | None = None
    while heap:
        key, reader_index, record = heapq.heappop(heap)
        if previous_key == key:
            raise ValueError(f"duplicated sfen: {key.decode('utf-8', errors='replace')}")
        previous_key = key
        yield record

        next_record = readers[reader_index].read_next()
        if next_record is not None:
            heapq.heappush(heap, (next_record[0], reader_index, next_record))


def write_db_run(records: list[DbRunRecord], path: Path) -> None:
    records.sort(key=lambda item: item[0])
    previous_key: bytes | None = None
    for key, _ in records:
        if previous_key == key:
            raise ValueError(f"duplicated sfen in one chunk: {key.decode('utf-8', errors='replace')}")
        previous_key = key

    with DbRunWriter(path, len(records)) as writer:
        for record in records:
            writer.write(record)


def merge_db_runs_to_run(run_paths: list[Path], output_path: Path) -> None:
    total = sum(read_db_run_count(path) for path in run_paths)
    with ExitStack() as stack:
        readers = [stack.enter_context(DbRunReader(path)) for path in run_paths]
        writer = stack.enter_context(DbRunWriter(output_path, total))
        for record in iter_merged_db_records(readers):
            writer.write(record)


def reduce_db_runs(run_paths: list[Path], work_dir: Path, max_open_runs: int) -> list[Path]:
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
            merge_db_runs_to_run(group, output_path)
            next_runs.append(output_path)
            for path in group:
                path.unlink(missing_ok=True)

        current = next_runs
        stage += 1

    return current


def flush_chunk(records: list[DbRunRecord], run_paths: list[Path], work_dir: Path, run_index: int) -> int:
    if not records:
        return run_index
    run_path = work_dir / f"ybb-to-db-{run_index:06d}.run"
    print(f"write run: {run_path} ({len(records)} positions)")
    write_db_run(records, run_path)
    run_paths.append(run_path)
    records.clear()
    return run_index + 1


def ybb_record_to_db_block(
    packed_sfen: bytes,
    ply: int,
    moves_blob: bytes,
    flags: int,
) -> DbRunRecord:
    board = board_from_packed_sfen(packed_sfen)
    sfen_no_ply = trim_number(board.sfen())

    move_struct = MOVE_DEPTH_STRUCT if flags & FLAG_MOVE_DEPTH else MOVE_STRUCT
    moves: list[tuple[int, int, int]] = []
    for offset in range(0, len(moves_blob), move_struct.size):
        if flags & FLAG_MOVE_DEPTH:
            move16, value, depth = move_struct.unpack(moves_blob[offset : offset + move_struct.size])
        else:
            move16, value = move_struct.unpack(moves_blob[offset : offset + move_struct.size])
            depth = 0
        moves.append((move16, value, depth))
    moves.sort(key=lambda item: item[1], reverse=True)

    lines = [f"sfen {sfen_no_ply} {ply}\n"]
    for move16, value, depth in moves:
        lines.append(f"{move16_to_usi(board, move16)} none {value} {depth}\n")

    key = sfen_no_ply.encode("utf-8")
    block = "".join(lines).encode("utf-8")
    return key, block


def convert_ybb_to_db(
    input_base: Path,
    output_db: Path,
    work_dir: Path,
    chunk_positions: int,
    chunk_bytes: int,
    max_open_runs: int,
) -> None:
    input_ybb = resolve_ybb_input(input_base)
    record_count, flags = read_ybb_header(input_ybb)
    move_struct = MOVE_DEPTH_STRUCT if flags & FLAG_MOVE_DEPTH else MOVE_STRUCT
    moves_base = HEADER_STRUCT.size + record_count * INDEX_STRUCT.size
    file_size = input_ybb.stat().st_size
    if file_size < moves_base:
        raise ValueError(f"broken ybb index area: {input_ybb}")
    moves_file_size = file_size - moves_base
    run_paths: list[Path] = []
    chunk_records: list[DbRunRecord] = []
    chunk_estimated_bytes = 0
    run_index = 0
    previous_packed_sfen: bytes | None = None

    with input_ybb.open("rb") as index_file, input_ybb.open("rb") as moves_file:
        index_file.seek(HEADER_STRUCT.size)
        for index in range(record_count):
            header = index_file.read(INDEX_STRUCT.size)
            if len(header) != INDEX_STRUCT.size:
                raise ValueError(f"broken ybb index record: {input_ybb}")
            packed_sfen, move_offset, ply, move_count = INDEX_STRUCT.unpack(header)
            if previous_packed_sfen is not None and packed_sfen <= previous_packed_sfen:
                raise ValueError(f"ybb index is not strictly sorted at record {index}")
            previous_packed_sfen = packed_sfen

            moves_size = move_count * move_struct.size
            if move_offset + moves_size > moves_file_size:
                raise ValueError(f"moves offset is out of range at record {index}")
            moves_file.seek(moves_base + move_offset)
            moves_blob = moves_file.read(moves_size)
            if len(moves_blob) != moves_size:
                raise ValueError(f"broken ybb move records at record {index}")

            record = ybb_record_to_db_block(packed_sfen, ply, moves_blob, flags)
            chunk_records.append(record)
            chunk_estimated_bytes += len(record[0]) + len(record[1])

            if len(chunk_records) >= chunk_positions or chunk_estimated_bytes >= chunk_bytes:
                run_index = flush_chunk(chunk_records, run_paths, work_dir, run_index)
                chunk_estimated_bytes = 0

    flush_chunk(chunk_records, run_paths, work_dir, run_index)
    run_paths = reduce_db_runs(run_paths, work_dir, max_open_runs)
    write_final_db(run_paths, output_db)


def write_final_db(run_paths: list[Path], output_db: Path) -> None:
    output_db.parent.mkdir(parents=True, exist_ok=True)
    output_tmp = output_db.with_name(output_db.name + ".tmp")
    total = sum(read_db_run_count(path) for path in run_paths)

    try:
        with output_tmp.open("wb") as output_file:
            output_file.write(f"{YANEURAOU_BOOK_HEADER_V1}\n".encode("ascii"))
            output_file.write(f"# NOE:{total}\n".encode("ascii"))

            if run_paths:
                with ExitStack() as stack:
                    readers = [stack.enter_context(DbRunReader(path)) for path in run_paths]
                    for _, block in iter_merged_db_records(readers):
                        output_file.write(block)

        os.replace(output_tmp, output_db)
    except Exception:
        output_tmp.unlink(missing_ok=True)
        raise


def make_work_dir(tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="convert-ybb-to-db-", dir=tmp_dir))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="やねうら王 バイナリ定跡DB .ybb を やねうら王定跡DB .db へ変換します。"
    )
    parser.add_argument(
        "input_base",
        type=Path,
        help="input ybb path. .ybb suffix is optional.",
    )
    parser.add_argument("output_db", type=Path)
    parser.add_argument("--tmp-dir", type=Path, default=Path("tmp"))
    parser.add_argument("--chunk-positions", type=int, default=DEFAULT_CHUNK_POSITIONS)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--max-open-runs", type=int, default=DEFAULT_MAX_OPEN_RUNS)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()

    if args.chunk_positions <= 0:
        raise ValueError("--chunk-positions must be positive")
    if args.chunk_bytes <= 0:
        raise ValueError("--chunk-bytes must be positive")

    try:
        resolve_ybb_input(args.input_base)
    except ValueError as exc:
        parser.error(str(exc))

    work_dir = make_work_dir(args.tmp_dir)
    try:
        convert_ybb_to_db(
            args.input_base,
            args.output_db,
            work_dir,
            args.chunk_positions,
            args.chunk_bytes,
            args.max_open_runs,
        )
    finally:
        if args.keep_temp:
            print(f"keep temp: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)

    print(f"wrote: {args.output_db}")


if __name__ == "__main__":
    main()
