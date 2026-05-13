#!/usr/bin/env python3
"""Merge two large YaneuraOu book DB files without loading them fully."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from YaneuraOuBookLib import BookMove, read_yaneuraou_book_blocks, write_yaneuraou_book_block, write_yaneuraou_header
from sort_largebook import DEFAULT_CHUNK_POSITIONS, tmp_root_from_arg


def choose_moves(lhs: list[BookMove], rhs: list[BookMove]) -> list[BookMove]:
    if not lhs:
        return rhs
    if not rhs:
        return lhs
    if lhs[0].depth > rhs[0].depth:
        return lhs
    if lhs[0].depth < rhs[0].depth:
        return rhs
    if len(lhs) >= len(rhs):
        return lhs
    return rhs


class BlockReader:
    def __init__(self, path: Path):
        self._blocks = read_yaneuraou_book_blocks(str(path))
        self.current: tuple[str, list[BookMove]] | None = None
        self.advance()

    def advance(self) -> None:
        try:
            self.current = next(self._blocks)
        except StopIteration:
            self.current = None


def run_sort_largebook(
    src: str,
    dst: Path,
    *,
    work_dir: Path,
    chunk_positions: int,
    ignore_book_ply: bool,
) -> None:
    script = Path(__file__).resolve().parent / "sort_largebook.py"
    cmd = [
        sys.executable,
        str(script),
        src,
        str(dst),
        "--tmp-dir",
        str(work_dir),
        "--chunk-positions",
        str(chunk_positions),
    ]
    if ignore_book_ply:
        cmd.append("--ignore-book-ply")
    subprocess.run(cmd, check=True)


def merge_sorted_books(src1: Path, src2: Path, dst: str) -> tuple[int, int, int, int, int]:
    reader1 = BlockReader(src1)
    reader2 = BlockReader(src2)
    same_nodes = 0
    different_nodes1 = 0
    different_nodes2 = 0
    positions = 0
    entries = 0

    with open(dst, "w", encoding="utf-8", newline="\r\n") as out:
        write_yaneuraou_header(out)

        while reader1.current is not None or reader2.current is not None:
            if reader1.current is None:
                sfen, moves = reader2.current  # type: ignore[misc]
                different_nodes2 += 1
                reader2.advance()
            elif reader2.current is None:
                sfen, moves = reader1.current
                different_nodes1 += 1
                reader1.advance()
            else:
                sfen1, moves1 = reader1.current
                sfen2, moves2 = reader2.current
                if sfen1 < sfen2:
                    sfen, moves = sfen1, moves1
                    different_nodes1 += 1
                    reader1.advance()
                elif sfen2 < sfen1:
                    sfen, moves = sfen2, moves2
                    different_nodes2 += 1
                    reader2.advance()
                else:
                    sfen = sfen1
                    moves = choose_moves(moves1, moves2)
                    same_nodes += 1
                    reader1.advance()
                    reader2.advance()

            entries += write_yaneuraou_book_block(out, sfen, moves)
            positions += 1

    return same_nodes, different_nodes1, different_nodes2, positions, entries


def merge_largebook(
    src1: str,
    src2: str,
    dst: str,
    *,
    tmp_dir: str | None,
    chunk_positions: int,
    ignore_book_ply: bool,
    keep_temp: bool,
) -> None:
    tmp_root = tmp_root_from_arg(tmp_dir)
    work_dir = Path(tempfile.mkdtemp(prefix="merge_largebook-", dir=tmp_root))

    try:
        sorted1 = work_dir / "src1.sorted.db"
        sorted2 = work_dir / "src2.sorted.db"
        run_sort_largebook(
            src1,
            sorted1,
            work_dir=work_dir,
            chunk_positions=chunk_positions,
            ignore_book_ply=ignore_book_ply,
        )
        run_sort_largebook(
            src2,
            sorted2,
            work_dir=work_dir,
            chunk_positions=chunk_positions,
            ignore_book_ply=ignore_book_ply,
        )
        same_nodes, different_nodes1, different_nodes2, positions, entries = merge_sorted_books(
            sorted1, sorted2, dst
        )
        print(f"same nodes = {same_nodes} , different nodes =  {different_nodes1} + {different_nodes2}")
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
        description="Merge two large YaneuraOu book DB files without loading them fully."
    )
    parser.add_argument("src1", help="first source YaneuraOu book DB")
    parser.add_argument("src2", help="second source YaneuraOu book DB")
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
        help=f"positions per sort run; default {DEFAULT_CHUNK_POSITIONS}",
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

    merge_largebook(
        args.src1,
        args.src2,
        args.dst,
        tmp_dir=args.tmp_dir,
        chunk_positions=args.chunk_positions,
        ignore_book_ply=args.ignore_book_ply,
        keep_temp=args.keep_temp,
    )


if __name__ == "__main__":
    main()
