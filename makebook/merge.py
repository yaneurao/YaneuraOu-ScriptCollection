#!/usr/bin/env python3
"""Merge two YaneuraOu book DB files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))
from YaneuraOuBookLib import BookMove, read_yaneuraou_book, write_yaneuraou_book


MERGE_SIDE_MODES = {"bw", "wb"}


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


def merge_books(
    lhs: dict[str, list[BookMove]], rhs: dict[str, list[BookMove]]
) -> tuple[dict[str, list[BookMove]], int, int, int]:
    merged: dict[str, list[BookMove]] = {}
    same_nodes = 0
    different_nodes1 = 0
    different_nodes2 = 0

    for sfen, lhs_moves in lhs.items():
        rhs_moves = rhs.get(sfen)
        if rhs_moves is None:
            merged[sfen] = lhs_moves
            different_nodes1 += 1
        else:
            merged[sfen] = choose_moves(lhs_moves, rhs_moves)
            same_nodes += 1

    for sfen, rhs_moves in rhs.items():
        if sfen not in merged:
            merged[sfen] = rhs_moves
            different_nodes2 += 1

    return merged, same_nodes, different_nodes1, different_nodes2


def side_to_move(sfen: str) -> str:
    tokens = sfen.split()
    if tokens and tokens[0] == "sfen":
        tokens = tokens[1:]
    if len(tokens) < 2 or tokens[1] not in ("b", "w"):
        raise ValueError(f"invalid SFEN side to move: {sfen}")
    return tokens[1]


def filter_book_by_side(book: dict[str, list[BookMove]], side: str) -> dict[str, list[BookMove]]:
    return {sfen: moves for sfen, moves in book.items() if side_to_move(sfen) == side}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge two YaneuraOu book DB files.",
        usage="%(prog)s [bw|wb] src1 src2 dst [--ignore-book-ply]",
    )
    parser.add_argument(
        "mode_or_src1",
        help="merge mode bw/wb, or first source YaneuraOu book DB",
    )
    parser.add_argument(
        "src1_or_src2",
        help="first source DB when mode is specified, otherwise second source DB",
    )
    parser.add_argument(
        "src2_or_dst",
        help="second source DB when mode is specified, otherwise destination DB",
    )
    parser.add_argument(
        "dst",
        nargs="?",
        help="destination YaneuraOu book DB when mode is specified",
    )
    parser.add_argument(
        "--ignore-book-ply",
        action="store_true",
        help="ignore ply when reading source positions",
    )
    args = parser.parse_args()

    if args.mode_or_src1 in MERGE_SIDE_MODES:
        if args.dst is None:
            parser.error("side merge usage: merge.py bw src1 src2 dst")
        args.mode = args.mode_or_src1
        args.src1 = args.src1_or_src2
        args.src2 = args.src2_or_dst
    else:
        if args.dst is not None:
            parser.error("usage: merge.py src1 src2 dst")
        args.mode = ""
        args.src1 = args.mode_or_src1
        args.src2 = args.src1_or_src2
        args.dst = args.src2_or_dst
    del args.mode_or_src1
    del args.src1_or_src2
    del args.src2_or_dst
    return args


def main() -> None:
    args = parse_args()

    book1 = read_yaneuraou_book(args.src1, ignore_book_ply=args.ignore_book_ply)
    book2 = read_yaneuraou_book(args.src2, ignore_book_ply=args.ignore_book_ply)
    if args.mode:
        side1, side2 = args.mode[0], args.mode[1]
        book1 = filter_book_by_side(book1, side1)
        book2 = filter_book_by_side(book2, side2)
    merged, same_nodes, different_nodes1, different_nodes2 = merge_books(book1, book2)
    write_yaneuraou_book(merged, args.dst)

    if args.mode:
        print(f"mode      = {args.mode}")
    print(f"same nodes = {same_nodes} , different nodes =  {different_nodes1} + {different_nodes2}")
    print(f"positions = {len(merged)}")
    print(f"entries   = {sum(len(moves) for moves in merged.values())}")
    print(f"wrote     = {args.dst}")


if __name__ == "__main__":
    main()
