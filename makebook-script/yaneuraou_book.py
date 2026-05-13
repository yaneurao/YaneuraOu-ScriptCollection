from __future__ import annotations

from dataclasses import dataclass

import cshogi  # type: ignore


YANEURAOU_BOOK_HEADER_V1 = "#YANEURAOU-DB2016 1.00"
UINT64_MOD = 1 << 64


@dataclass
class BookMove:
    move: str
    ponder: str
    value: int
    depth: int
    move_count: int


def c_atoll(token: str, default: int) -> int:
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


def read_text_lines(path: str):
    with open(path, "r", encoding="utf-8-sig", newline=None) as f:
        for raw in f:
            line = raw.rstrip("\n").rstrip("\r").strip(" \t")
            if line != "":
                yield line


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


def normalize_move(move: str) -> str:
    if move in ("", "none", "None", "resign"):
        return "none"
    return move


def parse_book_move(line: str) -> BookMove:
    tokens = split_space_tokens(line)
    move = normalize_move(tokens[0] if len(tokens) > 0 else "")
    ponder = normalize_move(tokens[1] if len(tokens) > 1 else "")
    value = c_atoll(tokens[2], 0) if len(tokens) > 2 else 0
    depth = c_atoll(tokens[3], 0) if len(tokens) > 3 else 0
    move_count = c_atoll(tokens[4], 1) if len(tokens) > 4 else 1
    return BookMove(move, ponder, value, depth, as_u64_from_s64(move_count))


def insert_book_move(moves: list[BookMove], new_move: BookMove) -> None:
    for i, old in enumerate(moves):
        if old.move == new_move.move:
            new_move.move_count = as_u64_from_s64(new_move.move_count) + old.move_count
            moves[i] = new_move
            return
    moves.append(new_move)


def read_yaneuraou_book(path: str, *, ignore_book_ply: bool = False) -> dict[str, list[BookMove]]:
    book: dict[str, list[BookMove]] = {}
    current_sfen = ""

    for line in read_text_lines(path):
        if line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("sfen "):
            current_sfen = line[5:]
            if ignore_book_ply:
                current_sfen = trim_number(current_sfen)
            continue
        if current_sfen == "":
            continue

        moves = book.setdefault(current_sfen, [])
        insert_book_move(moves, parse_book_move(line))

    return book


def sorted_book_moves(moves: list[BookMove]) -> list[BookMove]:
    return sorted(moves, key=lambda move: (-move.move_count, -move.value))


def normalize_sfen(sfen: str) -> str:
    board = cshogi.Board()
    board.set_sfen(sfen)
    return board.sfen()


def write_yaneuraou_book(book: dict[str, list[BookMove]], dst: str) -> None:
    vectored_book: list[tuple[str, list[BookMove]]] = []
    book_ply: dict[str, int] = {}

    for sfen, moves in book.items():
        if not moves:
            continue

        normalized_sfen = normalize_sfen(sfen)
        vectored_book.append((normalized_sfen, moves))

        sfen_left = trim_number(normalized_sfen)
        ply = sfen_ply(normalized_sfen)
        old_ply = book_ply.get(sfen_left)
        book_ply[sfen_left] = ply if old_ply is None else min(old_ply, ply)

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
                    f"{move.move} {move.ponder} {move.value} "
                    f"{move.depth} {move.move_count}\n"
                )
