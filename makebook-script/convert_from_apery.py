#!/usr/bin/env python3
"""Convert an Apery book to a YaneuraOu book DB."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from collections import defaultdict

import cshogi  # type: ignore
import numpy as np


YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"

SQ_NB = 81
APERY_PROMOTE = 1 << 14

INT_TO_PIECE = {
    1: "P",
    2: "L",
    3: "N",
    4: "S",
    5: "B",
    6: "R",
    7: "G",
}


@dataclass
class BookMove:
    move: int
    ponder: int
    value: int
    depth: int
    move_count: int


def trim_number(s: str) -> str:
    end = len(s)
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    while end > 0 and s[end - 1].isdigit():
        end -= 1
    while end > 0 and s[end - 1] in (" ", "\t", "\r", "\n"):
        end -= 1
    return s[:end]


def sfen_ply(sfen: str) -> int:
    left = trim_number(sfen)
    tail = sfen[len(left) :]
    try:
        return int(tail.strip())
    except ValueError:
        return 0


def square_to_usi(sq: int) -> str:
    return f"{sq // 9 + 1}{chr(ord('a') + sq % 9)}"


def apery_move16_to_usi(move: int) -> str:
    move &= 0xFFFF
    if move == 0:
        return "none"

    to_sq = move & 0x7F
    from_sq = (move >> 7) & 0x7F
    promote = (move & APERY_PROMOTE) != 0

    if from_sq >= SQ_NB:
        piece = from_sq - SQ_NB + 1
        return f"{INT_TO_PIECE.get(piece, '')}*{square_to_usi(to_sq)}"

    suffix = "+" if promote else ""
    return f"{square_to_usi(from_sq)}{square_to_usi(to_sq)}{suffix}"


def sorted_book_moves(moves: list[BookMove]) -> list[BookMove]:
    return sorted(moves, key=lambda m: (-m.move_count, -m.value))


def insert_book_move(book: dict[str, list[BookMove]], sfen: str, new_move: BookMove) -> None:
    moves = book.setdefault(sfen, [])
    for i, old in enumerate(moves):
        if old.move == new_move.move:
            new_move.move_count += old.move_count
            moves[i] = new_move
            return
    moves.append(new_move)


def normalize_sfen(sfen: str) -> str:
    board = cshogi.Board()
    board.set_sfen(sfen)
    return board.sfen()


def read_apery_entries(path: str) -> dict[int, list[BookMove]]:
    entries = np.fromfile(path, dtype=cshogi.BookEntry)
    by_key: dict[int, list[BookMove]] = defaultdict(list)
    for entry in entries:
        by_key[int(entry["key"])].append(
            BookMove(
                move=int(entry["fromToPro"]) & 0xFFFF,
                ponder=0,
                value=int(entry["score"]),
                depth=256,
                move_count=int(entry["count"]),
            )
        )
    return dict(by_key)


def board_move_from_move16(board: cshogi.Board, move16: int) -> int:
    return int(board.move_from_move16(move16 & 0xFFFF))


def is_legal_move16(board: cshogi.Board, move16: int) -> bool:
    move = board_move_from_move16(board, move16)
    return move != 0 and bool(board.is_legal(move))


def convert_from_apery(src: str, dst: str, unreg_depth: int) -> None:
    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))

    apery_book = read_apery_entries(src)
    book: dict[str, list[BookMove]] = {}
    seen: set[str] = set()
    collisions = 0

    def report() -> None:
        print(
            f"# seen positions = {len(seen)}, "
            f"size of converted book = {len(book)}, "
            f"# hash collisions detected = {collisions}"
        )

    def find_current_position(board: cshogi.Board) -> list[BookMove]:
        return book.get(board.sfen(), [])

    def search(board: cshogi.Board, unreg_depth_current: int) -> None:
        nonlocal collisions

        sfen = board.sfen()
        if unreg_depth == unreg_depth_current:
            sfen_for_key = trim_number(sfen)
            if sfen_for_key in seen:
                return
            seen.add(sfen_for_key)
            if len(seen) % 100000 == 0:
                report()

        entries = apery_book.get(int(board.book_key()), [])
        if not entries:
            if unreg_depth_current < 1:
                return
        else:
            if unreg_depth != unreg_depth_current:
                sfen_for_key = trim_number(sfen)
                if sfen_for_key in seen:
                    return
                seen.add(sfen_for_key)
                if len(seen) % 100000 == 0:
                    report()

            if any(not is_legal_move16(board, entry.move) for entry in entries):
                collisions += 1
                return

        for move in sorted(board.legal_moves, key=cshogi.move_to_usi):
            board.push(move)
            search(board, unreg_depth_current - 1 if not entries else unreg_depth)
            board.pop()

        if not entries:
            return

        for entry in entries:
            insert_book_move(
                book,
                sfen,
                BookMove(entry.move, 0, entry.value, 256, entry.move_count),
            )

        book[sfen] = sorted_book_moves(book[sfen])

        for book_move in book[sfen]:
            move = board_move_from_move16(board, book_move.move)
            board.push(move)
            child_moves = find_current_position(board)
            if child_moves:
                book_move.ponder = child_moves[0].move
            board.pop()

    board = cshogi.Board()
    search(board, unreg_depth)
    report()

    write_yaneuraou_book(book, dst)


def write_yaneuraou_book(book: dict[str, list[BookMove]], dst: str) -> None:
    vectored_book: list[tuple[str, list[BookMove]]] = []
    book_ply: dict[str, int] = {}

    for sfen, moves in book.items():
        if not moves:
            continue
        normalized = normalize_sfen(sfen)
        vectored_book.append((normalized, moves))
        sfen_left = trim_number(normalized)
        ply = sfen_ply(normalized)
        book_ply[sfen_left] = min(book_ply.get(sfen_left, ply), ply)

    vectored_book.sort(key=lambda item: item[0])

    with open(dst, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(YANEURAOU_BOOK_HEADER_V1 + "\n")
        for sfen, moves in vectored_book:
            sfen_left = trim_number(sfen)
            if book_ply[sfen_left] != sfen_ply(sfen):
                continue

            f.write(f"sfen {sfen}\n")
            for move in sorted_book_moves(moves):
                f.write(
                    f"{apery_move16_to_usi(move.move)} "
                    f"{apery_move16_to_usi(move.ponder)} "
                    f"{move.value} {move.depth} {move.move_count}\n"
                )

    print(f"wrote = {dst}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an Apery book to a YaneuraOu book DB."
    )
    parser.add_argument("src", help="source Apery book")
    parser.add_argument("dst", help="destination YaneuraOu book DB")
    parser.add_argument(
        "--unreg-depth",
        type=int,
        default=1,
        help="depth to search below unregistered positions, matching YaneuraOu default",
    )
    args = parser.parse_args()

    convert_from_apery(args.src, args.dst, args.unreg_depth)


if __name__ == "__main__":
    main()
