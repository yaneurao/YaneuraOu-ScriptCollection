#!/usr/bin/env python3
"""Convert a YaneuraOu book DB to an Apery book."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import cshogi  # type: ignore

COMMON_LIB_DIR = Path(__file__).resolve().parents[1] / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))
import YaneuraOuBookLib as BookLib


UINT16_MAX = 0xFFFF
SQ_NB = 81
APERY_PROMOTE = 1 << 14

PIECE_TO_INT = {
    "P": 1,
    "L": 2,
    "N": 3,
    "S": 4,
    "B": 5,
    "R": 6,
    "G": 7,
}


def square_to_index(sq: str) -> int:
    if len(sq) != 2:
        return SQ_NB
    file_ch, rank_ch = sq
    if not file_ch.isdigit():
        return SQ_NB
    file_no = ord(file_ch) - ord("0")
    rank = ord(rank_ch) - ord("a")
    if not (1 <= file_no <= 9 and 0 <= rank < 9):
        return SQ_NB
    return (file_no - 1) * 9 + rank


def usi_to_apery_move16(move: str) -> int:
    if move in ("none", "None", "resign") or len(move) <= 3:
        return 0

    to_sq = square_to_index(move[2:4])
    if to_sq >= SQ_NB:
        return 0

    if move[1:2] == "*":
        piece = PIECE_TO_INT.get(move[0], 0)
        if piece == 0:
            return 0
        return ((SQ_NB + piece - 1) << 7) | to_sq

    from_sq = square_to_index(move[0:2])
    if from_sq >= SQ_NB:
        return 0

    promote = APERY_PROMOTE if len(move) == 5 and move[4] == "+" else 0
    return promote | (from_sq << 7) | to_sq


def convert_to_apery(src: str, dst: str) -> None:
    book = BookLib.read_yaneuraou_book(src)

    keyed_entries = []
    board = cshogi.Board()
    for sfen, moves in book.items():
        board.set_sfen(sfen)
        keyed_entries.append((int(board.book_key()), moves))

    keyed_entries.sort(key=lambda item: item[0])

    with open(dst, "wb") as f:
        for key, moves in keyed_entries:
            for move in BookLib.sorted_book_moves(moves):
                count = min(move.move_count, UINT16_MAX)
                f.write(
                    struct.pack(
                        "<QHHi",
                        key,
                        usi_to_apery_move16(move.move) & UINT16_MAX,
                        count,
                        move.value,
                    )
                )

    print(f"positions = {len(book)}")
    print(f"entries   = {sum(len(moves) for moves in book.values())}")
    print(f"wrote     = {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a YaneuraOu book DB to an Apery book."
    )
    parser.add_argument("src", help="source YaneuraOu book DB")
    parser.add_argument("dst", help="destination Apery book")
    args = parser.parse_args()

    convert_to_apery(args.src, args.dst)


if __name__ == "__main__":
    main()
