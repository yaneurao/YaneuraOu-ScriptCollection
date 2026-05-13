#!/usr/bin/env python3
"""Sort and normalize a large YaneuraOu book DB using temporary runs."""

from __future__ import annotations

import argparse
import heapq
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from YaneuraOuBookLib import (
    BookMove,
    insert_book_move,
    normalize_sfen,
    read_yaneuraou_book_blocks,
    sfen_ply,
    trim_number,
    write_yaneuraou_book_block,
    write_yaneuraou_header,
)


DEFAULT_CHUNK_POSITIONS = 500000


@dataclass
class SortStats:
    positions: int = 0
    entries: int = 0
    runs: int = 0


def tmp_root_from_arg(tmp_dir: str | None) -> Path:
    root = Path(tmp_dir) if tmp_dir else Path.cwd() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return root


def merge_move_lists(dst: list[BookMove], src: list[BookMove]) -> None:
    for move in src:
        insert_book_move(dst, move)


def flush_run(
    chunk: dict[str, list[BookMove]], run_path: Path, *, run_index: int
) -> tuple[Path, int, int]:
    normalized: dict[str, list[BookMove]] = {}
    for sfen, moves in chunk.items():
        normalized_sfen = normalize_sfen(sfen)
        dst_moves = normalized.setdefault(normalized_sfen, [])
        merge_move_lists(dst_moves, moves)

    records = sorted(normalized.items(), key=lambda item: item[0])

    entries = 0
    with open(run_path, "w", encoding="utf-8", newline="\r\n") as out:
        write_yaneuraou_header(out)
        for sfen, moves in records:
            entries += write_yaneuraou_book_block(out, sfen, moves)

    print(
        f"run {run_index}: positions = {len(records)}, "
        f"entries = {entries}, wrote = {run_path}"
    )
    return run_path, len(records), entries


def make_sorted_runs(
    src: str, work_dir: Path, *, chunk_positions: int, ignore_book_ply: bool
) -> tuple[list[Path], SortStats]:
    chunk: dict[str, list[BookMove]] = {}
    runs: list[Path] = []
    stats = SortStats()

    def flush() -> None:
        if not chunk:
            return
        run_path = work_dir / f"run-{len(runs):06d}.db"
        path, positions, entries = flush_run(chunk, run_path, run_index=len(runs))
        runs.append(path)
        stats.positions += positions
        stats.entries += entries
        stats.runs += 1
        chunk.clear()

    for sfen, moves in read_yaneuraou_book_blocks(src, ignore_book_ply=ignore_book_ply):
        dst_moves = chunk.setdefault(sfen, [])
        merge_move_lists(dst_moves, moves)
        if len(chunk) >= chunk_positions:
            flush()

    flush()
    return runs, stats


class BlockStream:
    def __init__(self, path: Path):
        self.path = path
        self._blocks = read_yaneuraou_book_blocks(str(path))

    def next(self) -> tuple[str, list[BookMove]] | None:
        try:
            return next(self._blocks)
        except StopIteration:
            return None


def write_base_group(
    out,
    group: dict[str, list[BookMove]],
) -> tuple[int, int]:
    min_ply = min(sfen_ply(sfen) for sfen in group)
    positions = 0
    entries = 0
    for sfen in sorted(group):
        if sfen_ply(sfen) != min_ply:
            continue
        entries += write_yaneuraou_book_block(out, sfen, group[sfen])
        positions += 1
    return positions, entries


def merge_runs(runs: list[Path], dst: str) -> tuple[int, int]:
    streams = [BlockStream(path) for path in runs]
    heap: list[tuple[str, int, list[BookMove]]] = []

    for index, stream in enumerate(streams):
        block = stream.next()
        if block is not None:
            sfen, moves = block
            heapq.heappush(heap, (sfen, index, moves))

    positions = 0
    entries = 0
    current_base = ""
    current_group: dict[str, list[BookMove]] = {}

    with open(dst, "w", encoding="utf-8", newline="\r\n") as out:
        write_yaneuraou_header(out)

        while heap:
            sfen, index, moves = heapq.heappop(heap)
            base = trim_number(sfen)

            if current_base != "" and base != current_base:
                p, e = write_base_group(out, current_group)
                positions += p
                entries += e
                current_group = {}

            current_base = base
            dst_moves = current_group.setdefault(sfen, [])
            merge_move_lists(dst_moves, moves)

            block = streams[index].next()
            if block is not None:
                next_sfen, next_moves = block
                heapq.heappush(heap, (next_sfen, index, next_moves))

        if current_group:
            p, e = write_base_group(out, current_group)
            positions += p
            entries += e

    return positions, entries


def sort_largebook(
    src: str,
    dst: str,
    *,
    tmp_dir: str | None,
    chunk_positions: int,
    ignore_book_ply: bool,
    keep_temp: bool,
) -> None:
    tmp_root = tmp_root_from_arg(tmp_dir)
    work_dir = Path(tempfile.mkdtemp(prefix="sort_largebook-", dir=tmp_root))

    try:
        runs, run_stats = make_sorted_runs(
            src, work_dir, chunk_positions=chunk_positions, ignore_book_ply=ignore_book_ply
        )
        positions, entries = merge_runs(runs, dst)
        print(f"runs      = {run_stats.runs}")
        print(f"positions = {positions}")
        print(f"entries   = {entries}")
        print(f"wrote     = {dst}")
        if keep_temp:
            print(f"temp      = {work_dir}")
    finally:
        if not keep_temp:
            shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sort and normalize a large YaneuraOu book DB using temporary runs."
    )
    parser.add_argument("src", help="source YaneuraOu book DB")
    parser.add_argument("dst", help="destination YaneuraOu book DB")
    parser.add_argument(
        "--tmp-dir",
        default=None,
        help="directory for temporary files; defaults to ./tmp",
    )
    parser.add_argument(
        "--chunk-positions",
        type=int,
        default=DEFAULT_CHUNK_POSITIONS,
        help=f"positions per temporary run; default {DEFAULT_CHUNK_POSITIONS}",
    )
    parser.add_argument(
        "--ignore-book-ply",
        action="store_true",
        help="ignore ply when reading source positions",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="keep temporary files after successful or failed execution",
    )
    args = parser.parse_args()

    if args.chunk_positions <= 0:
        raise SystemExit("--chunk-positions must be positive")

    sort_largebook(
        args.src,
        args.dst,
        tmp_dir=args.tmp_dir,
        chunk_positions=args.chunk_positions,
        ignore_book_ply=args.ignore_book_ply,
        keep_temp=args.keep_temp,
    )


if __name__ == "__main__":
    main()
