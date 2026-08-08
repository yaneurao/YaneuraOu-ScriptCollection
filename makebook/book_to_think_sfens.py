#!/usr/bin/env python3
"""Enumerate leaf positions from a YaneuraOu book as think_sfens.txt lines."""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cshogi  # type: ignore

SCRIPT_COLLECTION_DIR = Path(__file__).resolve().parents[1]
COMMON_LIB_DIR = SCRIPT_COLLECTION_DIR / "CommonLib"
if str(COMMON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_LIB_DIR))

from YaneShogiLib import SFEN_START_PLY1, flipped_move, flipped_sfen
from YaneuraOuBookLib import (
    BookMove,
    insert_book_move,
    read_yaneuraou_book,
    trim_number,
)


DEFAULT_OUTPUT_PATH = SCRIPT_COLLECTION_DIR / "BookMiner" / "book" / "think_sfens.txt"
NON_BOARD_MOVES = {"", "none", "None", "null", "0000", "pass", "resign", "win"}


@dataclass
class RootPosition:
    position_cmd: str
    sfen_with_ply: str


@dataclass
class LeafOutput:
    position_cmd: str
    sfen_with_ply: str


def normalize_sfen_key(sfen: str) -> str:
    """Return a canonical no-ply SFEN key for book lookup."""
    sfen = sfen.strip()
    if sfen.startswith("sfen "):
        sfen = sfen[5:].strip()

    board = cshogi.Board()
    board.set_sfen(sfen)
    return trim_number(board.sfen())


def board_from_sfen(sfen_with_ply: str) -> cshogi.Board:
    board = cshogi.Board()
    board.set_sfen(sfen_with_ply)
    return board


def checked_push_usi(board: cshogi.Board, move: str, *, context: str) -> None:
    try:
        move32 = board.move_from_usi(move)
    except Exception as exc:
        raise ValueError(f"invalid move: {move} / {context} / {board.sfen()}") from exc

    if not move32 or not board.is_legal(move32):
        raise ValueError(f"illegal move: {move} / {context} / {board.sfen()}")

    board.push(move32)


def append_position_move(position_cmd: str, move: str) -> str:
    position_cmd = position_cmd.strip()
    if " moves " in position_cmd:
        return f"{position_cmd} {move}"
    return f"{position_cmd} moves {move}"


def parse_position_string(position_cmd: str) -> tuple[str, list[str]]:
    text = position_cmd.strip()
    if text.startswith("position "):
        text = text[len("position ") :].strip()

    head, sep, moves_part = text.partition(" moves ")
    moves = moves_part.split() if sep else []
    head = head.strip()

    if not head or head == "startpos":
        return SFEN_START_PLY1, moves
    if head.startswith("sfen "):
        return head[len("sfen ") :].strip(), moves
    return head, moves


def decode_position_string(position_cmd: str) -> str:
    sfen, moves = parse_position_string(position_cmd)
    board = board_from_sfen(sfen)
    for move in moves:
        checked_push_usi(board, move, context=position_cmd)
    return board.sfen()


def normalize_position_command(position_cmd: str, sfen_with_ply: str) -> str:
    text = position_cmd.strip()
    if text.startswith("position "):
        text = text[len("position ") :].strip()
    if not text:
        return "startpos"
    if text == "startpos" or text.startswith("startpos moves"):
        return text
    if text.startswith("sfen "):
        return text
    return f"sfen {sfen_with_ply}"


def read_roots(path: str | None) -> list[RootPosition]:
    if path is None:
        return [RootPosition("startpos", SFEN_START_PLY1)]

    roots: list[RootPosition] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                sfen_with_ply = decode_position_string(line)
            except Exception as exc:
                raise ValueError(f"failed to parse root at {path}:{line_no}: {line}") from exc
            roots.append(RootPosition(normalize_position_command(line, sfen_with_ply), sfen_with_ply))

    if not roots:
        return [RootPosition("startpos", SFEN_START_PLY1)]
    return roots


def load_book(path: str) -> dict[str, list[BookMove]]:
    raw_book = read_yaneuraou_book(path, ignore_book_ply=True)
    book: dict[str, list[BookMove]] = {}

    for sfen, moves in raw_book.items():
        key = normalize_sfen_key(sfen)
        merged_moves = book.setdefault(key, [])
        for move in moves:
            insert_book_move(merged_moves, move)

    return book


def lookup_book(
    book: dict[str, list[BookMove]], sfen_key: str, *, flip_lookup: bool
) -> tuple[list[BookMove] | None, bool]:
    moves = book.get(sfen_key)
    if moves is not None:
        return moves, False

    if flip_lookup:
        flipped_key = flipped_sfen(sfen_key)
        moves = book.get(flipped_key)
        if moves is not None:
            return moves, True

    return None, False


def already_seen(seen: set[str], sfen_key: str, *, flip_lookup: bool) -> bool:
    if sfen_key in seen:
        return True
    return flip_lookup and flipped_sfen(sfen_key) in seen


def mark_seen(seen: set[str], sfen_key: str) -> None:
    seen.add(sfen_key)


def add_leaf(
    leafs: list[LeafOutput],
    leaf_seen: set[str],
    position_cmd: str,
    sfen_with_ply: str,
    *,
    flip_lookup: bool,
) -> bool:
    key = normalize_sfen_key(sfen_with_ply)
    if already_seen(leaf_seen, key, flip_lookup=flip_lookup):
        return False
    mark_seen(leaf_seen, key)
    leafs.append(LeafOutput(position_cmd, sfen_with_ply))
    return True


def enumerate_leafs(
    book: dict[str, list[BookMove]],
    roots: list[RootPosition],
    *,
    flip_lookup: bool,
    skip_illegal: bool,
    progress_interval: int,
) -> tuple[list[LeafOutput], dict[str, int]]:
    queue: deque[RootPosition] = deque(roots)
    visited: set[str] = set()
    leaf_seen: set[str] = set()
    leafs: list[LeafOutput] = []
    expanded = 0
    skipped_illegal = 0
    skipped_terminal = 0

    while queue:
        node = queue.popleft()
        sfen_key = normalize_sfen_key(node.sfen_with_ply)

        if already_seen(visited, sfen_key, flip_lookup=flip_lookup):
            continue
        mark_seen(visited, sfen_key)

        moves, flipped_hit = lookup_book(book, sfen_key, flip_lookup=flip_lookup)
        if moves is None:
            add_leaf(
                leafs,
                leaf_seen,
                node.position_cmd,
                node.sfen_with_ply,
                flip_lookup=flip_lookup,
            )
            continue

        expanded += 1
        traversed = 0

        for book_move in moves:
            if book_move.move in NON_BOARD_MOVES:
                skipped_terminal += 1
                continue

            move = flipped_move(book_move.move) if flipped_hit else book_move.move
            board = board_from_sfen(node.sfen_with_ply)
            try:
                checked_push_usi(board, move, context=node.position_cmd)
            except ValueError:
                if not skip_illegal:
                    raise
                skipped_illegal += 1
                continue

            traversed += 1
            next_sfen_with_ply = board.sfen()
            next_position_cmd = append_position_move(node.position_cmd, move)
            next_key = normalize_sfen_key(next_sfen_with_ply)
            next_moves, _ = lookup_book(book, next_key, flip_lookup=flip_lookup)

            if next_moves is None:
                add_leaf(
                    leafs,
                    leaf_seen,
                    next_position_cmd,
                    next_sfen_with_ply,
                    flip_lookup=flip_lookup,
                )
            elif not already_seen(visited, next_key, flip_lookup=flip_lookup):
                queue.append(RootPosition(next_position_cmd, next_sfen_with_ply))

        if traversed == 0:
            add_leaf(
                leafs,
                leaf_seen,
                node.position_cmd,
                node.sfen_with_ply,
                flip_lookup=flip_lookup,
            )

        if progress_interval > 0 and expanded % progress_interval == 0:
            print(
                f"expanded={expanded:,} queue={len(queue):,} "
                f"leafs={len(leafs):,} visited={len(visited):,}",
                flush=True,
            )

    stats = {
        "expanded": expanded,
        "visited": len(visited),
        "leafs": len(leafs),
        "skipped_illegal": skipped_illegal,
        "skipped_terminal": skipped_terminal,
    }
    return leafs, stats


def write_leafs(path: Path, leafs: list[LeafOutput], *, append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as f:
        for leaf in leafs:
            f.write(leaf.position_cmd)
            f.write("\n")


def write_leaf_sfens(path: Path, leafs: list[LeafOutput], *, append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="\n") as f:
        for leaf in leafs:
            f.write(leaf.sfen_with_ply)
            f.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate all leaf positions reachable from roots in a YaneuraOu "
            "book and write them as BookMiner think_sfens.txt lines."
        )
    )
    parser.add_argument("book", help="source YaneuraOu book DB (.db or .ybb)")
    parser.add_argument(
        "output",
        nargs="?",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"output think_sfens.txt path (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--roots",
        help=(
            "root position file. Each line is startpos/startpos moves..., "
            "sfen ... moves..., or raw SFEN. Default is startpos."
        ),
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="append to output instead of overwriting it",
    )
    parser.add_argument(
        "--no-flip-lookup",
        action="store_true",
        help="do not look up 180-degree flipped SFENs in the book",
    )
    parser.add_argument(
        "--skip-illegal",
        action="store_true",
        help="skip illegal book moves instead of stopping with an error",
    )
    parser.add_argument(
        "--sfen-output",
        help="also write leaf SFENs with ply to this file",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=10000,
        help="print progress every N expanded book positions (0 disables progress)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_path = Path(args.output)
    sfen_output_path = Path(args.sfen_output) if args.sfen_output else None
    flip_lookup = not args.no_flip_lookup

    print(f"book       : {args.book}")
    print(f"output     : {output_path}")
    print(f"roots      : {args.roots if args.roots else 'startpos'}")
    print(f"flip lookup: {'enabled' if flip_lookup else 'disabled'}")

    print("read book...")
    book = load_book(args.book)
    entries = sum(len(moves) for moves in book.values())
    print(f"book positions = {len(book):,}")
    print(f"book entries   = {entries:,}")

    roots = read_roots(args.roots)
    print(f"root positions = {len(roots):,}")

    leafs, stats = enumerate_leafs(
        book,
        roots,
        flip_lookup=flip_lookup,
        skip_illegal=args.skip_illegal,
        progress_interval=args.progress_interval,
    )

    write_leafs(output_path, leafs, append=args.append)
    if sfen_output_path is not None:
        write_leaf_sfens(sfen_output_path, leafs, append=args.append)

    print(f"expanded         = {stats['expanded']:,}")
    print(f"visited          = {stats['visited']:,}")
    print(f"leafs            = {stats['leafs']:,}")
    print(f"skipped terminal = {stats['skipped_terminal']:,}")
    print(f"skipped illegal  = {stats['skipped_illegal']:,}")
    print(f"wrote            = {output_path}")
    if sfen_output_path is not None:
        print(f"wrote sfens      = {sfen_output_path}")


if __name__ == "__main__":
    main()
