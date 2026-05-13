#!/usr/bin/env python3
"""Merge two YaneuraOu book DB files."""

from __future__ import annotations

import argparse

from yaneuraou_book import BookMove, read_yaneuraou_book, write_yaneuraou_book


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge two YaneuraOu book DB files.")
    parser.add_argument("src1", help="first source YaneuraOu book DB")
    parser.add_argument("src2", help="second source YaneuraOu book DB")
    parser.add_argument("dst", help="destination YaneuraOu book DB")
    parser.add_argument(
        "--ignore-book-ply",
        action="store_true",
        help="ignore ply when reading source positions",
    )
    args = parser.parse_args()

    book1 = read_yaneuraou_book(args.src1, ignore_book_ply=args.ignore_book_ply)
    book2 = read_yaneuraou_book(args.src2, ignore_book_ply=args.ignore_book_ply)
    merged, same_nodes, different_nodes1, different_nodes2 = merge_books(book1, book2)
    write_yaneuraou_book(merged, args.dst)

    print(f"same nodes = {same_nodes} , different nodes =  {different_nodes1} + {different_nodes2}")
    print(f"positions = {len(merged)}")
    print(f"entries   = {sum(len(moves) for moves in merged.values())}")
    print(f"wrote     = {args.dst}")


if __name__ == "__main__":
    main()
