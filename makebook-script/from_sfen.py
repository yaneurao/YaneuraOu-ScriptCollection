#!/usr/bin/env python3
"""Create a YaneuraOu book DB from SFEN game records."""

from __future__ import annotations

import argparse
import sys

import cshogi  # type: ignore

from yaneuraou_book import BookMove, insert_book_move, read_text_lines, write_yaneuraou_book


COLOR_ANY = -1


def parse_position(tokens: list[str]) -> tuple[cshogi.Board, list[str]]:
    board = cshogi.Board()
    idx = 0

    while True:
        token = tokens[idx] if idx < len(tokens) else ""
        idx += 1 if idx < len(tokens) else 0

        if token == "sfen":
            if idx + 4 > len(tokens):
                return board, []
            board.set_sfen(" ".join(tokens[idx : idx + 4]))
            idx += 4
        elif token not in ("startpos", "moves", "sfen"):
            if token == "":
                return board, []
            return board, [token] + tokens[idx:]


def load_sfens(path: str, color: int) -> list[tuple[str, int, int]]:
    return [(line, color, line_no) for line_no, line in enumerate(read_text_lines(path), start=1)]


def move_to_usi(board: cshogi.Board, move_usi: str, line_no: int, source: str) -> str | None:
    move = board.move_from_usi(move_usi)
    if move == 0 or not board.is_legal(move):
        print(
            f"illegal move : line = {line_no} , {source} , move = {move_usi}",
            file=sys.stderr,
        )
        return None
    return cshogi.move_to_usi(move)


def from_sfen_records(records: list[tuple[str, int, int]], moves_limit: int) -> dict[str, list[BookMove]]:
    book: dict[str, list[BookMove]] = {}

    for source, color, line_no in records:
        tokens = source.split()
        if not tokens:
            continue

        board, move_tokens = parse_position(tokens)
        sfens: list[tuple[str, bool]] = []
        moves: list[str] = []

        for move_usi in move_tokens[: moves_limit + 1]:
            if move_usi in ("resign", "win"):
                break

            canonical_move = move_to_usi(board, move_usi, line_no, source)
            if canonical_move is None:
                break

            is_valid = color == COLOR_ANY or color == board.turn
            sfens.append((board.sfen(), is_valid))
            moves.append(canonical_move)
            board.push_usi(canonical_move)

        for i in range(max(0, len(sfens) - 1)):
            if not sfens[i][1]:
                continue
            sfen = sfens[i][0]
            book_moves = book.setdefault(sfen, [])
            insert_book_move(book_moves, BookMove(moves[i], moves[i + 1], 0, 32, 1))

    return book


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a YaneuraOu book DB from SFEN game records."
    )
    parser.add_argument("args", nargs="+")
    parser.add_argument("--moves", type=int, default=16, help="number of moves to read")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.args[0] == "bw":
        if len(args.args) != 4:
            raise SystemExit("usage: from_sfen.py bw black.sfen white.sfen output.db [--moves N]")
        black_file, white_file, dst = args.args[1], args.args[2], args.args[3]
        records: list[tuple[str, int, int]] = []
        if black_file != "no_file":
            records.extend(load_sfens(black_file, cshogi.BLACK))
        if white_file != "no_file":
            records.extend(load_sfens(white_file, cshogi.WHITE))
    else:
        if len(args.args) != 2:
            raise SystemExit("usage: from_sfen.py input.sfen output.db [--moves N]")
        records = load_sfens(args.args[0], COLOR_ANY)
        dst = args.args[1]

    book = from_sfen_records(records, args.moves)
    write_yaneuraou_book(book, dst)
    print(f"positions = {len(book)}")
    print(f"entries   = {sum(len(moves) for moves in book.values())}")
    print(f"wrote     = {dst}")


if __name__ == "__main__":
    main()
