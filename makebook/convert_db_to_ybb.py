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
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import BinaryIO

import cshogi
import numpy as np


MAGIC = b"YANE-BINBOOK-V1\0"
HEADER_STRUCT = struct.Struct("<16sQ")
INDEX_STRUCT = struct.Struct("<32sQHH")
MOVE_STRUCT = struct.Struct("<Hh")

RUN_MAGIC = b"YBBRUN1\0"
RUN_HEADER_STRUCT = struct.Struct("<8sQ")
RUN_RECORD_STRUCT = struct.Struct("<32sHH")

MOVE_NONE = 0
MOVE_NULL = (1 << 7) + 1
MOVE_RESIGN = (2 << 7) + 2
MOVE_WIN = (3 << 7) + 3

DEFAULT_CHUNK_POSITIONS = 500_000
DEFAULT_CHUNK_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_OPEN_RUNS = 64

YbbRunRecord = tuple[bytes, int, bytes]


def validate_ybb_base_path(path: Path) -> None:
    name = path.name
    if (
        name.endswith(".ybb")
        or name.endswith("-index")
        or name.endswith("-moves")
        or name.endswith("-index.ybb")
        or name.endswith("-moves.ybb")
    ):
        raise ValueError(
            "specify ybb base path without .ybb, -index, or -moves suffix. "
            f"example: user_book ; got: {path}"
        )


def ybb_pair_from_base(base_path: Path) -> tuple[Path, Path]:
    validate_ybb_base_path(base_path)
    return (
        base_path.with_name(f"{base_path.name}-index.ybb"),
        base_path.with_name(f"{base_path.name}-moves.ybb"),
    )


def trim_sfen_ply(sfen: str) -> tuple[str, int]:
    tokens = sfen.split()
    if tokens and tokens[0] == "sfen":
        tokens = tokens[1:]
    ply = 1
    if tokens:
        try:
            ply = int(tokens[-1])
            tokens = tokens[:-1]
        except ValueError:
            pass
    return " ".join(tokens), ply


def pack_sfen(board: cshogi.Board) -> bytes:
    psfen = np.empty(1, dtype=cshogi.PackedSfen)
    board.to_psfen(psfen)
    return psfen[0]["sfen"].tobytes()


def usi_to_move16(board: cshogi.Board, usi: str) -> int:
    if usi in ("none", "None"):
        return MOVE_NONE
    if usi in ("null", "0000", "pass"):
        return MOVE_NULL
    if usi == "resign":
        return MOVE_RESIGN
    if usi == "win":
        return MOVE_WIN
    move = board.move_from_usi(usi)
    if move == cshogi.MOVE_NONE:
        raise ValueError(f"invalid move for position: {usi} / {board.sfen()}")
    return int(cshogi.move16(move))


def parse_move_line(line: str) -> tuple[str, int] | None:
    if "," in line:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[1]
    else:
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"invalid move line: {line}")
        move_text = parts[0]
        eval_text = parts[2]

    if eval_text.lower() == "none":
        return None

    value = int(eval_text)
    if value < -32768 or value > 32767:
        raise ValueError(f"eval is out of int16 range: {value}")
    return move_text, value


class YbbRunWriter:
    def __init__(self, path: Path, record_count: int) -> None:
        self.path = path
        self.record_count = record_count
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
        move_count, remainder = divmod(len(moves_blob), MOVE_STRUCT.size)
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
    def __init__(self, path: Path) -> None:
        self.path = path
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
        moves_blob = self.file.read(move_count * MOVE_STRUCT.size)
        if len(moves_blob) != move_count * MOVE_STRUCT.size:
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


def write_ybb_run(records: list[YbbRunRecord], path: Path) -> None:
    records.sort(key=lambda item: item[0])
    previous_key: bytes | None = None
    for packed_sfen, _, _ in records:
        if previous_key == packed_sfen:
            raise ValueError(f"duplicated packed sfen in one chunk: {packed_sfen.hex()}")
        previous_key = packed_sfen

    with YbbRunWriter(path, len(records)) as writer:
        for record in records:
            writer.write(record)


def merge_ybb_runs_to_run(run_paths: list[Path], output_path: Path) -> None:
    total = sum(read_ybb_run_count(path) for path in run_paths)
    with ExitStack() as stack:
        readers = [stack.enter_context(YbbRunReader(path)) for path in run_paths]
        writer = stack.enter_context(YbbRunWriter(output_path, total))
        for record in iter_merged_ybb_records(readers):
            writer.write(record)


def reduce_ybb_runs(run_paths: list[Path], work_dir: Path, max_open_runs: int) -> list[Path]:
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
            merge_ybb_runs_to_run(group, output_path)
            next_runs.append(output_path)
            for path in group:
                path.unlink(missing_ok=True)

        current = next_runs
        stage += 1

    return current


def write_final_ybb(run_paths: list[Path], output_base: Path) -> None:
    index_path, moves_path = ybb_pair_from_base(output_base)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    moves_path.parent.mkdir(parents=True, exist_ok=True)

    index_tmp = index_path.with_name(index_path.name + ".tmp")
    moves_tmp = moves_path.with_name(moves_path.name + ".tmp")
    total = sum(read_ybb_run_count(path) for path in run_paths)

    try:
        with moves_tmp.open("wb") as moves_file, index_tmp.open("wb") as index_file:
            index_file.write(HEADER_STRUCT.pack(MAGIC, total))
            move_offset = 0

            if run_paths:
                with ExitStack() as stack:
                    readers = [stack.enter_context(YbbRunReader(path)) for path in run_paths]
                    for packed_sfen, ply, moves_blob in iter_merged_ybb_records(readers):
                        move_count = len(moves_blob) // MOVE_STRUCT.size
                        index_file.write(INDEX_STRUCT.pack(packed_sfen, move_offset, ply, move_count))
                        moves_file.write(moves_blob)
                        move_offset += len(moves_blob)

        os.replace(moves_tmp, moves_path)
        os.replace(index_tmp, index_path)
    except Exception:
        index_tmp.unlink(missing_ok=True)
        moves_tmp.unlink(missing_ok=True)
        raise


def flush_chunk(
    records: list[YbbRunRecord],
    run_paths: list[Path],
    work_dir: Path,
    run_index: int,
) -> int:
    if not records:
        return run_index
    run_path = work_dir / f"db-to-ybb-{run_index:06d}.run"
    print(f"write run: {run_path} ({len(records)} positions)")
    write_ybb_run(records, run_path)
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
) -> None:
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
                run_index = flush_chunk(chunk_records, run_paths, work_dir, run_index)
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
            move_text, value = parsed
            move16 = usi_to_move16(board, move_text)
            current_moves.append(MOVE_STRUCT.pack(move16, value))
            if len(current_moves) > 65535:
                raise ValueError(f"line {line_number}: too many moves: {current_sfen}")

    finish_current()
    flush_chunk(chunk_records, run_paths, work_dir, run_index)

    print(f"read positions: {total_positions}")
    run_paths = reduce_ybb_runs(run_paths, work_dir, max_open_runs)
    write_final_ybb(run_paths, output_base)


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
        help="output ybb base path. Do not add .ybb, -index, or -moves suffix.",
    )
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
        validate_ybb_base_path(args.output_base)
    except ValueError as exc:
        parser.error(str(exc))

    work_dir = make_work_dir(args.tmp_dir)
    try:
        convert_db_to_ybb(
            args.input_db,
            args.output_base,
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

    output_index, moves_path = ybb_pair_from_base(args.output_base)
    print(f"wrote: {output_index}")
    print(f"wrote: {moves_path}")


if __name__ == "__main__":
    main()
