#!/usr/bin/env python3
"""Convert a YaneuraOu text book to an Apery book."""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass

import cshogi  # type: ignore


UINT16_MAX = 0xFFFF
UINT64_MOD = 1 << 64
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


@dataclass
class BookMove:
    move: int
    ponder: int
    value: int
    depth: int
    move_count: int


def c_atoll(token: str, default: int) -> int:
    """Mimic C atoll(): missing token keeps default, malformed token becomes 0."""
    if token == "":
        return default

    i = 0
    sign = 1
    if token[i : i + 1] in ("+", "-"):
        sign = -1 if token[i] == "-" else 1
        i += 1

    value = 0
    found = False
    while i < len(token) and token[i].isdigit():
        value = value * 10 + ord(token[i]) - ord("0")
        i += 1
        found = True

    return sign * value if found else 0


def as_u64_from_s64(value: int) -> int:
    return value % UINT64_MOD


def read_text_lines(path: str):
    with open(path, "r", encoding="utf-8-sig", newline=None) as f:
        for raw in f:
            yield raw.rstrip("\n").rstrip("\r").rstrip(" \t")


def split_space_tokens(line: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    n = len(line)
    while pos < n:
        while pos < n and line[pos] == " ":
            pos += 1
        if pos >= n:
            break
        start = pos
        while pos < n and line[pos] != " ":
            pos += 1
        tokens.append(line[start:pos])
    return tokens


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


def insert_book_move(moves: list[BookMove], new_move: BookMove) -> None:
    for i, old in enumerate(moves):
        if old.move == new_move.move:
            new_move.move_count = as_u64_from_s64(new_move.move_count) + old.move_count
            moves[i] = new_move
            return
    moves.append(new_move)


def parse_book_move(line: str) -> BookMove:
    tokens = split_space_tokens(line)
    move_str = tokens[0] if len(tokens) > 0 else ""
    ponder_str = tokens[1] if len(tokens) > 1 else ""

    value = c_atoll(tokens[2], 0) if len(tokens) > 2 else 0
    depth = c_atoll(tokens[3], 0) if len(tokens) > 3 else 0
    move_count_s64 = c_atoll(tokens[4], 1) if len(tokens) > 4 else 1

    return BookMove(
        move=usi_to_apery_move16(move_str),
        ponder=usi_to_apery_move16(ponder_str),
        value=value,
        depth=depth,
        move_count=as_u64_from_s64(move_count_s64),
    )


def read_yaneuraou_book(path: str) -> dict[str, list[BookMove]]:
    book: dict[str, list[BookMove]] = {}
    current_sfen = ""

    for line in read_text_lines(path):
        if line == "":
            continue
        if line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("sfen "):
            current_sfen = line[5:]
            continue
        if current_sfen == "":
            continue

        moves = book.setdefault(current_sfen, [])
        insert_book_move(moves, parse_book_move(line))

    return book


def sorted_book_moves(moves: list[BookMove]) -> list[BookMove]:
    return sorted(moves, key=lambda m: (-m.move_count, -m.value))


def convert_to_apery(src: str, dst: str) -> None:
    book = read_yaneuraou_book(src)

    keyed_entries: list[tuple[int, list[BookMove]]] = []
    board = cshogi.Board()
    for sfen, moves in book.items():
        board.set_sfen(sfen)
        keyed_entries.append((int(board.book_key()), moves))

    keyed_entries.sort(key=lambda item: item[0])

    with open(dst, "wb") as f:
        for key, moves in keyed_entries:
            for move in sorted_book_moves(moves):
                count = min(move.move_count, UINT16_MAX)
                f.write(struct.pack("<QHHi", key, move.move & UINT16_MAX, count, move.value))

    print(f"positions = {len(book)}")
    print(f"entries   = {sum(len(moves) for moves in book.values())}")
    print(f"wrote     = {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a YaneuraOu text book to an Apery book."
    )
    parser.add_argument("src", help="source YaneuraOu text book")
    parser.add_argument("dst", help="destination Apery book")
    args = parser.parse_args()

    convert_to_apery(args.src, args.dst)


if __name__ == "__main__":
    main()
